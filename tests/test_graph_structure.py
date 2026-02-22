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

def candidate(entity_id, language):
    """Shorthand for one row as storage.resolve_names would return it."""
    return {'id': entity_id, 'language': language, 'filePath': entity_id.split('::')[0]}
PY_SOURCE = '"""Module docstring."""\nCONSTANT = 3\n\n\nclass Service:\n    """A service."""\n\n    def run(self, value):\n        """Do the thing."""\n        return helper(value)\n\n\ndef helper(value):\n    """A free function."""\n    return value * CONSTANT\n'

@pytest.fixture
def parsed_python(tmp_path):
    path = tmp_path / 'svc.py'
    path.write_text(PY_SOURCE, encoding='utf-8')
    return parse_file(path, 'svc.py')

def test_entity_kinds(parsed_python):
    """
    A function in a class is a method; a free function is a function; a
    module-level assignment is a constant; and the leftover top-level code
    becomes one "module" node named after the file.
    """
    kinds = {e.name: e.kind for e in parsed_python.entities}
    assert kinds == {'CONSTANT': 'constant', 'Service': 'class', 'run': 'method', 'helper': 'function', 'svc.py': 'module'}

def test_module_level_constants_are_their_own_entities(parsed_python):
    """
    A constant needs a NAME of its own to be findable.

    It used to exist only as a line of text inside the module node, so it had
    no name, nothing could link to it, and "what is CONSTANT set to?" could
    only ever match the whole-file blob. Giving it a node makes it
    retrievable, citable, and a valid target for a documentation MENTIONS
    edge.
    """
    constant = next((e for e in parsed_python.entities if e.kind == 'constant'))
    assert constant.id == 'svc.py::CONSTANT'
    assert constant.name == 'CONSTANT'
    assert 'CONSTANT = 3' in constant.code
    assert constant.start_line == 2

def test_module_entity_captures_leftover_top_level_code(parsed_python):
    """
    Whatever belongs to no other entity still needs a home, or it is in NO
    node at all and cannot be retrieved.

    Real entities are elided from it - they have their own nodes - and each
    elided run is labelled with the lines it stands for, so a citation into
    the module node can still be followed back to the file.
    """
    module = next((e for e in parsed_python.entities if e.kind == 'module'))
    assert module.id == 'svc.py::<module>'
    assert 'Module docstring.' in module.code
    assert 'return value * CONSTANT' not in module.code
    assert 'defined above' in module.code

def test_entity_ids_are_qualified(parsed_python):
    ids = {e.id for e in parsed_python.entities}
    assert 'svc.py::Service.run' in ids
    assert 'svc.py::helper' in ids

def test_docstrings_are_captured(parsed_python):
    """
    Docstrings say what code is FOR, which the code itself often does not,
    so losing them measurably degrades search.
    """
    docs = {e.name: e.docstring for e in parsed_python.entities}
    assert docs['run'] == 'Do the thing.'
    assert docs['helper'] == 'A free function.'

def test_line_numbers_are_1_based_and_within_the_file(parsed_python):
    """Line numbers are cited in answers, so they must match the editor."""
    total = len(PY_SOURCE.splitlines())
    for entity in parsed_python.entities:
        assert 1 <= entity.start_line <= entity.end_line <= total

def test_class_contains_its_method(parsed_python):
    edges = {(r.source_id, r.target_id, r.kind) for r in parsed_python.relationships}
    assert ('svc.py::Service', 'svc.py::Service.run', 'CONTAINS') in edges

def test_same_file_call_is_resolved_immediately(parsed_python):
    """`run` calls `helper`, and both are in this file, so no guessing needed."""
    edges = {(r.source_id, r.target_id, r.kind) for r in parsed_python.relationships}
    assert ('svc.py::Service.run', 'svc.py::helper', 'CALLS') in edges

def test_edges_only_reference_entities_from_this_file(parsed_python):
    """No edge may dangle off to an id that does not exist."""
    ids = {e.id for e in parsed_python.entities}
    for rel in parsed_python.relationships:
        assert rel.source_id in ids
        assert rel.target_id in ids
MD_SOURCE = '# Title\n\nIntro prose about the project.\n\n## Setup\n\nHow to set it up, calling `helper` along the way.\n\n### Install\n\nInstallation detail.\n\n## Usage\n\nHow to use it.\n'

@pytest.fixture
def parsed_markdown(tmp_path):
    path = tmp_path / 'README.md'
    path.write_text(MD_SOURCE, encoding='utf-8')
    return parse_document(path, 'README.md')

def test_sections_do_not_swallow_their_subsections(parsed_markdown):
    """
    The top section must NOT span the whole file.

    tree-sitter nests sections, so a level-1 section contains everything
    below it. Embedding that would store the entire document as one diluted
    chunk AND duplicate every subsection inside it.
    """
    total_lines = len(MD_SOURCE.splitlines())
    title = next((c for c in parsed_markdown.chunks if c.title == 'Title'))
    assert title.end_line < total_lines

def test_chunks_do_not_overlap(parsed_markdown):
    """Every line belongs to at most one chunk."""
    seen = set()
    for chunk in parsed_markdown.chunks:
        lines = set(range(chunk.start_line, chunk.end_line + 1))
        assert not lines & seen, f'{chunk.id} overlaps an earlier chunk'
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
    install = next((c for c in parsed_markdown.chunks if c.title == 'Install'))
    assert install.heading_path == 'Title > Setup > Install'

def test_whole_file_chunk_for_a_config(tmp_path):
    """A config file has no headings, so it is stored as one chunk."""
    path = tmp_path / 'config.yaml'
    path.write_text('service:\n  port: 8000\n  debug: true\n', encoding='utf-8')
    parsed = parse_document(path, 'config.yaml')
    assert len(parsed.chunks) == 1
    assert parsed.chunks[0].level == 0
    assert parsed.chunks[0].start_line == 1
    assert parsed.chunks[0].end_line == 3
