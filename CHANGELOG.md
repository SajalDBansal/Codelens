# Changelog

## 0.2.0 — SQLite storage and index maintenance

Replaces the LadybugDB backend from 0.1.0 with SQLite and NumPy storage.

- Local code and documentation indexing with SQLite graph metadata, persistent
  keyword search, and NumPy float32 embedding files. No database server required.
- Semantic and keyword retrieval, graph expansion, and answers with source references.
- Incremental indexing that reuses unchanged embeddings and detects removed files.
- Verified source references and compressed snapshots for consistent indexed context.
- `codelens init`, `embed`, `ask`, `retrieve`, `status`, and project management.
- API keys in the OS credential store and settings shared across local projects.
- `codelens compact` to reclaim obsolete vectors and snapshots without model calls.
- Python 3.11–3.15 compatibility checks, offline pipeline tests, and reproducible
  local performance measurement. Python 3.15 installation requires a compiler for
  the parser dependency; see [compatibility](docs/08-compatibility.md).

### Upgrading from 0.1.0

Existing LadybugDB `.lbug` indexes cannot be opened by the SQLite backend, and
automatic conversion is not provided. Back up settings and project `.codelens`
directories, then create fresh SQLite indexes and re-index the source. Re-indexing
can incur OpenAI usage charges.

Remove obsolete `ladybug_buffer_mb` and `ladybug_threads` fields from saved engine
settings, or recreate the settings. Library users of `GraphStore.query()` must
replace Cypher queries with parameterized SQL.

Install from source using the [quickstart](README.md#get-started).
