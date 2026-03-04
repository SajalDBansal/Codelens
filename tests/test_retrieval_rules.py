"""
tests/test_retrieval_rules.py
=============================

The judgement calls that decide what ends up in the prompt.

Everything tested here is pure logic with no database and no API behind it,
which is deliberate: these are the rules most likely to be quietly wrong, and
quick deterministic checks make these rules practical to re-check often.

Each test names the failure it exists to prevent.
"""

from graph_rag import config
from graph_rag.models import CodeEntity, RetrievedEntity
from graph_rag.parsing.doc_parser import _split_large_text
from graph_rag.pipeline.indexer import resolve_mention, resolve_reference
from graph_rag.retrieval.retriever import _format_entity, _truncate_code
from graph_rag.storage.graph_store import reciprocal_rank_fusion

# ---------------------------------------------------------------------------
# Document chunking: the size bound is absolute
# ---------------------------------------------------------------------------

def test_a_file_with_no_blank_lines_still_splits():
    """
    The splitter used to start a new chunk ONLY when standing on a blank
    line, so a file without any never split at all. A 68 KB yaml file of
    `key: value` lines came back as one 69,790-character chunk, which the
    embedder then truncated to 8,000 - 88% of the file silently discarded
    while every report said it was fully indexed.
    """
    text = "\n".join(f"  key_{i}: value_{i}" for i in range(3000))
    chunks = _split_large_text(text, start_line=1, max_chars=4000)

    assert len(chunks) > 1
    assert all(len(body) <= 4000 for body, _, _ in chunks)


def test_splitting_loses_no_content():
    """A split must rearrange the text, never drop any of it."""
    text = "\n".join(f"line {i} with some content" for i in range(500))
    chunks = _split_large_text(text, start_line=1, max_chars=1000)

    rejoined = "\n".join(body for body, _, _ in chunks)
    assert rejoined.split() == text.split()


def test_a_single_enormous_line_is_cut_up():
    """
    Minified json and one-line csv exports are a single line longer than the
    whole budget. No break point will ever help, so it has to be cut.
    """
    text = "x" * 30000
    chunks = _split_large_text(text, start_line=1, max_chars=4000)

    assert all(len(body) <= 4000 for body, _, _ in chunks)
    assert sum(len(body) for body, _, _ in chunks) == 30000


def test_line_numbers_stay_in_order():
    text = "\n".join(f"line {i}" for i in range(2000))
    chunks = _split_large_text(text, start_line=1, max_chars=500)

    for body, start, end in chunks:
        assert 1 <= start <= end


def test_a_blank_line_is_not_a_break_point_when_it_is_too_early():
    """
    The blank line straight after a heading used to become the break point
    for the whole section, producing a useless 10-byte first chunk holding
    nothing but "# Title" - which we would then pay to embed.
    """
    text = "# Title\n\n" + ("prose that goes on and on. " * 400)
    chunks = _split_large_text(text, start_line=1, max_chars=2000)

    assert len(chunks[0][0]) > 100, "first chunk is a stub, not real content"


# ---------------------------------------------------------------------------
# Call resolution
# ---------------------------------------------------------------------------

def test_a_call_never_resolves_to_a_constant():
    """
    Once constants became entities, `some_dict.items()` reduced to the bare
    name `items` and matched a dataclass field called `items`, producing a
    CALLS edge pointing at a variable. Calls land on callable things.
    """
    candidates = [
        {"id": "retriever.py::items", "language": "python",
         "filePath": "retriever.py", "kind": "constant"},
    ]
    target, reason = resolve_reference(candidates, "indexer.py::run", "python")

    assert target is None
    assert reason == "external"


def test_a_call_may_resolve_to_a_class():
    """Calling a class is how most languages spell "construct one"."""
    candidates = [
        {"id": "models.py::Relationship", "language": "python",
         "filePath": "models.py", "kind": "class"},
    ]
    target, reason = resolve_reference(candidates, "indexer.py::run", "python")

    assert reason == "linked"
    assert target == "models.py::Relationship"


def test_a_name_only_found_in_another_language_is_external_not_ambiguous():
    """
    A Python file calling `login` when the only `login` in the graph is
    TypeScript is calling something OUTSIDE the graph. Reporting that as
    "ambiguous" inflated the headline "real missed edges" figure with cases
    that were nothing of the kind.
    """
    candidates = [
        {"id": "api.ts::login", "language": "typescript",
         "filePath": "api.ts", "kind": "function"},
    ]
    target, reason = resolve_reference(candidates, "app.py::verify", "python")

    assert target is None
    assert reason == "external"


# ---------------------------------------------------------------------------
# Documentation mentions
# ---------------------------------------------------------------------------

