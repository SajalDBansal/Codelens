# 13 - Data model and local storage

There are three distinct kinds of persistent data: shared user settings, per-project metadata, and the graph itself. Understanding that separation makes model changes, backups, and multiple projects easier to reason about.

## Files on disk

```text
~/.codelens/
    config.json              # provider, models, dimensions, engine options
    projects.json            # IDs, names, source paths, selected default
    .state.lock              # shared metadata coordination

<repo>/.codelens/
    project.json             # manifest for this project
    project.lock             # project command coordination
    graph.sqlite             # metadata, postings, compressed snapshots
    graph.sqlite.vectors/    # float32 .npy segments
    graphs/<id>/graph.sqlite   # replacement graph generations
```

Additional lock or database sidecar files may appear. The API key is held separately in the OS credential store. The shared registry contains locations, not project source text, vectors, or chat transcripts.

`project.json` records the project ID/name/root, active database reference, timestamps, status, embedding model/dimensions, last answer/summary models, statistics, a pending replacement, and previous database references. Status can be `new`, `indexing`, `ready`, or `failed`.

## Python models and database tables

`models.py` defines objects passed between Python modules. `sqlite_schema.py`
defines the physical SQLite tables. `schema.py` converts models to the storage
API's property names, such as `filePath`; the SQLite implementation maps those
properties to columns such as `file_path` and JSON data.

| Logical node label | Key | Meaning |
| --- | --- | --- |
| `Folder` | `path` | Repository-relative directory and completion hash; root path is the empty string. |
| `File` | `path` | Accepted source/document file, classification, and completion hash. |
| `CodeEntity` | `id` | Function/class/method/constant/implementation/module, source reference and resolution hints. |
| `Doc` | `id` | Documentation/configuration chunk and source coordinates. |
| `Summary` | `id` | Generated folder overview, folder hash, and entity count. |
| `Interface` | `id` | Normalized route/topic/table/env/gRPC token shared by referring nodes. |

The `nodes` table keys each object by `(label, id)`. The `edges` table uses typed foreign keys with cascading deletion; public write methods restrict supported relationship kinds and endpoints. For example, `CALLS` joins two code entities, `IMPORTS` joins files, `MENTIONS` joins a document to code, and `REFERENCES` joins code/documents to an interface. `CONTAINS` supports several hierarchy combinations.

The separate `index_meta` table has one row (`id=1`) for the embedding model,
dimensions, schema version, and original repository root. Compressed source and
text snapshots are stored by content hash in `blobs`.

Primary keys make repeated writes address existing objects. SQL upserts create or refresh the keyed node/edge. Removed definitions and files require explicit cleanup rather than assuming an upsert will remove them.

## Vectors and the three hashes

Code, document and summary nodes reference rows in immutable `.npy` files of float32 vectors. The width is selected when creating an index. Stored metadata prevents a graph from silently being used with another embedding model, dimension, schema version, or source root.

| Property | Meaning |
| --- | --- |
| `contentHash` | Source/content identity; for a summary this reflects its folder hash. |
| `embeddingInputHash` | SHA-256 of the exact embedding text plus model and dimensions. |
| `embeddingHash` | Fingerprint associated with the vector actually saved. |

When new text no longer matches an old vector's fingerprint, the write clears the stale vector/hash. Saving a replacement attaches the vector and its matching fingerprint together. This avoids treating an old embedding as valid just because the node's source text was updated.

## How search is stored

Vector search uses NumPy cosine calculations over memory-mapped float32 segments and orders the results. This implementation performs exact similarity search; it does not create an HNSW/ANN index or download a vector extension.

Keyword search uses persistent `keyword_docs` and `keyword_terms` SQLite tables. The original BM25 formula, tokenizer and exact-name boost are retained. Postings use integer document keys and are updated transactionally with chunk metadata. An open connection caches at most 128 term/statistic results; writes and SQLite data-version changes invalidate the cache. Both approaches are straightforward for a local package, but their cost grows with corpus size.

## Connections and ownership

One database file belongs to one project, so identical IDs such as `src/main.py::run` in another project do not collide. Two graph generations of the same project are separate database files as well. There is no cross-user shared graph deduplication layer.

Use `GraphStore` as a context manager so SQLite connections and vector memory maps are closed. Query results are materialized before reuse. Some writes use transaction helpers; the entire indexing operation is not one atomic transaction.

For ordinary inspection use `codelens status --project demo`. A developer can read a known active graph without changing it:

```python
from graph_rag.storage import GraphStore

with GraphStore("/actual/path/from/status/graph.sqlite", read_only=True) as store:
    print(store.index_metadata())
    print(store.query("SELECT count(*) AS entities FROM nodes WHERE label=:label", label="CodeEntity"))
```

Use the active path reported by status, not a guessed initial path after a rebuild. Close CLI operations before copying database files; retain the manifest, SQLite file, and matching `.vectors` directory. `codelens compact` removes obsolete segments and snapshots under exclusive locks. See [local storage and maintenance](14-local-storage-and-maintenance.md) for source references, snapshot semantics, and backup instructions.

**Source:** [models.py](../graph_rag/models.py), [sqlite_schema.py](../graph_rag/storage/sqlite_schema.py), [sqlite_store.py](../graph_rag/storage/sqlite_store.py), [vector_files.py](../graph_rag/storage/vector_files.py), [source_text.py](../graph_rag/storage/source_text.py), [projects.py](../graph_rag/cli/projects.py).

[Previous: Parsing documents](12-parsing-documents.md) | [Guide](README.md) | [Next: Local storage and maintenance](14-local-storage-and-maintenance.md)
