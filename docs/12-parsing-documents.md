# 12 - How documents become chunks

Code alone does not explain every intention, setup step, or configuration choice. CodeLens indexes eligible prose and configuration text as `Doc` nodes, separately from code entities.

## Markdown follows headings

Consider:

```markdown
# Checkout
An overview of order pricing.

## Calculation order
`subtotal` runs before `apply_discount` and `add_tax`.
```

The Markdown parser creates section chunks with titles, heading trails, text, and source line ranges. A parent section keeps its own introduction rather than repeating the entire text of all its children. `CONTAINS` edges preserve the hierarchy between emitted sections.

An identifier combines the path and heading trail, such as `README.md::Checkout > Calculation order`. Split pieces receive part suffixes. Repeated identical heading trails can currently produce duplicate chunk IDs; they are not automatically made unique. Give repeated sections distinct titles or inspect their output if content appears missing. A heading-only container may not get its own chunk, while its useful children still do.

Large sections are split around paragraph/line boundaries to respect character limits. Oversized individual lines can require further splitting. This is a text-size policy, not a model-specific tokenizer or a guarantee that every code fence remains an independent semantic unit.

## Other documents

YAML, JSON, TOML, configuration files, and other accepted text use the plain-text route. They are split into text pieces with file-derived titles and line positions. They are not necessarily parsed into semantic JSON keys, YAML objects, or a separate AST for each file format.

Small files may become one chunk; larger files get `part N` titles. File classification can recognize many extensions, but accepting a format does not mean the package deeply understands that format's structure.

## Chunk fields

| Field | Purpose |
| --- | --- |
| `id` | Stable-ish path and section identity for updating the stored chunk. |
| `file_path`, `doc_type` | Where the text came from and its classification. |
| `title`, `heading_path`, `level` | Human-readable section context and depth. |
| `start_line`, `end_line` | Source coordinates. |
| `text`, `content_hash` | The section content and its hash. |
| `embedding` | Optional vector attached after embedding succeeds. |

Heading renames or chunk-boundary changes may create new IDs. The indexer removes old chunk IDs that no longer exist in a successfully reparsed file.

## Limits

`max_doc_chunk_chars` defaults to 4000. `max_doc_chunks_per_file` defaults to 12 for structured documents. `max_prose_chunks_per_file` defaults to 80 for configured prose types (`markdown`, `rst`, `text`). The cap keeps the earliest chunks, and removes relationships/mentions whose endpoints were discarded.

This protects cost and context from huge files, but can omit later sections. If an answer is missing, verify that the relevant document was accepted and its section survived chunking/caps before blaming the model. Change settings and force an embed to reprocess unchanged files.

## Links to code and shared interfaces

Backtick references such as `add_tax` produce mention candidates. The indexer searches indexed names and ranks possible targets by path proximity, up to `max_mention_targets`. A common name may link to several candidates. It is not a guarantee that the author intended every target.

Accepted text can also produce shared interface tokens. A route in a README or variable declaration in a configuration example may be stored through `REFERENCES`. This is text matching; it does not verify a running service or credential value.

At retrieval time, a document seed can bring in code it mentions. Code hits can bring in documents mentioning them. The answer prompt labels document text as `DOCS`, distinct from real `CODE`, because documentation may be stale.

**Try it:** run `codelens retrieve "calculation order" --project smoke_repo` after embedding the bundled demo. Inspect whether the README section and relevant helpers appear. The exact ranking depends on the embedding model and settings.

**Source:** [doc_parser.py](../graph_rag/parsing/doc_parser.py), [resolve_mention in indexer.py](../graph_rag/pipeline/indexer.py), [models.py](../graph_rag/models.py).

[Previous: Parsing code](11-parsing-code.md) | [Guide](README.md) | [Next: Data model and storage](13-data-model-and-schema.md)
