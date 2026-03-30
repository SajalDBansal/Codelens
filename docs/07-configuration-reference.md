# 07 - Configuration reference

User preferences live in `~/.codelens/config.json`. The API key lives in the OS credential store. `graph_rag/config.py` supplies engine defaults, and the CLI applies the saved settings around one operation. You normally configure the installed application through `codelens config`, not by editing Python code.

## Read and change a setting

```sh
codelens config list
codelens config list --json
codelens config show
codelens config show default_top_k
codelens config update default_top_k
codelens config update default_top_k 8
codelens config update api_key
```

An interactive update contains the current value: type to append, Backspace to remove, Ctrl+U to clear, Enter to save, Ctrl+C to cancel. Arrays and objects use JSON syntax. For example, `extra_ignored_directories` can be `["generated", "scratch"]`. Boolean values are `true`/`false`, not quoted strings.

The API key is never displayed as a default or accepted as a positional value. Use hidden input, or pipe a key from a secret manager to `config set --api-key-stdin`. There is no environment-variable fallback. `schema_version`, `provider`, `credential_source`, and the configuration-file location are metadata, not editable engine controls.

## Precedence and persistence

Built-in defaults are used when creating the settings. Saved preferences apply to each command. Supported command options such as `--top-k` override the corresponding saved value for that invocation. Choosing `--on-model-change keep` uses the graph's saved model/dimensions for that operation without rewriting global preferences.

Updates are validated, locked, and atomically replace the JSON file. Invalid values leave the saved configuration unchanged. Concurrent edits to the same setting are detected; unrelated settings are preserved. These protections are local process coordination, not a distributed configuration service.

## Complete setting catalog

The table below is generated from the current `Settings` and `EngineOptions` defaults, not from anyone's private saved configuration. The CLI catalog is authoritative for your installed version. Long collection defaults follow the table in full.

| Setting | Built-in default | Meaning |
| --- | --- | --- |
| `embedding_model` | `text-embedding-3-small` | Model used to embed code and questions; changes require keep/rebuild. |
| `embedding_dimensions` | `1536` | Vector dimensions; changes require keep/rebuild. |
| `reasoning_model` | `gpt-4.1-nano` | Chat Completions model for answers and new folder summaries. |
| `api_key` | `not configured; stored in OS keyring` | Stored in the OS keyring; replace through hidden input, never displayed. |
| `max_file_size_bytes` | `1000000` | Largest source file to index, in bytes. |
| `max_doc_file_size_bytes` | `100000` | Largest documentation/config file to index, in bytes. |
| `max_doc_chunks_per_file` | `12` | Maximum chunks per structured document. |
| `max_prose_chunks_per_file` | `80` | Maximum chunks per prose document. |
| `extract_interfaces` | `true` | Extract shared routes, events, and interface references. |
| `max_interface_fanout` | `12` | Maximum entities linked through a shared interface. |
| `generate_summaries` | `true` | Generate folder summaries using the reasoning model. |
| `min_entities_for_summary` | `3` | Minimum entities before generating a folder summary. |
| `default_summary_top_k` | `2` | Folder summaries to retrieve; zero disables summary search. |
| `max_doc_chunk_chars` | `4000` | Maximum characters in a documentation chunk. |
| `embedding_batch_size` | `64` | Maximum items per embedding request. |
| `max_embed_chars` | `8000` | Maximum characters embedded for one code entity. |
| `default_top_k` | `5` | Number of initial code search results. |
| `default_hops` | `1` | Graph expansion depth: 1 or 2. |
| `max_context_chars` | `60000` | Maximum characters in retrieved answer context. |
| `default_doc_top_k` | `3` | Documents to retrieve; zero disables document search. |
| `max_mention_targets` | `3` | Maximum code targets for one document mention. |
| `max_name_candidates` | `200` | Maximum candidates considered when resolving a name. |
| `min_similarity` | `0.62` | Minimum rescaled cosine score: (1 + cosine) / 2; not raw cosine or a probability. |
| `max_block_chars` | `6000` | Maximum characters per retrieved code block. |
| `hybrid_vector_weight` | `1.0` | Weight of semantic matches in rank fusion. |
| `hybrid_keyword_weight` | `1.0` | Weight of keyword matches in rank fusion. |
| `hybrid_reserved_vector_slots` | `2` | Slots reserved for semantic matches. |
| `rrf_k` | `20` | Reciprocal rank fusion smoothing constant. |
| `module_keyword_penalty` | `0.35` | Keyword multiplier for module blocks, from 0 to 1. |
| `max_module_expansion` | `3` | Maximum symbols expanded from a matched module. |
| `relative_similarity_margin` | `0.08` | Allowed similarity gap below the best seed. |
| `neighbour_relevance_weight` | `1.0` | Weight of a graph neighbour's own question relevance. |
| `extra_ignored_directories` | `[]` | Additional excluded folder names, as a JSON array. |
| `ignored_directories` | `Full value below` | Excluded folder names; .codelens must remain excluded. |
| `ignored_directory_suffixes` | `[".egg-info", ".dist-info"]` | Excluded folder suffixes, as a JSON array. |
| `prose_doc_types` | `["markdown", "rst", "text"]` | Types using the prose chunk limit: markdown, rst, text. |
| `edge_type_weights` | `{"CALLS": 1.0, "EXTENDS": 0.9, "REFERENCES": 0.8, "USES_TYPE": 0.55, "CONTAINS": 0.6}` | Graph expansion weights for each supported relationship. |

