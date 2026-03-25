"""
parsing/doc_parser.py
=====================

The documentation equivalent of code_parser.py.

THE CENTRAL QUESTION: WHAT IS THE UNIT WORTH EMBEDDING?
-------------------------------------------------------
For code the answer is obvious - a function. For a README it is NOT "1000
characters", which is what ordinary RAG does and which happily cuts a
sentence, or a code example, straight down the middle.

The right unit is a SECTION: a heading plus everything underneath it, up to
the next heading of the same or higher level. A section is a unit the author
already decided belongs together.

    # Setup                  <- section, lines 1-20
    ## Install               <-   nested section, lines 5-12
    ## Configure             <-   nested section, lines 13-20

And here is the neat part: tree-sitter's markdown grammar produces exactly
that shape, with `section` nodes NESTED by heading depth. So this file walks
the tree the same way code_parser.py does, and produces the same kind of
CONTAINS edges - a section contains a subsection just as a class contains a
method.

FILES WITH NO HEADINGS
----------------------
A yaml or json config has no headings to split on. Those are stored as ONE
chunk covering the whole file, which is usually correct - a package.json is
meaningfully a single thing. Only if such a file is very large do we fall
back to splitting it on blank lines.
"""

import re
from pathlib import Path
from typing import Optional

from .. import config
from ..models import DocChunk, ParsedDocument, Relationship
from .interfaces import extract_interface_tokens
from .languages import classify, get_cached_parser
from .tree_utils import hash_text as _hash_text
from .tree_utils import inclusive_end_line
from .tree_utils import node_text as _text


# Matches a name written in `backticks` - how people refer to code in prose.
#
# We use backtick spans and NOT every word in the document on purpose. If we
# linked on any matching word, a README containing the ordinary English word
# "index" would link itself to every function called `index`. Backticks are
# the author explicitly saying "this is a code identifier".
_MENTION_RE = re.compile(r"`([A-Za-z_][A-Za-z0-9_.]*)`")


def _extract_mentions(text: str) -> list[str]:
    """
    Find the code identifiers a piece of prose refers to.

    `GraphStore.save_entities` yields "save_entities" - we keep the last
    segment, because that is what matches a CodeEntity's `name` property.
    """
    names = []
    for raw in _MENTION_RE.findall(text):
        name = raw.rsplit(".", 1)[-1]
        # One- and two-character names are far too generic to link safely.
        if len(name) > 2:
            names.append(name)
    return names


def _heading_text(section_node, source: bytes) -> tuple[Optional[str], int]:
    """
    Get a section's heading text and its depth.

    Returns (title, level), e.g. ("Setup", 2) for `## Setup`.
    Returns (None, 0) if this section has no heading node - which happens for
    the preamble text before the first heading in a file.
    """
    for child in section_node.named_children:
        if child.type in ("atx_heading", "setext_heading"):
            raw = _text(child, source).strip()

            # Depth = the number of leading '#' characters. The grammar also
            # tells us via the marker node type (atx_h2_marker), but counting
            # is simpler and works for setext headings too.
            level = len(raw) - len(raw.lstrip("#"))

            title = raw.lstrip("#").strip()
            # Setext headings are underlined with === or ---, not prefixed.
            if level == 0:
                title = raw.split("\n")[0].strip()
                level = 1

            return (title or None, level or 1)

    return (None, 0)


