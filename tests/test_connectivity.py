"""
tests/test_connectivity.py
==========================

Two things that both exist because of failures nothing else could see.

1. COMPONENT COUNTING - the instrument that says whether the indexer built
   ONE graph or several. Every other number in the run summary counts work
   that was done, and all of them read healthy while a five-language
   repository comes out as five disconnected islands split along language
   lines. Only this number shows that.

2. CONSOLE ENCODING - the thing that stops a Windows console killing the
   process while it prints an answer that has already been paid for.

Both are tested without a database or an API key, because both are pure
functions with the awkward parts deliberately kept out of them.
"""

import io
import sys

from graph_rag.cli._console import configure_console
from graph_rag.pipeline.indexer import should_reuse_block
from graph_rag.storage.graph_store import _component_sizes


# ---------------------------------------------------------------------------
# Connected components
# ---------------------------------------------------------------------------

def test_an_empty_graph_has_no_components():
    assert _component_sizes({}) == []


def test_entities_with_no_edges_are_each_their_own_component():
    """
    An isolated entity is not "part of the graph with no neighbours" - it is
    its own island, and counting it as one is what makes the summary's
    `connected pieces` number honest on a repo of unlinked files.
    """
    adjacency = {"a": set(), "b": set(), "c": set()}
    assert sorted(_component_sizes(adjacency)) == [1, 1, 1]


def test_a_fully_linked_graph_is_one_component():
    adjacency = {
        "a": {"b"},
        "b": {"a", "c"},
        "c": {"b"},
    }
    assert _component_sizes(adjacency) == [3]


def test_two_islands_are_counted_separately():
    """
    THE SHAPE OF THE REAL FAILURE.

    This is the demo repository in miniature: a Python cluster and a Go
    cluster, each internally well connected, with nothing joining them. The
    edge counts look excellent and the graph is still in two pieces.
    """
    adjacency = {
        "py1": {"py2"}, "py2": {"py1", "py3"}, "py3": {"py2"},
        "go1": {"go2"}, "go2": {"go1"},
    }
    assert sorted(_component_sizes(adjacency)) == [2, 3]


def test_one_bridging_edge_merges_two_islands():
    """
    What a single working interface token is worth: the same graph as above,
    plus one shared route, is one connected structure.
    """
    adjacency = {
        "py1": {"py2"}, "py2": {"py1", "py3"}, "py3": {"py2", "go1"},
        "go1": {"go2", "py3"}, "go2": {"go1"},
    }
    assert _component_sizes(adjacency) == [5]


def test_a_long_chain_does_not_exhaust_the_stack():
    """
    A chain of functions each calling the next is ordinary in real code, and
    a recursive walk would hit Python's recursion limit somewhere around a
    thousand of them. The traversal is iterative for this reason.
    """
    size = 10_000
    adjacency: dict[str, set[str]] = {}
    for i in range(size):
        adjacency.setdefault(f"n{i}", set())
        if i:
            adjacency[f"n{i}"].add(f"n{i - 1}")
            adjacency[f"n{i - 1}"].add(f"n{i}")

    assert _component_sizes(adjacency) == [size]


def test_a_neighbour_missing_from_the_map_does_not_crash():
    """
    Adjacency is built from edges the database returned, and defensive code
    here is cheap: a dangling id must not take the whole run down after the
    indexing work has already been done.
    """
    assert _component_sizes({"a": {"ghost"}}) == [2]


# ---------------------------------------------------------------------------
# Console encoding
# ---------------------------------------------------------------------------

def test_configure_console_survives_a_stream_that_cannot_be_reconfigured():
    """
    Under pytest, stdout is a capture object with no `reconfigure` at all.
    Failing there would mean the fix could never be exercised by a test, and
    would break anyone piping the command into another program.
    """
    original_out, original_err = sys.stdout, sys.stderr
    try:
        sys.stdout = io.StringIO()
        sys.stderr = io.StringIO()
        configure_console()          # must not raise
    finally:
        sys.stdout, sys.stderr = original_out, original_err


def test_configure_console_makes_a_legacy_encoding_able_to_carry_an_answer():
    """
    THE CRASH THIS PREVENTS.

    A Windows console hands Python a cp1252 stdout, which has no byte for
    the arrows, em dashes and curly quotes a language model produces
    constantly. `print(answer)` then raises UnicodeEncodeError - after the
    retrieval has run and the OpenAI call has been billed, so the user pays
    for an answer they never see.

    A real TextIOWrapper over a cp1252 buffer reproduces that exactly.
    """
    answer = "changing apply_tax → final_price_cents — see ‘pricing’"

    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding="cp1252")

    # Before: the same call the CLI makes blows up.
    try:
        stream.write(answer)
        stream.flush()
        raise AssertionError("expected cp1252 to reject the arrow")
    except UnicodeEncodeError:
        pass

    original_out, original_err = sys.stdout, sys.stderr
    try:
        sys.stdout = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        sys.stderr = io.TextIOWrapper(io.BytesIO(), encoding="cp1252")
        configure_console()

        # After: it writes, and the characters survive intact.
        sys.stdout.write(answer)
        sys.stdout.flush()
        written = sys.stdout.buffer.getvalue().decode("utf-8")
        assert "→" in written
        assert "apply_tax" in written
    finally:
        sys.stdout, sys.stderr = original_out, original_err


# ---------------------------------------------------------------------------
# The decision that spends money
# ---------------------------------------------------------------------------

def test_an_unchanged_block_with_a_vector_is_reused():
    """
    The whole point of the block-level Merkle hash. Editing a comment at the
    top of a file shifts every line below it without changing one function's
    text, and paying to re-embed all of them is the cost this avoids.
    """
    assert should_reuse_block("abc123", "abc123", force=False) is True


def test_a_changed_block_is_re_embedded():
    assert should_reuse_block("abc123", "def456", force=False) is False


def test_a_block_with_no_stored_vector_is_embedded():
    """
    `None` means either "new node" or "node exists but has no vector". Both
    collapse to the same answer: there is nothing to reuse. Treating the
    second as reusable would leave the entity permanently unsearchable while
    the summary cheerfully reported it as reused.
    """
    assert should_reuse_block(None, "abc123", force=False) is False


def test_force_distrusts_a_matching_hash():
    """
    `--force` is the repair for a changed parser or a changed embedding
    model, where the stored vector is stale even though the source text is
    byte-for-byte identical. If a matching hash could still win here, the
    flag would silently not do the one job it exists for.
    """
    assert should_reuse_block("abc123", "abc123", force=True) is False
