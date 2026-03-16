# Understand CodeLens

This guide explains the **current local CodeLens package**, version 0.1.0: what problem it solves, how to run it, how information moves through it, and where to find the implementation. It assumes basic familiarity with files, functions, and a terminal. You do not need prior knowledge of AI, embeddings, or graph databases.

CodeLens reads a local project, stores a searchable graph in that project's `.codelens` directory, and retrieves evidence for questions. It uses embedded LadybugDB and calls OpenAI for embeddings and generated text. User preferences are saved in `~/.codelens/config.json`; the API key is held separately in the OS credential store.

## Pick a reading path

| Your goal | Read in this order |
| --- | --- |
| Understand the idea | [RAG](01-what-is-rag.md) -> [Graph RAG](02-why-graph-rag-for-code.md) -> [Product](03-what-this-product-does.md) |
| Run it on your computer | [Setup](04-setup-and-first-run.md) -> [Commands](05-the-two-commands.md) -> [Worked example](22-end-to-end-walkthrough.md) |
| Understand every pipeline stage | [Architecture](07-architecture.md), then chapters 08 through 17 |
| Tune it or diagnose a result | [Configuration](06-configuration-reference.md) -> [Retrieval](16-retrieval.md) -> [Troubleshooting](19-troubleshooting.md) |
| Change the implementation | [Architecture](07-architecture.md) -> [Testing](18-testing.md) -> [Contributor guide](23-contributor-guide.md) |

For a complete read, follow the numbered chapters. Each chapter starts with the idea, explains the current implementation, gives examples, and links to the relevant source. The [glossary](21-glossary.md) is available whenever a term is unfamiliar.

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
 local LadybugDB graph <--- search + expand + select context
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
| [04. Setup and first run](04-setup-and-first-run.md) | How do I install, configure, and try the package locally? |
| [05. CLI commands and projects](05-the-two-commands.md) | How do init, config, embed, ask, retrieve, and project selection work? |
| [06. Configuration reference](06-configuration-reference.md) | What does every editable setting mean? |
| [07. Architecture](07-architecture.md) | Which module owns each part of the system? |
| [08. What gets indexed](08-what-gets-indexed.md) | Which files are read, skipped, or treated as documents? |
| [09. Parsing code](09-parsing-code.md) | How does source code become functions, classes, and module nodes? |
| [10. Parsing documents](10-parsing-documents.md) | How are README sections and configuration text chunked and linked? |
| [11. Data model and storage](11-data-model-and-schema.md) | What is in the graph, JSON metadata, and local database file? |
| [12. Relationships](12-relationships.md) | How are calls, types, imports, mentions, and shared interfaces resolved? |
| [13. Embeddings](13-embeddings.md) | What is sent to OpenAI, and when can an old vector be reused? |
| [14. Change detection](14-change-detection.md) | How are edits, deletions, exclusions, and failed runs handled? |
| [15. Indexing pipeline](15-the-indexing-pipeline.md) | In what order does an embed operation do its work? |
| [16. Retrieval](16-retrieval.md) | How do semantic search, keywords, graph expansion, and ranking combine? |
| [17. Generation](17-generation.md) | How are folder summaries and cited answers produced? |
| [18. Testing](18-testing.md) | What can be checked without OpenAI, and how is retrieval evaluated? |
| [19. Troubleshooting](19-troubleshooting.md) | How do I diagnose setup, indexing, and answer problems? |
| [20. Limits and roadmap](20-limits-and-roadmap.md) | What is supported, uncertain, or still future work? |
| [21. Glossary](21-glossary.md) | What do the technical words mean? |
| [22. End-to-end walkthrough](22-end-to-end-walkthrough.md) | What happens to one real function and one real question? |
| [23. Contributor guide](23-contributor-guide.md) | Where should a developer make and verify a change? |

## How to read examples honestly

For release maintenance, see [Supported versions and local verification](compatibility-and-ci.md). It explains the dependency baseline, local checks, and how to update them. GitHub Actions and CI/CD setup are deferred.

The running example is [examples/smoke_repo](../examples/smoke_repo): checkout adds prices, applies a discount, then tax. Commands marked as model-backed use your OpenAI account. The offline smoke script substitutes deterministic model results and tests mechanics rather than answer quality.

Illustrative output is labeled as such. Counts depend on your files and settings. A graph link is evidence from static analysis or a matching rule, not proof of a runtime call. Citations are requested from the answer model; they are not independently validated by a citation checker.

These docs describe the local package only. The separate worker/backend application and any proposed website are outside this package. No database-server setup or `.env` configuration is required here.

[Package README](../README.md) | [Start with chapter 01](01-what-is-rag.md)