def _split_large_text(text: str, start_line: int, max_chars: int) -> list[tuple[str, int, int]]:
    """
    Split a heading-less file (yaml, json, csv, sql, plain text) into chunks.

    Returns a list of (text, start_line, end_line).

    THE RULE: NO CHUNK MAY EXCEED max_chars. EVER.
    ----------------------------------------------
    We still PREFER to break at a blank line, so a chunk does not cut through
    the middle of a record. But preferring is all it is - the size bound is
    absolute.

    That distinction was the whole bug in the previous version. It only ever
    started a new chunk when it happened to be standing on a blank line, so a
    file with no blank lines in it never split at all. A 68 KB yaml file of
    `key: value` lines came back as ONE 69,790-character chunk, which the
    embedder then silently truncated to 8,000 - 88% of the file was thrown
    away, and every report said it had been indexed in full.

    Three ways a chunk ends, in order of preference:
      1. at the last blank line we passed, if there was one;
      2. at the last line that fit, if there was not;
      3. mid-line, for a single line longer than the whole budget (minified
         json, a one-line csv export).
    """
    if len(text) <= max_chars:
        # `splitlines()` rather than counting "\n": a file ending in a
        # newline has the same number of LINES as one that does not, but one
        # more newline character. Counting newlines therefore reports an
        # end_line one past the end of the file - which would make a citation
        # point at a line that does not exist.
        line_count = max(1, len(text.splitlines()))
        return [(text, start_line, start_line + line_count - 1)]

    chunks: list[tuple[str, int, int]] = []

    # Each entry is (line_text, 1-based line number in the original file).
    pending: list[tuple[str, int]] = []
    pending_len = 0
    # Index into `pending` of the most recent blank line - our preferred
    # break point, if we reach the size limit before finding a better one.
    last_blank: int | None = None

    def emit(lines: list[tuple[str, int]]) -> None:
        if not lines:
            return
        body = "\n".join(line for line, _ in lines)
        if not body.strip():
            return
        chunks.append((body, lines[0][1], lines[-1][1]))

    for number, line in enumerate(text.split("\n"), start=start_line):
        # A single line longer than the whole budget: no break point will
        # ever help, so cut it into fixed-size pieces. They all report the
        # same line number, which is correct - they really are all one line.
        if len(line) > max_chars:
            # Whatever little we had accumulated rides along with the first
            # piece rather than becoming a chunk of its own. A markdown
            # section is "# Title", a blank line, then one very long
            # paragraph - and emitting the heading alone produced an 8-byte
            # chunk that said nothing and still cost an embedding.
            prefix = ""
            first_line = number
            if pending and pending_len < max_chars // 10:
                prefix = "\n".join(item for item, _ in pending) + "\n"
                first_line = pending[0][1]
            else:
                emit(pending)
            pending, pending_len, last_blank = [], 0, None

            budget = max_chars - len(prefix)
            for offset in range(0, len(line), budget):
                piece = line[offset:offset + budget]
                if offset == 0 and prefix:
                    chunks.append((prefix + piece, first_line, number))
                else:
                    chunks.append((piece, number, number))
            continue

        # Adding this line would break the bound, so close the current chunk.
        if pending and pending_len + len(line) + 1 > max_chars:
            if last_blank is not None and last_blank > 0:
                # Break at the blank line and carry the remainder forward, so
                # the record that started after it stays whole.
                emit(pending[:last_blank])
                pending = pending[last_blank + 1:]
            else:
                emit(pending)
                pending = []
            pending_len = sum(len(item) + 1 for item, _ in pending)
            last_blank = None

        if not line.strip():
            # Only remember a blank line as a break point once enough text has
            # accumulated to be worth a chunk of its own. Without this, the
            # blank line after a heading became the break point for the whole
            # section, so splitting a long section produced a useless 10-byte
            # first chunk holding nothing but "# Title" - and we would pay to
            # embed it.
            if pending_len >= max_chars // 10:
                last_blank = len(pending)
        pending.append((line, number))
        pending_len += len(line) + 1

    emit(pending)
    return chunks


