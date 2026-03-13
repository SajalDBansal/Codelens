# 23 - A developer's guide to changing CodeLens

Start with the public command, then trace the particular stage you need to change. The package is small enough to follow a request directly without learning a large orchestration framework.

## Read the code in a useful order

1. [models.py](../graph_rag/models.py): understand the objects crossing module boundaries.
2. [cli/app.py](../graph_rag/cli/app.py) and [cli/workflow.py](../graph_rag/cli/workflow.py): understand selection, settings, locks, and failure handling.
3. [pipeline/indexer.py](../graph_rag/pipeline/indexer.py): follow one indexing operation.
4. [storage/schema.py](../graph_rag/storage/schema.py) and [storage/graph_store.py](../graph_rag/storage/graph_store.py): see what persists and how it is searched.
5. [retrieval/retriever.py](../graph_rag/retrieval/retriever.py) and [generation/answerer.py](../graph_rag/generation/answerer.py): follow a question into context and an answer.
6. Read the parser/embedding/change-detection module relevant to your change, then its tests.

Existing comments can include historical motivation or old examples. Use executable behavior and tests to resolve conflicts; these docs describe the current local backend.

## Where a change belongs

| Change | Likely files and checks |
| --- | --- |
| Add a public option/command | `cli/app.py`, possibly workflow/adapters; verify help, validation, and project/config behavior. |
| Add a saved engine setting | `config.py`, `cli/engine_options.py`, relevant engine use; verify catalog coverage and update chapter 06. |
| Recognize another extension/language construct | `parsing/languages.py`, possibly `code_parser.py`; add representative syntax and relationship cases. |
| Change document splitting | `doc_parser.py`; verify text coverage, line ranges, IDs, caps, and removed chunks on refresh. |
| Improve name resolution | `pipeline/indexer.py`, `import_resolver.py`; test both resolved and deliberately ambiguous cases. |
| Change embedded text | `embedding/embedder.py`; verify fingerprints and explain when forced re-indexing is needed. |
| Change stored fields | `models.py`, `storage/schema.py`, conversion/query methods; design compatibility/migration deliberately. |
| Change search/ranking | `graph_store.py`, `retriever.py`; use structural tests plus representative model-backed retrieval evaluation. |
| Change answer behavior | `generation/answerer.py`, model options; inspect context and citations, not just wording. |

Adding a provider is broader than changing one SDK call. It affects setup choices, credential storage, profile identity, embedding dimensions, errors/retries, model capabilities, tests, and documentation.

## Preserve the important invariants

- One graph belongs to one source root and embedding profile.
- A saved vector must describe the exact input/profile identified by its fingerprint.
- Failed files must remain eligible for retry; successful-looking hashes must not hide unfinished work.
- Deletions must still be detected during forced runs.
- Unchanged definitions in an edited file still need current source coordinates.
- Replacing an embedding profile must not publish the new active graph before completion.
- Config/credential errors must not reveal the API key in output.
- Source and retrieved prose are untrusted input; they do not authorize running repository instructions.

## Add one small, checkable example

For a parser/resolution change, construct a minimal fixture with the exact syntax, an expected target, and a similar-looking wrong target. For recovery, interrupt the relevant stage and verify a retry fixes it. For retrieval, test which evidence survives the context budget, not merely whether a function returned a nonempty list.

The bundled checkout example is useful for a smoke run; do not treat success on four functions as proof that every large application will work. Expand evaluation cases when supporting new language/framework patterns.

## Verify and document

```sh
python -m pip install -e ".[dev]"
python -m pytest -q
python scripts/smoke_local.py
```

Use focused tests during development and the full suite before a release. Real-model evaluation is separate and incurs usage. Keep examples and command options synchronized with the CLI. After changing stored/schema behavior, test existing graph handling explicitly rather than only fresh databases.

The source distribution includes docs, tests, scripts, examples, and evaluation inputs through `MANIFEST.in`. The wheel is the runtime import package with metadata/license. Build/install checks should validate both forms before publishing; a local editable install alone does not prove packaging completeness.

CodeLens is MIT-licensed; see [LICENSE](../LICENSE). Repository and issue links are in the [package README](../README.md).

[Previous](22-end-to-end-walkthrough.md) | [Guide](README.md)
