# 09 - How code becomes entities

Parsing means reading source according to a language's syntax. Tree-sitter returns a syntax tree: a function definition contains a name, parameters, and a body; a class contains members; an import has its own syntax. CodeLens walks that tree to create its own simpler objects.

## From text to a stored unit

```python
def add_tax(amount: float, percent: float) -> float:
    return amount * (1 + percent / 100)
```

For this function in `pricing.py`, an entity includes:

| Field | Meaning |
| --- | --- |
| `id` | `pricing.py::add_tax`, used to find the same logical entity again. |
| `kind` / `name` | `function` / `add_tax`. |
| `file_path` / `language` | The relative source path and parser language. |
| `start_line` / `end_line` | One-based source coordinates for display and citations. |
| `code` / `docstring` | Source text and extracted documentation. |
| `content_hash` | Hash of the entity's code content. |
| Call/type/owner hints | Facts useful for relationship resolution later. |

The exact embedding-input fingerprint is separate from the content hash; paths, names, and documentation can affect an embedding too.

## Language rules

Different grammars use different node names. `LanguageConfig` in `languages.py` tells the shared parser which nodes represent functions, classes, calls, constants, imports, and related features. The extension map selects the grammar, and loaded parsers are cached.

This avoids writing a complete independent parser for every language, but it does not make support uniform. Go receiver methods, Rust `impl` blocks, JavaScript function-valued declarations, and nested Python definitions need different handling. A recognized extension means there are rules for it, not that every valid program construct is fully understood.

## Entity identity and ownership

Identifiers combine the relative file path with a qualified name, such as `service.py::AuthService.login`. Nested scope helps distinguish identically named members. Collision handling adds disambiguation where declarations would otherwise reuse an ID. IDs are deterministic for the parser's input, but renaming a file/function or changing its ownership can replace an ID rather than update it in place.

Some languages express ownership outside a nested class body. A Go receiver method or a Rust implementation can be linked to its owning type using stored owner hints. That link can be completed after other files are parsed.

## Calls, types, and module code

The parser collects call sites while respecting definition boundaries: a nested function's calls should not automatically be attributed to its enclosing function. It resolves what it can locally and records unresolved names and evidence for cross-file processing.

Receiver type annotations and argument counts can help disambiguate a name. Signatures and fields can produce `USES_TYPE` references. A self-call is recorded as recursion rather than treated as an ordinary loop to traverse repeatedly.

Each source file can also produce a `::<module>` entity. It captures top-level imports, initialization, wiring, and an outline of declarations. Function/class bodies already represented separately are not simply copied into a second full-file blob. This makes file-level questions possible while reducing repeated text.

Module nodes can match many keywords and connect to many definitions. Retrieval therefore applies a module keyword penalty and a cap on module containment expansion.

## What parse_file returns

`parse_file` returns a `ParsedFile` containing entities, relationships found locally, unresolved references, imports, interface references, and file metadata. It does not generate vectors or write the graph itself. The indexer owns those later steps.

Tree-sitter can recover a partial tree from invalid syntax. A successful parser return does not prove the file compiles or that coverage is complete. Exceptions or an absent parse result are recorded as file failures by indexing. No project scripts are executed to discover runtime behavior.

## Inspect the parser directly

From the source checkout with CodeLens installed:

```python
from pathlib import Path
from graph_rag.parsing.code_parser import parse_file

parsed = parse_file(Path("examples/smoke_repo/pricing.py"), "pricing.py")
for entity in parsed.entities:
    print(entity.id, entity.kind, entity.start_line, entity.end_line)
```

This does not call OpenAI or write a graph. It can initialize the language parser, so a clean environment may need its grammar resources first.

**Source:** [code_parser.py](../graph_rag/parsing/code_parser.py), [languages.py](../graph_rag/parsing/languages.py), [models.py](../graph_rag/models.py), [test_scope_and_ownership.py](../tests/test_scope_and_ownership.py).

[Previous](08-what-gets-indexed.md) | [Guide](README.md) | [Next: Documents](10-parsing-documents.md)
