# Changelog

This file records user-visible changes. Version `0.1.0` is prepared for release;
publication has not yet been confirmed.

## 0.1.0 — First release candidate

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

The distribution is `codelens`, the Python import is `graph_rag`, and the legacy
`graph-rag-index` / `graph-rag-ask` launchers remain available. Older development
installations named `graph-rag-reviewer` should be removed from the same Python
environment before installing CodeLens. Removing the package does not erase saved
settings, credentials, or project graphs.