def _candidate(entity_id, path, kind="function"):
    return {"id": entity_id, "language": "python", "filePath": path, "kind": kind}


def test_mentions_link_even_when_the_name_is_not_unique():
    """
    THE REGRESSION THIS FILE EXISTS FOR.

    Mentions used to require a unique match, exactly like calls. On any
    repository with more than one language that test never passed - `login`
    matched 12 entities, `AuthService` 13 - so the feature produced ZERO
    edges and documentation stayed completely disconnected from the code.
    """
    candidates = [
        _candidate("src/auth/app.py::login", "src/auth/app.py"),
        _candidate("src/api.ts::login", "src/api.ts"),
        _candidate("build/init.lua::login", "build/init.lua"),
    ]
    linked = resolve_mention(candidates, "src/auth/README.md", max_targets=3)

    assert linked, "a name shared across languages must still link somewhere"
    # The one sitting next to the document wins.
    assert linked[0] == "src/auth/app.py::login"


def test_mentions_prefer_the_nearest_code_in_the_tree():
    candidates = [
        _candidate("far/away/thing.py::helper", "far/away/thing.py"),
        _candidate("docs/api/impl.py::helper", "docs/api/impl.py"),
    ]
    linked = resolve_mention(candidates, "docs/api/README.md", max_targets=1)

    assert linked == ["docs/api/impl.py::helper"]


def test_mentions_never_point_at_a_module_node():
    """
    A doc writing `login` means the function, not the whole file that happens
    to contain it - a module node is a grab-bag and makes a poor target.
    """
    candidates = [
        _candidate("src/app.py::<module>", "src/app.py", kind="module"),
        _candidate("src/app.py::login", "src/app.py"),
    ]
    linked = resolve_mention(candidates, "README.md", max_targets=3)

    assert linked == ["src/app.py::login"]


def test_mentions_are_capped():
    candidates = [_candidate(f"f{i}.py::run", f"f{i}.py") for i in range(20)]
    linked = resolve_mention(candidates, "README.md", max_targets=3)

    assert len(linked) == 3


# ---------------------------------------------------------------------------
# Hybrid search plumbing
# ---------------------------------------------------------------------------


def test_fusion_rewards_agreement_between_the_two_searches():
    """
    An item both searches liked must beat one only a single search liked,
    whatever the raw scores said - that is the entire point of fusing on
    rank rather than trying to normalise incomparable scores.
    """
    vector = ["only_vector", "both"]
    keyword = ["only_keyword", "both"]

    scores = reciprocal_rank_fusion([vector, keyword])

    assert scores["both"] > scores["only_vector"]
    assert scores["both"] > scores["only_keyword"]


def test_fusion_weights_shift_the_balance():
    scores = reciprocal_rank_fusion(
        [["a"], ["b"]], weights=[3.0, 1.0]
    )
    assert scores["a"] > scores["b"]


# ---------------------------------------------------------------------------
# The context budget
# ---------------------------------------------------------------------------

def _entity(entity_id, code):
    return CodeEntity(
        id=entity_id, kind="function", name=entity_id.split("::")[-1],
        file_path=entity_id.split("::")[0], language="python",
        start_line=1, end_line=2, code=code, content_hash="h",
    )


def test_one_oversized_block_does_not_discard_everything_behind_it():
    """
    The budget loop used to `break`. A single 50 KB entity ranked first
    therefore returned a COMPLETELY EMPTY context, and the model was asked
    the question with nothing to answer from.
    """
    huge = RetrievedEntity(entity=_entity("a.py::huge", "x" * 50000), hop=0, score=0.9)
    small = [
        RetrievedEntity(entity=_entity(f"b{i}.py::small", "def f(): pass"), hop=0, score=0.8)
        for i in range(5)
    ]

    # Deliberately tighter than one capped block, so the oversized entity
    # genuinely cannot fit and the skip-versus-break behaviour is what decides
    # whether anything survives.
    max_chars = 2000
    blocks, used, dropped = [], 0, 0
    for item in [huge, *small]:
        block = _format_entity(item, len(blocks) + 1)
        if used + len(block) > max_chars:
            dropped += 1
            continue
        blocks.append(block)
        used += len(block)

    assert len(blocks) == 5, "the small blocks must survive the oversized one"
    assert dropped == 1


def test_a_single_block_is_capped():
    """
    `code` is stored untruncated, so without a per-block cap one generated
    file could consume the entire context budget by itself.
    """
    capped = _truncate_code("y" * (config.MAX_BLOCK_CHARS * 3))

    assert len(capped) < config.MAX_BLOCK_CHARS + 100
    assert capped.endswith("(block truncated)")


def test_a_short_block_is_untouched():
    assert _truncate_code("def f(): pass") == "def f(): pass"
