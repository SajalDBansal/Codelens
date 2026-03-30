# 09 - Architecture and module map

CodeLens is a synchronous local Python application. A CLI invocation selects one project, acquires local locks, applies settings, and runs indexing or retrieval. SQLite runs inside that process. OpenAI is the external model service.

## Responsibilities

| Location | Responsibility |
| --- | --- |
| `graph_rag/cli/app.py` | Typer command tree and public options. |
| `cli/config_commands.py`, `interactive.py` | Setup, readable configuration tables, editable prompts. |
| `cli/settings.py`, `engine_options.py` | Models, credential lookup, validation, scoped runtime settings. |
| `cli/state.py` | Versioned records, configuration-directory selection, JSON writes, file locks. |
| `cli/projects.py` | Registry, manifests, selection, stored database inspection, `.gitignore` entry. |
| `cli/workflow.py` | Model compatibility, update-before-answer, rebuilds, recovery, timestamps. |
| `cli/maintenance.py` | Index validation and compaction under project/index locks. |
| `cli/index_command.py`, `ask_command.py` | Engine adapters, progress/results, compatibility entry points. |
| `config.py`, `models.py` | Built-in defaults and typed in-memory data objects. |
| `change_detection/merkle.py` | Eligible-file scan, hashes, change/deletion sets. |
| `parsing/` | Code, document, and interface-token extraction. |
| `pipeline/indexer.py`, `import_resolver.py` | Indexing order and cross-file resolution. |
| `pipeline/summarizer.py` | Optional model-generated folder overviews. |
| `embedding/embedder.py` | Embedding input text, fingerprints, batches, retries. |
| `storage/graph_store.py` | Public storage imports; implementation lives in `sqlite_store.py`. |
| `storage/sqlite_store.py`, `sqlite_schema.py` | SQLite tables, transactions, graph operations, keyword search, statistics. |
| `storage/vector_files.py` | Durable immutable NumPy vector segments and memory maps. |
| `storage/source_text.py` | Verified source ranges and compressed fallback snapshots. |
| `storage/search.py` | Identifier tokenization and rank fusion helpers. |
| `retrieval/retriever.py` | Search fusion, expansion, ranking, bounded context. |
| `generation/` | Answer prompt and model call. |

The separation is by responsibility, not an enforced rule that imports only flow in one directory order. For example, storage uses embedding-input builders to decide whether a vector still matches the text being stored.

## Indexing call path

```text
codelens embed
  app.run_workflow
    require_settings + add/resolve project
    workflow.run
      project lock + stored model/root checks
      engine_settings context
      workflow.index_project
        ensure_gitignore + save status/pending generation
        database command lock
        index_command.main(_managed=True)
          pipeline.run_indexing
        inspect completed graph + save project metadata
```

The alternate command entry points delegate to the Typer CLI. The `_managed=True` adapters are internal calls after the outer workflow has applied project/configuration checks. Normal users should not bypass that layer.

## Question call path

```text
codelens ask / retrieve
  app.run_workflow -> workflow.run
    select project + lock + check model
    update through index_project unless --no-update
    ask_command.main(_managed=True)
      open GraphStore
      retrieval.retrieve
      print evidence/context
      generation.answer_question       # ask only
    save last-used/model metadata
```

## Where state lives

| State | Location | Lifetime |
| --- | --- | --- |
| User choices | `~/.codelens/config.json` | Shared across local projects. |
| API key | OS credential store | Associated with the configuration directory. |
| Project directory/index | `~/.codelens/projects.json` | Locates graphs; does not contain their code. |
| Project manifest | `<repo>/.codelens/project.json` | Source root, active/pending graph paths, models, status, counts. |
| Graph, metadata, hashes, source snapshots | Active SQLite file, initially `.codelens/graph.sqlite` | Persistent per project/generation. |
| Embeddings | Matching `graph.sqlite.vectors/` directory of `.npy` segments | Persistent; obsolete segments are reclaimed by compaction. |
| Keyword postings | SQLite tables, with a bounded query cache in memory | Persistent postings; writes invalidate cached results. |
| Parser cache | Process memory; underlying grammar tooling may use a disk cache | Reuses loaded parsers. |

`engine_settings` temporarily applies a saved profile to engine runtime values and restores them afterward. This supports the local synchronous CLI; it is not a request-isolated configuration design for a concurrent web server.

Local locks coordinate shared JSON, a project operation, and the exact database path. They prevent conflicting CLI operations but do not create durable jobs, multi-user authorization, or distributed leases. The graph database is not opened through a network driver.

**Source:** start with [app.py](../graph_rag/cli/app.py), then [workflow.py](../graph_rag/cli/workflow.py), then [indexer.py](../graph_rag/pipeline/indexer.py) or [retriever.py](../graph_rag/retrieval/retriever.py).

[Previous: Python compatibility](08-compatibility.md) | [Guide](README.md) | [Next: What gets indexed](10-what-gets-indexed.md)
