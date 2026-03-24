# 03 - What CodeLens does today

CodeLens is a local command-line application for exploring a codebase with graph-assisted retrieval. It is installed as the Python distribution `codelens-rag` (terminal command: `codelens`); its import package is still named `graph_rag`.

## A normal session

```sh
codelens init
codelens embed "/path/to/my-project"
codelens ask "How is the order total calculated?" --project my-project
codelens retrieve "Where is the discount checked?" --project my-project
```

Setup saves the API key and preferences. Embedding registers the project, creates its graph, and prints indexing progress. Asking checks for source changes, updates the graph if needed, retrieves evidence, and generates an answer. Retrieval does the same preparation but stops after showing the evidence.

## What is implemented

| Capability | Current behavior |
| --- | --- |
| Local repository input | Read an existing folder. Clone/download repositories yourself. |
| Code parsing | Extract entities using supported Tree-sitter grammars and language rules. |
| Document indexing | Chunk documentation/configuration and other accepted text. |
| Graph storage | Embedded LadybugDB file per project; no database server. |
| Search | Exact vector similarity plus Python BM25 keyword search. |
| Relationships | Structural, call, type, import, mention, and shared-interface links. |
| Incremental updates | File/folder hashes and exact embedding-input fingerprints avoid unnecessary work. |
| User configuration | Validated JSON settings and a separate OS keyring credential. |
| Multiple projects | A shared registry points to separate project-local graphs. |
| Model changes | Keep a graph's embedding profile or build a separate replacement. |
| Answers | OpenAI text responses prompted to use evidence and cite source locations. |

The supported parser list includes Python, JavaScript, TypeScript/TSX, Go, Rust, Java, C#, Ruby, PHP, Kotlin, Swift, Scala, C, C++, shell scripts, and Lua. Language coverage is not identical: supported syntax and resolution rules differ. Markdown is parsed into sections; other accepted text follows the document route.

## When it helps

- A new developer needs an entry point into an unfamiliar project.
- You remember a behavior but not the function name.
- A feature spans a function, its helpers, and a README section.
- You want to inspect the evidence before asking a model to explain it.

The included [checkout example](../examples/smoke_repo) is deliberately small enough to verify manually. For prices `[100, 50]`, discount `10`, and tax `20`, its code calculates `150 -> 135 -> 162`. An explanation should reflect that order and cite the corresponding code. This is a checkable expectation, not a fixed model response.

## What stays local and what goes online

Parsing, hashes, the graph, vectors, and search run locally. OpenAI receives embedding inputs; it also receives folder outlines for optional summaries and selected evidence plus the question for answers. The key is kept in the OS credential store. Projects store source excerpts in their graph files, so those files contain project data too.

## Boundaries

There is no website, API server, account/login system, GitHub cloning workflow, automatic code editing, or autonomous code execution in this package. It does not persist a conversation history or combine several project graphs into one question. It prints progress and completed answers; there is no structured event stream or token-by-token answer stream yet.

A low-relevance question may stop before answer generation, but its query embedding has already been requested. An unchanged embed can avoid new model work, but scanning still costs local time. Costs, speed, and retrieval quality depend on the repository and settings; the docs do not promise free queries or perfect answers.

**Source:** [app.py](../graph_rag/cli/app.py), [workflow.py](../graph_rag/cli/workflow.py), [pyproject.toml](../pyproject.toml).

[Previous](02-why-graph-rag-for-code.md) | [Guide](README.md) | [Next: Setup](04-setup-and-first-run.md)
