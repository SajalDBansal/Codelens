"""
parsing/tree_utils.py
=====================

Small helpers shared by code_parser.py and doc_parser.py.

They live here rather than in either parser because both need them and
neither should import the other - a code parser and a document parser are
peers, not layers.
"""

import hashlib


def node_text(node, source: bytes) -> str:
    """
    The source text of one tree-sitter node.

    tree-sitter works in BYTES and gives us byte offsets, so we slice the raw
    bytes and then decode. `errors="replace"` keeps a file with an odd
    encoding from crashing the whole indexing run.
    """
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def hash_text(text: str) -> str:
    """
    sha256 of a piece of text, as a hex string.

    This is the "block hash" of the Merkle design. Same text in -> same hash
    out, always; different text in -> different hash out. That is the whole
    trick that lets unchanged code skip re-embedding.
    """
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def inclusive_end_line(node) -> int:
    """
    The 1-based number of the LAST line this node actually covers.

    WHY THIS IS NOT JUST `end_point[0] + 1`
    ---------------------------------------
    tree-sitter's `end_point` is EXCLUSIVE - it marks the position just past
    the node. For a node that ends at the end of a line, that position is
    (start of the next line), i.e. column 0 of the following row.

    So `end_point[0] + 1` is right only when the node ends part-way through a
    line. When it ends at column 0, adding 1 claims a line the node does not
    contain. Two things went wrong because of it:

      - a markdown section reported the next section's heading as its own
        last line, so consecutive chunks overlapped by one line;
      - the final section of a file reported an end_line past the end of the
        file, which would produce a citation pointing at a line that does
        not exist.

    Both are quiet: nothing raises, the numbers are just slightly wrong, and
    they surface later as a confusing citation.
    """
    row, column = node.end_point

    if column == 0:
        # Ends at the start of a row, so the last line it covers is the one
        # before. `row` is 0-based, so the 1-based previous line is `row`.
        return row

    # Ends part-way through `row` (0-based), so that row IS the last line.
    return row + 1
