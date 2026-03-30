# 18 - The indexing pipeline, step by step

`codelens embed` means much more than "call an embedding model." It coordinates setup, project selection, parsing, graph writes, embeddings, relationship resolution, summaries, and completion metadata.

## Before the engine starts

The CLI loads or initializes user settings, registers/resolves the project, adds the `.codelens/` ignore entry if needed, and acquires the project lock. It checks the database's source root and embedding profile. A profile mismatch requires keep/rebuild/error handling before model work.

An ordinary update records `indexing`. A replacement records its pending path/profile while the previous database remains active. The workflow also locks the exact database path, then calls the managed indexing adapter.

## Inside run_indexing

| Order | Work | Main result |
| --- | --- | --- |
| 1 | Open SQLite and ensure compatible schema/model/root metadata. | A valid store for this project/profile. |
| 2 | Scan eligible files and construct today's hash tree. | File/folder hashes and code/document classifications. |
| 3 | Compare with stored hashes. | Changed files, removed files, unchanged counts. |
| 4 | If nothing changed, return early. | No parsing, new embeddings, or graph-shape recomputation. |
| 5 | Delete missing files; create/update required folder/file nodes without success hashes. | Containers ready and affected completion hashes invalidated. |
| 6 | Parse each changed file; record failures and keep processing other files. | Code entities or document chunks. |
| 7 | Check parsed source bytes against the scan hash; save source snapshots, refreshed properties and local links; delete vanished IDs and compare embedding fingerprints. | Verified source references, pending embedding lists and unresolved references. |
| 8 | Embed changed code, then document chunks, and save their vectors. | Searchable text with matching vector fingerprints. |
| 9 | Refresh file imports and resolve cross-file names, owners, types, and document mentions. | Additional graph relationships and remaining unresolved/external state. |
| 10 | Save shared interface references and prune noisy/orphan tokens. | Bounded discovery links across files/languages. |
| 11 | Generate optional summaries from complete current folder contents and embed them. | Searchable folder overviews, or no overview when optional generation fails. |
| 12 | Remove obsolete summaries/folders; save successful file and folder hashes. | Updated completion markers; failed paths remain retryable. |
| 13 | Compute statistics/connectivity and return the report. | Counts and graph-shape information for the CLI. |

Some operations happen per file or per category rather than as completely separate global phases. In particular, local graph links are written while parsing results are handled; cross-file links are resolved later.

## After the engine returns

The adapter prints the report and fails the CLI operation if files failed to parse. The workflow inspects the finished graph, verifies its profile, and saves counts/timestamps/status. A successful replacement switches the active database reference only at this point. If the operation was triggered by ask/retrieve, the question proceeds after the update succeeds.

## Understand the output

| Counter/message | Meaning |
| --- | --- |
| Files changed | New or changed eligible files, or files forced to reprocess. |
| Files failed | Files that did not produce a successful parse result. |
| Blocks embedded | Code entities that received vectors, not HTTP request count. |
| Blocks reused | Parsed entities whose stored input fingerprints/vectors were reusable. |
| Calls -> library | Names treated as external to the indexed project. |
| Calls ambiguous | Candidate targets existed but resolution did not settle them. |
| Folder overviews | Summaries generated or reused according to hashes and settings. |
| Connected pieces | Components in the stored graph analysis, not a correctness score. |

A graph with several components may be legitimate. Zero call edges can also be legitimate for a document-only or definition-only project. Interpret the metrics alongside the code, not as universal pass/fail criteria.

## Where work can fail

Credential/profile/root errors stop early. File parse errors are accumulated while other files are attempted. Essential embedding failures interrupt the run. Optional summary generation can return no summary; embedding a summary that was generated is a separate operation and can still fail.

Individual database writes can already be committed before a later error. Completion-hash invalidation and project status make retries possible, but there is no durable queue or per-API-batch job checkpoint. See [change detection and recovery](17-change-detection.md).

## What progress is available today?

The pipeline prints stage messages and counters, including embedding categories and retry messages. It does not emit a structured event for every file, graph edge, API batch, or retrieval step. Neither the embedding wrapper nor answer generation supplies a frontend-ready live event stream. Detailed streaming remains future work rather than a documented current capability.

**Source:** [workflow.py](../graph_rag/cli/workflow.py), [index_command.py](../graph_rag/cli/index_command.py), [indexer.py](../graph_rag/pipeline/indexer.py), [summarizer.py](../graph_rag/pipeline/summarizer.py).

[Previous: Change detection](17-change-detection.md) | [Guide](README.md) | [Next: Retrieval](19-retrieval.md)
