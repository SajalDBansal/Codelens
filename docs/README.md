# Understand CodeLens

Start with the [three-step quickstart](../README.md#get-started) to install CodeLens,
set up your key, and ask your first question. For a slower walkthrough, use
[Setup and first run](05-setup-and-first-run.md).

This guide explains CodeLens: what problem it solves, how to run it, how information moves through it, and where to find the implementation. You do not need prior knowledge of AI, embeddings, or graph databases.

CodeLens reads a local project, stores a searchable graph in that project's `.codelens` directory, and retrieves evidence for questions. It uses embedded SQLite and calls OpenAI for embeddings and generated text. User preferences are saved in `~/.codelens/config.json`; the API key is held separately in the OS credential store.

See [local storage](14-local-storage-and-maintenance.md), [performance measurement](22-storage-benchmark.md), and [compatibility](08-compatibility.md) for storage behavior and tested environments.

## Pick a reading path

| Your goal | Read in this order |
| --- | --- |
| Understand the idea | [RAG](01-what-is-rag.md) -> [Graph RAG](02-why-graph-rag-for-code.md) -> [Product](03-what-this-product-does.md) |
| Run it on your computer | [Setup](05-setup-and-first-run.md) -> [Commands](06-cli-commands-and-projects.md) -> [Worked example](26-end-to-end-walkthrough.md) |
| Understand every pipeline stage | [Architecture](09-architecture.md), then chapters 10 through 20 |
| Tune it or diagnose a result | [Configuration](07-configuration-reference.md) -> [Retrieval](19-retrieval.md) -> [Troubleshooting](23-troubleshooting.md) |
| Change the implementation | [Architecture](09-architecture.md) -> [Testing](21-testing.md) -> [Contributor guide](27-contributor-guide.md) |
| Install, upgrade, or remove the application | [Install, upgrade, uninstall](04-install-upgrade-uninstall.md) |
| Check release changes and support | [Changelog](../CHANGELOG.md) -> [Compatibility](08-compatibility.md) -> [Current limitations](24-limitations.md) |

For a complete read, follow the numbered chapters. Each chapter starts with the idea, explains the current implementation, gives examples, and links to the relevant source. The [glossary](25-glossary.md) is available whenever a term is unfamiliar.

## The whole system in one picture

```text
Saved user settings + OS credential store
                  |
             codelens CLI
                  |
     select project + lock + model checks
                  |
       +----------+------------------+
       |                             |
     embed                       ask / retrieve
       |                             |
 scan + hash <--- check/update -------+
       |                             |
 parse + connect + embed             |
       |                             |
 local SQLite graph <--- search + expand + select context
                                     |
                              retrieve: show evidence
                              ask: OpenAI answer + citations
```

There is one local CLI process for an operation, not a distributed service. The graph is built from parsing and reference resolution. Embeddings help find relevant text within that graph.

## Chapters

| Chapter | Question it answers |
| --- | --- |
| [01. What is RAG?](01-what-is-rag.md) | Why does an AI need retrieval before answering about my project? |
| [02. Why Graph RAG for code?](02-why-graph-rag-for-code.md) | How do functions, files, and documents become connected evidence? |
| [03. What the product does](03-what-this-product-does.md) | What exists today, and which problems can it help with? |
| [04. Install, upgrade, and uninstall](04-install-upgrade-uninstall.md) | How do I install the application, update it, or remove it? |
| [05. Setup and first run](05-setup-and-first-run.md) | How do I install, configure, and try the package locally? |
| [06. CLI commands and projects](06-cli-commands-and-projects.md) | How do init, config, embed, ask, retrieve, and project selection work? |
| [07. Configuration reference](07-configuration-reference.md) | What does every editable setting mean? |
| [08. Python compatibility](08-compatibility.md) | Which Python versions and platforms are verified, and what do they need? |
| [09. Architecture](09-architecture.md) | Which module owns each part of the system? |
| [10. What gets indexed](10-what-gets-indexed.md) | Which files are read, skipped, or treated as documents? |
| [11. Parsing code](11-parsing-code.md) | How does source code become functions, classes, and module nodes? |
| [12. Parsing documents](12-parsing-documents.md) | How are README sections and configuration text chunked and linked? |
| [13. Data model and storage](13-data-model-and-schema.md) | What is in the graph, JSON metadata, and local database file? |
| [14. Local storage and maintenance](14-local-storage-and-maintenance.md) | How are embeddings and source references stored, backed up, and compacted? |
| [15. Relationships](15-relationships.md) | How are calls, types, imports, mentions, and shared interfaces resolved? |
| [16. Embeddings](16-embeddings.md) | What is sent to OpenAI, and when can an old vector be reused? |
| [17. Change detection](17-change-detection.md) | How are edits, deletions, exclusions, and failed runs handled? |
| [18. Indexing pipeline](18-the-indexing-pipeline.md) | In what order does an embed operation do its work? |
| [19. Retrieval](19-retrieval.md) | How do semantic search, keywords, graph expansion, and ranking combine? |
| [20. Generation](20-generation.md) | How are folder summaries and cited answers produced? |
| [21. Testing](21-testing.md) | What can be checked without OpenAI, and how is retrieval evaluated? |
| [22. Performance measurement](22-storage-benchmark.md) | How do I measure local indexing, retrieval, storage, and memory use? |
| [23. Troubleshooting](23-troubleshooting.md) | How do I diagnose setup, indexing, and answer problems? |
| [24. Current limitations](24-limitations.md) | What does this version support, and what are its boundaries? |
| [25. Glossary](25-glossary.md) | What do the technical words mean? |
| [26. End-to-end walkthrough](26-end-to-end-walkthrough.md) | What happens to one real function and one real question? |
| [27. Contributor guide](27-contributor-guide.md) | Where should a developer make and verify a change? |

## How to read examples honestly

For Python/platform support and dependency versions, see [Compatibility](08-compatibility.md). The [changelog](../CHANGELOG.md) records release features; [Testing](21-testing.md) explains how to verify implementation changes locally.

The running example is [examples/smoke_repo](../examples/smoke_repo): checkout adds prices, applies a discount, then tax. Commands marked as model-backed use your OpenAI account. The offline smoke script substitutes deterministic model results and tests mechanics rather than answer quality.

Illustrative output is labeled as such. Counts depend on your files and settings. A graph link is evidence from static analysis or a matching rule, not proof of a runtime call. Citations are requested from the answer model; they are not independently validated by a citation checker.

These docs describe the local package only. The separate worker/backend application and any proposed website are outside this package. No database-server setup or `.env` configuration is required here.

[Package README](../README.md) | [Start with chapter 01](01-what-is-rag.md)