**ignored_directories:**

```json
[
  ".angular",
  ".cache",
  ".codelens",
  ".dart_tool",
  ".expo",
  ".git",
  ".gradle",
  ".hg",
  ".idea",
  ".mypy_cache",
  ".next",
  ".nox",
  ".nuxt",
  ".nyc_output",
  ".parcel-cache",
  ".pytest_cache",
  ".ruff_cache",
  ".serverless",
  ".svelte-kit",
  ".svn",
  ".terraform",
  ".tox",
  ".turbo",
  ".venv",
  ".vscode",
  ".yarn",
  "DerivedData",
  "Pods",
  "__pycache__",
  "__snapshots__",
  "artifacts",
  "bower_components",
  "build",
  "coverage",
  "dist",
  "env",
  "htmlcov",
  "node_modules",
  "out",
  "site-packages",
  "target",
  "vendor",
  "venv"
]
```

## Important interpretations

`min_similarity` uses the storage score `(1 + cosine_similarity) / 2`, clamped to 0..1. The default 0.62 corresponds to raw cosine 0.24. It is not a probability. Keyword-only hits without a returned vector score are handled differently; see [retrieval](19-retrieval.md).

`max_context_chars`, `max_block_chars`, `max_embed_chars`, and document chunk limits are character-based safeguards, not exact tokenizer limits. Headers and truncation markers add text. The configured model's actual input limits still matter.

`hybrid_vector_weight` and `hybrid_keyword_weight` weight reciprocal-rank fusion. Reserved vector slots are handled separately: setting vector weight to zero does not disable vector requests or automatically remove reserved semantic hits.

`edge_type_weights` must retain the supported relationship keys. The general structural-neighbor ranking uses the traversed code-edge type. Shared-interface expansion has its own path and fallback rank; changing the `REFERENCES` setting alone does not turn off that expansion. To prevent new interface extraction use `extract_interfaces=false` and re-index affected content.

`ignored_directories` is the full exact folder-name list; `extra_ignored_directories` adds names. These are not path patterns or Git ignore rules. `.codelens` must remain excluded. `prose_doc_types` selects the larger chunk-count policy for Markdown, reStructuredText, and text.

## When a change takes effect

| Change | What to do |
| --- | --- |
| Retrieval counts, scoring, context budget | Ask again; no embedding rebuild needed. |
| Answer model | Future answers and newly generated summaries use it; existing summaries remain cached. |
| Embedding model or dimensions | Choose keep or rebuild at the next operation. Rebuild creates a new graph generation. |
| Parsing/chunking/exclusion settings | Run `codelens embed --project demo --force` to apply them to unchanged files too. |
| Database buffer/threads | The next database connection uses the new values. |
| Summary generation disabled | Re-index to apply it to changed content; to clear/reprocess unchanged folders too, force an embed. |

Example tuning: start with defaults, inspect `retrieve`, then change one setting at a time. Increase `default_top_k` for broader evidence, use two hops for a wider neighborhood, or reduce summary use with `default_summary_top_k=0`. Compare actual retrieved files before concluding a change improved quality.

Aliases such as `DEFAULT_TOP_K` and `engine.default_top_k` refer to `default_top_k`. `CHAT_MODEL` maps to `reasoning_model`; `EMBEDDING_DIM` maps to `embedding_dimensions`. Saved engine options appear under the JSON `engine` object even when the CLI accepts their shorter names.

**Source:** [settings.py](../graph_rag/cli/settings.py), [engine_options.py](../graph_rag/cli/engine_options.py), [config_commands.py](../graph_rag/cli/config_commands.py), [config.py](../graph_rag/config.py).

[Previous: CLI commands and projects](06-cli-commands-and-projects.md) | [Guide](README.md) | [Next: Python compatibility](08-compatibility.md)
