# 11 - Data model and local storage

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
    graph.lbug               # initial Ladybug graph
    graphs/<id>/graph.lbug   # replacement graph generations
```

Additional lock or database sidecar files may appear. The API key is held separately in the OS credential store. The shared registry contains locations, not project source text, vectors, or chat transcripts.

`project.json` records the project ID/name/root, active database reference, timestamps, status, embedding model/dimensions, last answer/summary models, statistics, a pending replacement, and previous database references. Status can be `new`, `indexing`, `ready`, or `failed`.

## Python models and database tables

`models.py` defines objects passed between Python modules. `schema.py` defines their typed LadybugDB representation. Python properties use names such as `file_path`; the database uses properties such as `filePath`. Conversion helpers translate between them.

| Node table | Key | Meaning |
| --- | --- | --- |
| `Folder` | `path` | Repository-relative directory and completion hash; root path is the empty string. |
| `File` | `path` | Accepted source/document file, classification, and completion hash. |
| `CodeEntity` | `id` | Function/class/method/constant/implementation/module, source text and resolution hints. |
| `Doc` | `id` | Documentation/configuration chunk and source coordinates. |
| `Summary` | `id` | Generated folder overview, folder hash, and entity count. |
| `Interface` | `id` | Normalized route/topic/table/env/gRPC token shared by referring nodes. |
| `IndexMeta` | `id` | Embedding model, dimensions, schema version, and original repository root. |

Typed relationship tables allow only the supported endpoint combinations. For example, `CALLS` joins two code entities, `IMPORTS` joins files, `MENTIONS` joins a document to code, and `REFERENCES` joins code/documents to an interface. `CONTAINS` supports several hierarchy combinations.

Primary keys make repeated writes address existing objects. `MERGE` creates or finds the keyed node/edge; setters refresh its properties. Removed definitions and files require explicit cleanup rather than assuming an upsert will remove them.

## Vectors and the three hashes

Code, document, and summary tables have fixed-width `FLOAT[dimensions]` vectors. The width is selected when creating a database. Stored metadata prevents a graph from silently being used with another embedding model, dimension, schema version, or source root.

| Property | Meaning |
| --- | --- |
| `contentHash` | Source/content identity; for a summary this reflects its folder hash. |
| `embeddingInputHash` | Fingerprint of the current exact embedding text plus model and dimensions. |
| `embeddingHash` | Fingerprint associated with the vector actually saved. |

When new text no longer matches an old vector's fingerprint, the write clears the stale vector/hash. Saving a replacement attaches the vector and its matching fingerprint together. This avoids treating an old embedding as valid just because the node's source text was updated.

## How search is stored

Vector search uses Ladybug's core cosine calculation over stored vectors and orders the results. This implementation performs exact similarity search; it does not create an HNSW/ANN index or download a vector extension.

Keyword search is implemented in Python using BM25-style postings from graph text. Its token/posting cache belongs to an open `GraphStore` connection and is invalidated by writes. It is not a persistent native full-text index. Both approaches are straightforward for a local package, but their cost grows with corpus size.

## Connections and ownership

One database file belongs to one project, so identical IDs such as `src/main.py::run` in another project do not collide. Two graph generations of the same project are separate database files as well. There is no cross-user shared graph deduplication layer.

Use `GraphStore` as a context manager so native connections and results are closed. The local wrapper materializes query rows and closes the native result before reuse. Some writes use transaction helpers; the entire indexing operation is not one atomic transaction.

For ordinary inspection use `codelens status --project demo`. A developer can read a known active graph without changing it:

```python
from graph_rag.storage import GraphStore

with GraphStore("/actual/path/from/status/graph.lbug", read_only=True) as store:
    print(store.index_metadata())
    print(store.query("MATCH (e:CodeEntity) RETURN count(e) AS entities"))
```

Use the active path reported by status, not a guessed initial path after a rebuild. Close CLI operations before copying database files; retain the manifest and the graph files it references. There is no dedicated backup/restore or prune command yet.

**Source:** [models.py](../graph_rag/models.py), [schema.py](../graph_rag/storage/schema.py), [graph_store.py](../graph_rag/storage/graph_store.py), [local_connection.py](../graph_rag/storage/local_connection.py), [projects.py](../graph_rag/cli/projects.py).

[Previous](10-parsing-documents.md) | [Guide](README.md) | [Next: Relationships](12-relationships.md)