def _parse_markdown(source: bytes, relative_path: str, doc_type: str) -> ParsedDocument:
    """
    Split a markdown file into nested section chunks using tree-sitter.

    Mirrors the shape of code_parser._walk_tree: walk the tree, emit a chunk
    for each `section` node, and add a CONTAINS edge from parent to child.
    """
    parser = get_cached_parser("markdown")
    tree = parser.parse(source)

    chunks: list[DocChunk] = []
    relationships: list[Relationship] = []
    mentions: list[tuple[str, str]] = []

    def visit(node, heading_trail: list[str], parent_id: Optional[str]) -> None:
        if node.type != "section":
            # Not a section - keep descending (the top-level `document` node).
            for child in node.named_children:
                visit(child, heading_trail, parent_id)
            return

        title, level = _heading_text(node, source)

        # --- THE IMPORTANT BIT: a section's chunk is its OWN body only ------
        #
        # tree-sitter nests sections, so a level-1 section literally contains
        # the whole rest of the file. If we embedded that we would get one
        # chunk holding the entire README (diluted into meaninglessness, and
        # truncated), duplicating every subsection underneath it.
        #
        # So we cut the text at the first nested subsection. The parent keeps
        # its heading plus its introductory prose; each child owns its own.
        # The parent/child relationship is not lost - it is carried by the
        # CONTAINS edge and by the heading trail in the id.
        first_subsection = next(
            (child for child in node.named_children if child.type == "section"),
            None,
        )
        own_end_byte = first_subsection.start_byte if first_subsection else node.end_byte
        own_end_line = (
            # A subsection starts on 0-based row N, so this section's own last
            # line is the 1-based line N - which is the same number.
            first_subsection.start_point[0]
            if first_subsection
            # No subsection: this section runs to its own end. `end_point` is
            # exclusive, so a plain +1 would claim the next line.
            else inclusive_end_line(node)
        )
        text = source[node.start_byte:own_end_byte].decode("utf-8", errors="replace")

        # A section with no heading is the preamble before the first heading.
        # Give it the file name so it still has a usable title.
        display_title = title or Path(relative_path).name
        trail = heading_trail + [display_title]

        # "README.md::Setup > Install"
        chunk_id = f"{relative_path}::{' > '.join(trail)}"

        # Skip sections that are only a heading with no real content of their
        # own - a pure container like "## Setup" followed straight away by
        # "### Install". It would cost an embedding to say nothing. Its
        # children are still visited, so no content is lost.
        body_without_heading = text
        if title:
            body_without_heading = text.replace(f"{'#' * level} {title}", "", 1)

        # Skip ONLY a section with no body of its own at all - a pure
        # container like "## Setup" followed immediately by "### Install".
        #
        # This test used to require more than 20 characters, which quietly
        # deleted genuinely short sections: a "## Port" heading over a single
        # line saying "8080" is real content someone may well ask about, and
        # it was vanishing from the graph without a trace.
        has_content = bool(body_without_heading.strip())

        chunk_id_for_children = parent_id
        if has_content:
            # A long section is SPLIT, not truncated.
            #
            # This used to store `text[:MAX_EMBED_CHARS]`, so a 31 KB section
            # was stored as 8 KB and the other 23 KB simply ceased to exist -
            # no warning, and the graph reported the file as fully indexed.
            # Splitting keeps every word reachable, and several focused
            # vectors retrieve better than one diluted one anyway.
            pieces = _split_large_text(
                text,
                start_line=node.start_point[0] + 1,
                max_chars=config.MAX_DOC_CHUNK_CHARS,
            )

            for part_number, (piece, piece_start, piece_end) in enumerate(pieces, start=1):
                part_id = chunk_id if len(pieces) == 1 else f"{chunk_id} (part {part_number})"
                chunks.append(
                    DocChunk(
                        id=part_id,
                        file_path=relative_path,
                        doc_type=doc_type,
                        title=display_title,
                        heading_path=" > ".join(trail),
                        level=level,
                        start_line=piece_start,
                        # A single-piece section keeps the end line the walk
                        # worked out, which accounts for nested subsections.
                        end_line=own_end_line if len(pieces) == 1 else piece_end,
                        text=piece,
                        content_hash=_hash_text(piece),
                    )
                )

                if parent_id is not None:
                    relationships.append(
                        Relationship(source_id=parent_id, target_id=part_id, kind="CONTAINS")
                    )

                for name in _extract_mentions(piece):
                    mentions.append((part_id, name))

            # Subsections hang off the FIRST part, which carries the heading.
            chunk_id_for_children = chunk_id if len(pieces) == 1 else f"{chunk_id} (part 1)"

        # Descend into nested sections, carrying the heading trail down.
        for child in node.named_children:
            if child.type == "section":
                visit(child, trail, chunk_id_for_children)

    visit(tree.root_node, heading_trail=[], parent_id=None)

    return ParsedDocument(
        source_bytes=source,
        file_path=relative_path,
        doc_type=doc_type,
        content_hash=_hash_text(source.decode("utf-8", errors="replace")),
        chunks=chunks,
        relationships=relationships,
        mentions=mentions,
    )


def _parse_plain(source: bytes, relative_path: str, doc_type: str) -> ParsedDocument:
    """
    Handle a file with no heading structure: yaml, json, toml, plain text.

    Stored as one chunk unless it is large, in which case it is split on
    blank lines. There are no nested sections, so there are no CONTAINS edges.
    """
    text = source.decode("utf-8", errors="replace")
    file_name = Path(relative_path).name

    chunks: list[DocChunk] = []
    mentions: list[tuple[str, str]] = []

    # Split on MAX_DOC_CHUNK_CHARS rather than the embedding limit: a whole
    # 200 KB .sql schema embedded as one vector would be far too diffuse to
    # match anything usefully. Several focused chunks retrieve much better.
    pieces = _split_large_text(text, start_line=1, max_chars=config.MAX_DOC_CHUNK_CHARS)

    for index, (piece, start_line, end_line) in enumerate(pieces):
        if not piece.strip():
            continue

        # Only number the id when the file actually had to be split, so the
        # common single-chunk case gets a clean "config.yaml::config.yaml".
        suffix = file_name if len(pieces) == 1 else f"{file_name} (part {index + 1})"
        chunk_id = f"{relative_path}::{suffix}"

        chunks.append(
            DocChunk(
                id=chunk_id,
                file_path=relative_path,
                doc_type=doc_type,
                title=suffix,
                heading_path=suffix,
                level=0,          # 0 means "whole file", not a heading
                start_line=start_line,
                end_line=end_line,
                text=piece,
                content_hash=_hash_text(piece),
            )
        )

        for name in _extract_mentions(piece):
            mentions.append((chunk_id, name))

    return ParsedDocument(
        source_bytes=source,
        file_path=relative_path,
        doc_type=doc_type,
        content_hash=_hash_text(text),
        chunks=chunks,
        relationships=[],
        mentions=mentions,
    )


