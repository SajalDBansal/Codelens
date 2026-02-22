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
from .languages import classify, get_cached_parser
from .tree_utils import hash_text as _hash_text
from .tree_utils import inclusive_end_line
from .tree_utils import node_text as _text
_MENTION_RE = re.compile('`([A-Za-z_][A-Za-z0-9_.]*)`')

def _heading_text(section_node, source: bytes) -> tuple[Optional[str], int]:
    """
    Get a section's heading text and its depth.

    Returns (title, level), e.g. ("Setup", 2) for `## Setup`.
    Returns (None, 0) if this section has no heading node - which happens for
    the preamble text before the first heading in a file.
    """
    for child in section_node.named_children:
        if child.type in ('atx_heading', 'setext_heading'):
            raw = _text(child, source).strip()
            level = len(raw) - len(raw.lstrip('#'))
            title = raw.lstrip('#').strip()
            if level == 0:
                title = raw.split('\n')[0].strip()
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
        line_count = max(1, len(text.splitlines()))
        return [(text, start_line, start_line + line_count - 1)]
    chunks: list[tuple[str, int, int]] = []
    pending: list[tuple[str, int]] = []
    pending_len = 0
    last_blank: int | None = None

    def emit(lines: list[tuple[str, int]]) -> None:
        if not lines:
            return
        body = '\n'.join((line for line, _ in lines))
        if not body.strip():
            return
        chunks.append((body, lines[0][1], lines[-1][1]))
    for number, line in enumerate(text.split('\n'), start=start_line):
        if len(line) > max_chars:
            prefix = ''
            first_line = number
            if pending and pending_len < max_chars // 10:
                prefix = '\n'.join((item for item, _ in pending)) + '\n'
                first_line = pending[0][1]
            else:
                emit(pending)
            pending, pending_len, last_blank = ([], 0, None)
            budget = max_chars - len(prefix)
            for offset in range(0, len(line), budget):
                piece = line[offset:offset + budget]
                if offset == 0 and prefix:
                    chunks.append((prefix + piece, first_line, number))
                else:
                    chunks.append((piece, number, number))
            continue
        if pending and pending_len + len(line) + 1 > max_chars:
            if last_blank is not None and last_blank > 0:
                emit(pending[:last_blank])
                pending = pending[last_blank + 1:]
            else:
                emit(pending)
                pending = []
            pending_len = sum((len(item) + 1 for item, _ in pending))
            last_blank = None
        if not line.strip():
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
    parser = get_cached_parser('markdown')
    tree = parser.parse(source)
    chunks: list[DocChunk] = []
    relationships: list[Relationship] = []
    mentions: list[tuple[str, str]] = []

    def visit(node, heading_trail: list[str], parent_id: Optional[str]) -> None:
        if node.type != 'section':
            for child in node.named_children:
                visit(child, heading_trail, parent_id)
            return
        title, level = _heading_text(node, source)
        first_subsection = next((child for child in node.named_children if child.type == 'section'), None)
        own_end_byte = first_subsection.start_byte if first_subsection else node.end_byte
        own_end_line = first_subsection.start_point[0] if first_subsection else inclusive_end_line(node)
        text = source[node.start_byte:own_end_byte].decode('utf-8', errors='replace')
        display_title = title or Path(relative_path).name
        trail = heading_trail + [display_title]
        chunk_id = f"{relative_path}::{' > '.join(trail)}"
        body_without_heading = text
        if title:
            body_without_heading = text.replace(f"{'#' * level} {title}", '', 1)
        has_content = bool(body_without_heading.strip())
        chunk_id_for_children = parent_id
        if has_content:
            pieces = _split_large_text(text, start_line=node.start_point[0] + 1, max_chars=config.MAX_DOC_CHUNK_CHARS)
            for part_number, (piece, piece_start, piece_end) in enumerate(pieces, start=1):
                part_id = chunk_id if len(pieces) == 1 else f'{chunk_id} (part {part_number})'
                chunks.append(DocChunk(id=part_id, file_path=relative_path, doc_type=doc_type, title=display_title, heading_path=' > '.join(trail), level=level, start_line=piece_start, end_line=own_end_line if len(pieces) == 1 else piece_end, text=piece, content_hash=_hash_text(piece)))
                if parent_id is not None:
                    relationships.append(Relationship(source_id=parent_id, target_id=part_id, kind='CONTAINS'))
                for name in []:
                    mentions.append((part_id, name))
            chunk_id_for_children = chunk_id if len(pieces) == 1 else f'{chunk_id} (part 1)'
        for child in node.named_children:
            if child.type == 'section':
                visit(child, trail, chunk_id_for_children)
    visit(tree.root_node, heading_trail=[], parent_id=None)
    return ParsedDocument(file_path=relative_path, doc_type=doc_type, content_hash=_hash_text(source.decode('utf-8', errors='replace')), chunks=chunks, relationships=relationships, mentions=mentions)

def _parse_plain(source: bytes, relative_path: str, doc_type: str) -> ParsedDocument:
    """
    Handle a file with no heading structure: yaml, json, toml, plain text.

    Stored as one chunk unless it is large, in which case it is split on
    blank lines. There are no nested sections, so there are no CONTAINS edges.
    """
    text = source.decode('utf-8', errors='replace')
    file_name = Path(relative_path).name
    chunks: list[DocChunk] = []
    mentions: list[tuple[str, str]] = []
    pieces = _split_large_text(text, start_line=1, max_chars=config.MAX_DOC_CHUNK_CHARS)
    for index, (piece, start_line, end_line) in enumerate(pieces):
        if not piece.strip():
            continue
        suffix = file_name if len(pieces) == 1 else f'{file_name} (part {index + 1})'
        chunk_id = f'{relative_path}::{suffix}'
        chunks.append(DocChunk(id=chunk_id, file_path=relative_path, doc_type=doc_type, title=suffix, heading_path=suffix, level=0, start_line=start_line, end_line=end_line, text=piece, content_hash=_hash_text(piece)))
        for name in []:
            mentions.append((chunk_id, name))
    return ParsedDocument(file_path=relative_path, doc_type=doc_type, content_hash=_hash_text(text), chunks=chunks, relationships=[], mentions=mentions)

def parse_document(absolute_path: Path, relative_path: str) -> Optional[ParsedDocument]:
    """
    Read one non-code file and split it into embeddable chunks.

    The mirror image of parsing.parse_file. Returns None if this is not a
    file type we index as a document.
    """
    language, doc_type = classify(absolute_path)
    if language is not None or doc_type is None:
        return None
    source = absolute_path.read_bytes()
    if doc_type in ('markdown', 'rst'):
        parsed = _parse_markdown(source, relative_path, doc_type)
        if parsed.chunks:
            return parsed
    return _parse_plain(source, relative_path, doc_type)
