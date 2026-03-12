# 08 - What gets indexed?

The scanner decides which files can influence the graph, embeddings, and answers. Excluded files do not participate in the current hash tree. This avoids spending model requests on generated output, dependencies, binary assets, and obvious secret files.

## The decision sequence

1. Walk the chosen local project directory in a stable order.
2. Skip symbolic links, configured directory names, and configured directory suffixes.
3. Classify each remaining path as supported code, document/text, or excluded.
4. Check its size against the source or document limit.
5. Read its bytes and reject content that looks binary.
6. Hash accepted content and include it in the tree.

The classification policy accepts unfamiliar text extensions as documents. It is not restricted to a short documentation allowlist. For example, SQL, GraphQL, protobuf, HTML, and infrastructure text can be useful even when no code-entity parser exists for them.

| Example | Typical default treatment |
| --- | --- |
| `src/checkout.py` | Python code entities. |
| `src/view.tsx` | TSX code entities. |
| `README.md` | Markdown document sections. |
| `package.json`, `settings.toml` | Document/configuration text. |
| A small UTF-8 file with an unfamiliar extension | Plain document text if no exclusion matches. |
| `node_modules/...`, `.venv/...`, `artifacts/...` | Entire directory skipped. |
| `.codelens/...` | Skipped so the graph never indexes itself. |
| Lock files/minified bundles matching skip rules | Skipped. |
| Known binary formats or binary-looking bytes | Skipped. |
| Paths matching secret-file rules | Skipped; this is not complete secret detection. |

Directory names are checked at every depth. The default name `artifacts` skips `src/artifacts/` as well as root `artifacts/`, but not a file named `artifacts.py`. Matching uses the configured name directly; it is not a wildcard or case-insensitive Git matcher.

## Limits and important caveats

The built-in source size limit is 1,000,000 bytes; the document limit is 100,000 bytes. Binary detection samples the first 8192 bytes for NULs and UTF-8 decoding behavior. It is a heuristic, not a guarantee that arbitrary files are safe or meaningful.

Secret checks examine repository-relative paths. Some example/template configuration names are deliberately accepted so setup documentation remains searchable. A real credential accidentally placed in an ordinary `.py`, `.md`, or accepted example file can still be sent to the embedding service. Review sensitive input rather than treating filename filters as a content redactor.

Unreadable files and inaccessible directories can be skipped during scanning. If a previously indexed path is no longer observed, it may look deleted. Stable permissions and a stable source tree matter when refreshing a graph.

The scanner does **not** implement arbitrary `.gitignore` rules. Managing the `.codelens/` entry protects generated data from Git; it is separate from CodeLens's own exclusion settings.

## Change the exclusions

```sh
codelens config show ignored_directories
codelens config update extra_ignored_directories
```

Enter a JSON list such as `["generated", "scratch"]`. These settings are shared user preferences. After changing them, run:

```sh
codelens embed --project demo --force
```

Previously indexed files that are now excluded are removed as missing from the current eligible tree. Related source entities/documents are deleted, and stale summaries/interfaces are cleaned up. `.codelens` must stay excluded.

## Inspect classification without model calls

From an installed Python environment, this snippet uses built-in defaults and does not open a graph or call OpenAI:

```python
from graph_rag.parsing.languages import classify

print(classify("src/checkout.py"))   # ('python', None)
print(classify("README.md"))         # (None, 'markdown')
print(classify("notes.custom"))      # (None, 'text')
```

`classify` handles file-level rules only. Directory exclusions, byte inspection, and size checks happen in the scanner. To understand a real run, combine its stage output with the current configuration; this snippet is not a complete dry-run CLI.

**Source:** [languages.py](../graph_rag/parsing/languages.py), [merkle.py](../graph_rag/change_detection/merkle.py), [test_exclusions.py](../tests/test_exclusions.py), [test_artifact_exclusion.py](../tests/test_artifact_exclusion.py).

[Previous](07-architecture.md) | [Guide](README.md) | [Next: Code parsing](09-parsing-code.md)
