# 14 - Local storage and maintenance

CodeLens creates a local index when you run `codelens embed .`. Python's built-in
SQLite stores graph metadata; NumPy files store embeddings. No database server,
Docker service, or separate storage setup is required.

## Files in your project

```text
.codelens/
  project.json
  graph.sqlite
  graph.sqlite.vectors/
    <sha256>.npy
```

SQLite stores node properties, typed directed edges, model and dimension metadata,
file/folder hashes, keyword postings, and compressed source snapshots. Vectors are
float32 binary arrays. SQLite references connect their rows to code, document, and
summary nodes. CodeLens adds `.codelens/` to the project's `.gitignore`.

A model rebuild creates a separate graph generation. Use `codelens status` to find
the active index; it may be inside `.codelens/graphs/` after a rebuild.

## Source references and snapshots

Code and document chunks reference a repository-relative path, the SHA-256 hash of
the parsed file bytes, and exact character ranges. Offsets refer to UTF-8 decoded
with `errors="replace"`. CodeLens accepts a range only when it reconstructs the
indexed text exactly, and verifies the live file hash before reading it.

If the file has changed or disappeared, retrieval uses its compressed snapshot.
Snapshots are deduplicated by content hash. Synthetic module text, generated
summaries, and transformed chunks use compressed text blobs. This preserves the
indexed version for `--no-update` and keeps source context consistent with its
embedding. Paths and line numbers alone would not provide that guarantee.

The index therefore contains compressed source content as well as references.
Keep it private wherever the source itself needs to remain private.

## Search and graph operations

| Feature | Implementation |
| --- | --- |
| Semantic search | Exact cosine scan over memory-mapped float32 arrays. |
| Keyword search | Persistent SQLite postings, BM25, identifier tokenization, and exact-name boosts. |
| Hybrid retrieval | Reciprocal rank fusion, graph expansion, ranking, and bounded context. |
| Relationships | Typed CALLS, CONTAINS, EXTENDS, USES_TYPE, IMPORTS, MENTIONS, and REFERENCES edges. |
| Incremental indexing | File/block fingerprints, embedding reuse, deletion handling, and resolver retries. |
| Recovery | SQLite transactions, durable vector writes, integrity checks, and completion markers. |

Developers can use `GraphStore.query()` with parameterized SQL. See the
[data model](13-data-model-and-schema.md) for table names and a read-only example.

## Reclaim unused space

Updates append immutable vector segments. To remove obsolete vectors and source
snapshots, run inside the indexed project:

```sh
codelens compact
```

From another folder, use `codelens compact --project PROJECT_ID`. Compaction checks
integrity, repacks active vectors, removes unreferenced segments and snapshots,
and vacuums SQLite. It makes no model requests and preserves retrieval results.
It does not remove previous graph generations from model rebuilds.

The CLI holds project and index locks during maintenance. Direct library callers
must coordinate access using the same locks; close direct readers before compacting.

## Back up and restore

1. Stop CodeLens commands and close direct `GraphStore` connections.
2. Copy the project's entire `.codelens` folder to your backup location.
3. To restore, stop CodeLens again and restore that folder at the same project path.

Keep the manifest, SQLite file, and matching `.vectors` directory together.
Copying only SQLite loses the embeddings. The index records its source root;
moving it to an unrelated path is not supported automatically. Index the project
at its new location when moving a checkout.

## Capacity and measurement

Exact vector search scans stored vectors, so its cost grows with the number and
dimension of embeddings. Vector payloads use roughly `4 × count × dimensions`
bytes, plus headers and metadata. Disk usage also depends on source snapshots,
keyword postings, and update history. See [performance measurement](22-storage-benchmark.md)
to benchmark the local pipeline on your machine.

[Previous: Data model and storage](13-data-model-and-schema.md) | [Guide](README.md) | [Next: Relationships](15-relationships.md)
