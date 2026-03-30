# 16 - Embedding inputs, batches, and reuse

An embedding is a numeric vector for a text input. CodeLens stores vectors alongside code, documents, and folder summaries, then embeds a question with the same profile so their similarities can be compared.

## What is actually embedded?

For code, the input includes a breadcrumb and source text. An illustrative input for the demo is:

```text
File: pricing.py
Language: python
Function: add_tax
Doc: Apply percentage tax to an already discounted amount.

def add_tax(amount: float, percent: float) -> float:
    ...
```

The real input includes the extracted code, not the ellipsis shown here. An enclosing qualified name is included where available. Docstrings are shortened for the breadcrumb, and long code/text bodies are capped by `max_embed_chars` before embedding.

Documents use file/type/section breadcrumbs plus chunk text. Summaries use the folder path plus generated prose. The question is embedded as query text. Paths and headings matter because identical text can have different meaning in different locations.

## Models and dimensions

The built-in embedding choices are `text-embedding-3-small`, `text-embedding-3-large`, and `text-embedding-ada-002`. Their default widths are 1536, 3072, and 1536 respectively. The saved configuration validates the chosen width; supported v3 choices can use shorter dimensions, while the ada choice is fixed at 1536.

These are package-supported IDs and settings, not a guarantee of account availability. Use `codelens models` and `codelens config check`. The answer/reasoning model is selected separately and does not need to share the embedding model's identity.

## Batching and retries

`Embedder.embed_texts` groups inputs, with 64 items per request by default. The returned items are ordered by their response index before being mapped back to entity IDs. One batch can contain many functions: a count of 64 embedded blocks does not mean 64 HTTP requests.

Batching reduces request overhead. It does not make input tokens free or inherently lower a provider's per-token price. Total work depends on how much accepted text is sent and how often it is repeated.

The wrapper attempts a failing batch up to four times, waiting 1, 2, then 4 seconds for retryable failures such as rate limits/server errors or failures without an HTTP status. Authentication and other explicit non-retryable request errors stop sooner. The underlying SDK may also have its own retries, so this is not a strict total HTTP-attempt bound.

## Exact input fingerprints

The fingerprint is conceptually:

```text
SHA256(model + separator + dimensions + separator + full_embedding_input)
```

This is more specific than hashing the function body. A new path, changed breadcrumb, docstring change, truncation setting, model, or dimensions can change what should be embedded. The indexer checks the saved fingerprint only when it reparses the relevant content; after changing input-building settings, force an embed to revisit unchanged files.

An existing node with no valid vector still needs embedding. A matching body plus an obsolete vector is insufficient. Storage clears a vector whose saved fingerprint does not match the new input and records the new vector's matching fingerprint when it is saved.

## Reuse and failure boundaries

Unchanged valid inputs are reused on normal updates. `--force` requests reprocessing/re-embedding instead. Successful graph writes may survive an interrupted operation, so retry can reuse work already saved.

There is not a durable checkpoint after every API batch. `embed_entities` collects the results of its batches before the caller saves that category's vectors. If a later batch fails before this save, earlier API work in that call may be repeated. Source completion hashes are deferred so an interrupted operation remains eligible for repair.

Changing the embedding profile is a separate case: the CLI offers keep, rebuild, or cancel. Rebuild creates a separate graph generation and switches the manifest only after success. It does not mix incompatible vectors in the existing fixed-width table.

## Inspect an input without an API request

```python
from pathlib import Path
from graph_rag.parsing.code_parser import parse_file
from graph_rag.embedding.embedder import build_embedding_text

parsed = parse_file(Path("examples/smoke_repo/pricing.py"), "pricing.py")
entity = next(item for item in parsed.entities if item.name == "add_tax")
print(build_embedding_text(entity))
```

This shows the built-in-default input construction without creating an OpenAI client. It is useful when debugging what meaning a vector is supposed to represent.

**Source:** [embedder.py](../graph_rag/embedding/embedder.py), [sqlite_store.py](../graph_rag/storage/sqlite_store.py), [vector_files.py](../graph_rag/storage/vector_files.py), [workflow.py](../graph_rag/cli/workflow.py).

[Previous: Relationships](15-relationships.md) | [Guide](README.md) | [Next: Change detection](17-change-detection.md)
