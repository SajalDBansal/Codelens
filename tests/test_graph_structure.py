"""
tests/test_graph_structure.py
=============================

Guards the shape of the graph: that entities come out with the right kinds
and line numbers, and that edges connect the things they claim to.

The most important tests here are the ones around `resolve_reference`. That
one function decides which definition a call by NAME actually points at, and
it is the single most consequential piece of judgement in the indexer:

  - too strict, and real calls go unlinked, leaving the graph in disconnected
    islands that graph traversal cannot cross;
  - too loose, and it invents edges between unrelated code, which silently
    sends retrieval off to the wrong file and poisons answers.

Both failure modes are quiet. Neither raises an error. Hence the tests.
"""

import pytest

from graph_rag.parsing import language_family, parse_document, parse_file
from graph_rag.pipeline import resolve_reference


# ---------------------------------------------------------------------------
# resolve_reference - the name resolution rule
# ---------------------------------------------------------------------------

def candidate(entity_id, language):
    """Shorthand for one row as storage.resolve_names would return it."""
    return {"id": entity_id, "language": language, "filePath": entity_id.split("::")[0]}


def test_no_candidates_is_external_not_a_failure():
    """
    A call to `len` or `fetch` finds nothing, and that is correct.

    These must be counted separately from real misses, or the "unresolved"
    number is dominated by healthy library calls and tells you nothing.
    """
    target, reason = resolve_reference([], "a.py::f", "python")
    assert target is None
    assert reason == "external"


def test_single_candidate_in_same_language_links():
    candidates = [candidate("api/security.py::verify_password", "python")]
    target, reason = resolve_reference(candidates, "api/services.py::login", "python")
    assert reason == "linked"
    assert target == "api/security.py::verify_password"


def test_cross_language_name_collision_does_not_link():
    """
    A Python `login` and a TypeScript `login` are different functions.

    This was a real bug: both collided on the bare name, both were marked
    ambiguous, and NEITHER was linked - so the TypeScript one sat isolated
    in the graph with no edges at all.
    """
    candidates = [
        candidate("api/services.py::AuthService.login", "python"),
        candidate("web/src/api.ts::login", "typescript"),
    ]

    # The Python caller gets the Python one...
    target, reason = resolve_reference(candidates, "api/main.py::handle_login", "python")
    assert reason == "linked"
    assert target == "api/services.py::AuthService.login"

    # ...and the TypeScript caller gets the TypeScript one.
    target, reason = resolve_reference(candidates, "web/src/cart.ts::Cart.submit", "typescript")
    assert reason == "linked"
    assert target == "web/src/api.ts::login"


def test_tsx_can_call_into_ts():
    """
    .ts, .tsx and .js are three grammars but ONE module system.

    A .tsx component importing a function from a .ts file is completely
    ordinary. Comparing raw language names instead of families would refuse
    to link it - which is exactly what happened before LANGUAGE_FAMILIES.
    """
    candidates = [candidate("web/src/api.ts::login", "typescript")]
    target, reason = resolve_reference(
        candidates, "web/src/LoginForm.tsx::LoginForm.handleSubmit", "tsx"
    )
    assert reason == "linked"
    assert target == "web/src/api.ts::login"


def test_ambiguous_within_one_language_links_nothing():
    """
    Three classes with a `save` method: we do not know which is meant.

    Creating no edge is the right answer. A wrong edge would quietly send
    retrieval to unrelated code, which is worse than a little less context.
    """
    candidates = [
        candidate("a.py::UserRepo.save", "python"),
        candidate("b.py::OrderRepo.save", "python"),
        candidate("c.py::CartRepo.save", "python"),
    ]
    target, reason = resolve_reference(candidates, "svc.py::do", "python")
    assert target is None
    assert reason == "ambiguous"


def test_never_links_an_entity_to_itself():
    """A recursive function must not get a CALLS edge pointing at itself."""
    candidates = [candidate("a.py::walk", "python")]
    target, reason = resolve_reference(candidates, "a.py::walk", "python")
    assert target is None


@pytest.mark.parametrize(
    "language,expected",
    [
        ("typescript", "js"), ("tsx", "js"), ("javascript", "js"),
        ("python", "python"), ("go", "go"), ("rust", "rust"),
        # Java, Kotlin and Scala share the JVM and call into each other freely.
        ("java", "jvm"), ("kotlin", "jvm"), ("scala", "jvm"),
        # C and C++ share headers and link together.
        ("c", "c"), ("cpp", "c"),
        ("cobol", "cobol"),   # unknown languages resolve against themselves
        (None, None),
    ],
)
def test_language_families(language, expected):
    assert language_family(language) == expected


# ---------------------------------------------------------------------------
# Parser output shape
# ---------------------------------------------------------------------------

PY_SOURCE = '''"""Module docstring."""
CONSTANT = 3


class Service:
    """A service."""

    def run(self, value):
        """Do the thing."""
        return helper(value)


def helper(value):
    """A free function."""
    return value * CONSTANT
'''


@pytest.fixture
def parsed_python(tmp_path):
    path = tmp_path / "svc.py"
    path.write_text(PY_SOURCE, encoding="utf-8")
    return parse_file(path, "svc.py")


def test_entity_kinds(parsed_python):
    """
    A function in a class is a method; a free function is a function; a
    module-level assignment is a constant; and the leftover top-level code
    becomes one "module" node named after the file.
    """
    kinds = {e.name: e.kind for e in parsed_python.entities}
    assert kinds == {
        "CONSTANT": "constant",
        "Service": "class",
        "run": "method",
        "helper": "function",
        "svc.py": "module",
    }