def parse_document(absolute_path: Path, relative_path: str) -> Optional[ParsedDocument]:
    """
    Read one non-code file and split it into embeddable chunks.

    The mirror image of parsing.parse_file. Returns None if this is not a
    file type we index as a document.
    """
    # `classify` and NOT `detect_doc_type`, so this agrees exactly with what
    # change_detection/ decided to put in the Merkle tree.
    #
    # They used to disagree. classify() accepts an unknown-but-textual
    # extension as "text", so a `.zig` or `.unknownext` file got a File node
    # and a stored hash - but detect_doc_type() returned None for it, so
    # parse_document refused it and produced no content at all. The file then
    # counted as "unchanged" on every later run, so it stayed permanently
    # empty in the graph while looking indexed.
    language, doc_type = classify(absolute_path)
    if language is not None or doc_type is None:
        return None

    source = absolute_path.read_bytes()

    # Markdown and reStructuredText have headings, so they get real sections.
    # (rst is parsed with the markdown grammar as a rough approximation -
    # its `#`-style headings are close enough, and the fallback splitter
    # catches anything the grammar does not understand.)
    if doc_type in ("markdown", "rst"):
        parsed = _parse_markdown(source, relative_path, doc_type)
        # A markdown file with no headings at all produces no sections.
        # Fall back to whole-file chunking so its content is not lost.
        if parsed.chunks:
            return _add_interface_refs(_cap_chunks(parsed))

    return _add_interface_refs(_cap_chunks(_parse_plain(source, relative_path, doc_type)))


def _add_interface_refs(parsed: ParsedDocument) -> ParsedDocument:
    """
    Record the routes and environment variables this document names.

    A compose file, a .env.example or a deployment doc DECLARES the values
    that code elsewhere reads, so these are the edges that connect
    configuration to the services consuming it - across languages, which no
    name-based rule can do. See parsing/interfaces.py.

    Configuration formats also get bare `KEY: value` declarations recognised,
    which is how they name variables that code reads through `os.getenv`.
    """
    is_config = parsed.doc_type not in config.PROSE_DOC_TYPES

    # A .proto file has no tree-sitter grammar here, so it arrives as plain
    # text - but it is the one document in a repository that exists PURELY as
    # a cross-language contract. Its `service` and `rpc` declarations are the
    # names every generated client shares, so they are worth reading out
    # properly rather than treating the file as prose that happens to
    # mention some words.
    is_proto = any(
        chunk.file_path.lower().endswith(".proto") for chunk in parsed.chunks[:1]
    )

    refs: list[tuple[str, str, str]] = []
    for chunk in parsed.chunks:
        for kind, value in extract_interface_tokens(
            chunk.text, is_config=is_config, is_proto=is_proto
        ):
            refs.append((chunk.id, kind, value))

    parsed.interface_refs = refs
    return parsed


def _cap_chunks(parsed: ParsedDocument) -> ParsedDocument:
    """
    Stop one file from taking over the document index.

    The size limit in config is the first defence; this is the second, and it
    catches what size alone cannot - a file under the limit that is still
    mostly repetition. A 236 KB JSON export produced 60 chunks, which was 81%
    of every Doc node in the graph, and those fragments were then retrieved
    into the answers for unrelated questions.

    PROSE AND DATA GET DIFFERENT CEILINGS, AND THAT MATTERS
    -------------------------------------------------------
    The cap was written for data dumps and then applied to everything,
    including genuine hand-written documentation - where it silently threw
    away section 13 onwards. A long architecture document is exactly the kind
    of file whose later sections carry the deep detail, and losing them
    without a word is the worst sort of recall hole: nothing fails, the
    answer is just quietly incomplete.

    Prose cannot be a machine-generated dump - it has headings, an author and
    a structure - so it gets a far higher ceiling. The tight limit stays where
    it belongs, on the data-shaped types that motivated it.
    """
    limit = (
        config.MAX_PROSE_CHUNKS_PER_FILE
        if parsed.doc_type in config.PROSE_DOC_TYPES
        else config.MAX_DOC_CHUNKS_PER_FILE
    )
    if limit <= 0 or len(parsed.chunks) <= limit:
        return parsed

    kept = parsed.chunks[:limit]
    kept_ids = {chunk.id for chunk in kept}

    parsed.chunks = kept
    # Edges into a chunk we just dropped would dangle.
    parsed.relationships = [
        rel for rel in parsed.relationships
        if rel.source_id in kept_ids and rel.target_id in kept_ids
    ]
    parsed.mentions = [
        (chunk_id, name) for chunk_id, name in parsed.mentions
        if chunk_id in kept_ids
    ]
    return parsed
