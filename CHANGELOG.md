# Changelog

This file records user-visible changes to CodeLens, distributed as `codelens-rag`.

## Unreleased

- Refined installation and pipeline documentation for the published package.
- Removed temporary publishing helpers, release checklists, and work-plan material from the repository.

## 0.1.0 — 22 October 2026

Initial beta, published as [codelens-rag on PyPI](https://pypi.org/project/codelens-rag/0.1.0/). Install with `python -m pip install codelens-rag`; run it with `codelens`.

### Included

- A local `codelens` command-line application built with Typer.
- One-time `init` for an OpenAI API key, embedding model, answer model, and recommended settings.
- Viewable and editable saved configuration, with the API key kept in the OS credential store.
- A project registry for selecting local projects from different directories.
- A separate LadybugDB graph in each project's `.codelens` folder; no database server or Docker required.
- Source parsing, documentation indexing, and relationships between supported code entities.
- OpenAI embeddings, keyword search, graph expansion, and answers prompted to cite source locations.
- `embed`, `retrieve`, `ask`, project management, status, and diagnostic commands.
- Incremental updates and embedding reuse; questions normally check for source changes first.
- Embedding-model/dimension compatibility checks and a rebuild workflow that retains the previous graph.
- Automatic `.codelens/` entries in project `.gitignore` files.
- Beginner documentation, a checkout example, tests, and manual release checks.

### Support and limits

- Verified compatibility target: Windows x64, CPython 3.11–3.13; Python 3.12 recommended.
- Python 3.14 is excluded because the tested standard Windows runtime could not load the pinned LadybugDB dependency. Other operating systems still need release verification.
- OpenAI is the only model provider. Embeddings, generated summaries, and answers require network access and can incur charges.
- Each question uses one project. Static relationships and model-generated citations can be incomplete; inspect the evidence.
- Token-by-token answer streaming, a website, persistent chat history, and automatic deletion of old graph generations are not included.

The distribution is `codelens-rag`, the command is `codelens`, the Python import is `graph_rag`, and the legacy
`graph-rag-index` / `graph-rag-ask` launchers remain available. Older development
installations named `codelens` or `graph-rag-reviewer` should be removed from the same Python
environment before installing CodeLens. Removing the package does not erase saved
settings, credentials, or project graphs.