def test_module_level_constants_are_their_own_entities(parsed_python):
    """
    A constant needs a NAME of its own to be findable.

    It used to exist only as a line of text inside the module node, so it had
    no name, nothing could link to it, and "what is CONSTANT set to?" could
    only ever match the whole-file blob. Giving it a node makes it
    retrievable, citable, and a valid target for a documentation MENTIONS
    edge.
    """
    constant = next(e for e in parsed_python.entities if e.kind == "constant")
    assert constant.id == "svc.py::CONSTANT"
    assert constant.name == "CONSTANT"
    assert "CONSTANT = 3" in constant.code
    assert constant.start_line == 2


def test_module_entity_captures_leftover_top_level_code(parsed_python):
    """
    Whatever belongs to no other entity still needs a home, or it is in NO
    node at all and cannot be retrieved.

    Real entities are elided from it - they have their own nodes - and each
    elided run is labelled with the lines it stands for, so a citation into
    the module node can still be followed back to the file.
    """
    module = next(e for e in parsed_python.entities if e.kind == "module")
    assert module.id == "svc.py::<module>"
    assert "Module docstring." in module.code
    # The bodies of real entities are elided - they have their own nodes.
    assert "return value * CONSTANT" not in module.code
    assert "defined above" in module.code


def test_entity_ids_are_qualified(parsed_python):
    ids = {e.id for e in parsed_python.entities}
    assert "svc.py::Service.run" in ids
    assert "svc.py::helper" in ids


def test_docstrings_are_captured(parsed_python):
    """
    Docstrings say what code is FOR, which the code itself often does not,
    so losing them measurably degrades search.
    """
    docs = {e.name: e.docstring for e in parsed_python.entities}
    assert docs["run"] == "Do the thing."
    assert docs["helper"] == "A free function."


def test_line_numbers_are_1_based_and_within_the_file(parsed_python):
    """Line numbers are cited in answers, so they must match the editor."""
    total = len(PY_SOURCE.splitlines())
    for entity in parsed_python.entities:
        assert 1 <= entity.start_line <= entity.end_line <= total


def test_class_contains_its_method(parsed_python):
    edges = {(r.source_id, r.target_id, r.kind) for r in parsed_python.relationships}
    assert ("svc.py::Service", "svc.py::Service.run", "CONTAINS") in edges


def test_same_file_call_is_resolved_immediately(parsed_python):
    """`run` calls `helper`, and both are in this file, so no guessing needed."""
    edges = {(r.source_id, r.target_id, r.kind) for r in parsed_python.relationships}
    assert ("svc.py::Service.run", "svc.py::helper", "CALLS") in edges


def test_edges_only_reference_entities_from_this_file(parsed_python):
    """No edge may dangle off to an id that does not exist."""
    ids = {e.id for e in parsed_python.entities}
    for rel in parsed_python.relationships:
        assert rel.source_id in ids
        assert rel.target_id in ids


# ---------------------------------------------------------------------------
# Document chunking
# ---------------------------------------------------------------------------

MD_SOURCE = """# Title

Intro prose about the project.

## Setup

How to set it up, calling `helper` along the way.

### Install

Installation detail.

## Usage

How to use it.
"""


@pytest.fixture
def parsed_markdown(tmp_path):
    path = tmp_path / "README.md"
    path.write_text(MD_SOURCE, encoding="utf-8")
    return parse_document(path, "README.md")


def test_sections_do_not_swallow_their_subsections(parsed_markdown):
    """
    The top section must NOT span the whole file.

    tree-sitter nests sections, so a level-1 section contains everything
    below it. Embedding that would store the entire document as one diluted
    chunk AND duplicate every subsection inside it.
    """
    total_lines = len(MD_SOURCE.splitlines())
    title = next(c for c in parsed_markdown.chunks if c.title == "Title")
    assert title.end_line < total_lines


def test_chunks_do_not_overlap(parsed_markdown):
    """Every line belongs to at most one chunk."""
    seen = set()
    for chunk in parsed_markdown.chunks:
        lines = set(range(chunk.start_line, chunk.end_line + 1))
        assert not (lines & seen), f"{chunk.id} overlaps an earlier chunk"
        seen |= lines


def test_chunk_line_numbers_stay_inside_the_file(parsed_markdown):
    """An end_line past the end of the file would produce a broken citation."""
    total_lines = len(MD_SOURCE.splitlines())
    for chunk in parsed_markdown.chunks:
        assert 1 <= chunk.start_line <= chunk.end_line <= total_lines


def test_heading_path_carries_the_trail(parsed_markdown):
    """
    "Install" alone is meaningless; "Title > Setup > Install" is not.

    The trail goes into the embedded text, so it is what makes one project's
    Install section distinguishable from every other project's.
    """
    install = next(c for c in parsed_markdown.chunks if c.title == "Install")
    assert install.heading_path == "Title > Setup > Install"


def test_backtick_names_become_mentions(parsed_markdown):
    """Only `backticked` names count - not every English word in the prose."""
    names = {name for _, name in parsed_markdown.mentions}
    assert "helper" in names
    assert "prose" not in names


def test_whole_file_chunk_for_a_config(tmp_path):
    """A config file has no headings, so it is stored as one chunk."""
    path = tmp_path / "config.yaml"
    path.write_text("service:\n  port: 8000\n  debug: true\n", encoding="utf-8")

    parsed = parse_document(path, "config.yaml")
    assert len(parsed.chunks) == 1
    assert parsed.chunks[0].level == 0          # 0 means "whole file"
    assert parsed.chunks[0].start_line == 1
    assert parsed.chunks[0].end_line == 3       # not 4 - the file has 3 lines
