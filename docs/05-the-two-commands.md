# 05 - CLI commands and project selection

The public interface is the `codelens` command family. This chapter keeps its old filename so existing links work; the application now has more than two commands.

## Command map

| Command | Purpose | Model/network work |
| --- | --- | --- |
| `init` | Save the API key and recommended configuration. | No OpenAI request. |
| `config list/show/update` | Discover, inspect, or edit settings. | Local only. |
| `config check` | Check credentials and selected model IDs. | OpenAI model-list request. |
| `models` | Show built-in model choices. | Local; `--available` requests account model IDs. |
| `projects add/list/use/show/remove` | Manage registered local projects. | No OpenAI request. |
| `embed [PATH]` | Build or update the selected project graph. | Embeddings and optional summaries when needed. |
| `retrieve QUESTION` | Update, search, expand, and show evidence. | Query embedding; updates may make additional calls. |
| `ask QUESTION` | Update, retrieve, then generate an answer. | Same as retrieve, plus answer generation when evidence is available. |
| `status` | Inspect project metadata, model profile, and counts. | Local graph access; no source freshness scan. |
| `doctor` | Check saved settings, credential availability, and registered paths. | Local only. |

Run `codelens --help` or `codelens COMMAND --help` for exact syntax. `python -m graph_rag` is the same CLI when used with the installed interpreter.

## Embed

```sh
codelens embed "/path/to/project"
codelens embed --project demo
codelens embed --project demo --force
```

Without a path or `--project`, embed uses the current directory, not the selected default project. A path and `--project` are alternatives. `--force` reparses/re-embeds eligible content while still detecting deletions; it does not bypass an embedding-profile mismatch. `--quiet` reduces indexing detail, not all output or work.

## Ask and retrieve

```sh
codelens ask "How is tax calculated?" --project demo
codelens retrieve "add_tax" --project demo
codelens ask "What calls add_tax?" --project demo --hops 2 --show-context
codelens ask "Explain checkout" --project demo --no-answer
```

`retrieve` and `ask --no-answer` omit answer generation. `retrieve` displays the context automatically; `--show-context` makes it visible during ask. They still embed the question. Every question is independent: previous turns are not stored or sent as chat history.

| Option | Meaning |
| --- | --- |
| `--top-k N` | Override the initial code seed count; default 5. |
| `--doc-top-k N` | Override document seed count; default 3. Zero disables document retrieval, not folder summaries. |
| `--hops 1` / `--hops 2` | Set the code-neighbor traversal depth. |
| `--no-update` | Query the stored version without a fresh source scan. |
| `--on-model-change keep` | Use the graph's existing embedding profile for this invocation. |
| `--on-model-change rebuild` | Build a replacement with the configured embedding profile. |
| `--on-model-change error` | Stop if the profile differs. |

`--no-update` still requires the registered source folder to exist. It cannot be combined with a model rebuild and will not bypass an incomplete update marked `failed` or `indexing`.

## How the selected project is found

For questions and status, resolution is: explicit `--project`, then a manifest in the current directory or an ancestor, then the selected default. `projects use` sets the default used outside another project. A project can be addressed by name, full UUID, unique UUID prefix, or registered path. Duplicate names require an unambiguous ID.

```sh
codelens projects add "/path/to/shop" --name shop
codelens projects add "/path/to/blog" --name blog
codelens projects list
codelens projects use shop
codelens ask "Explain routing" --project blog
codelens projects show shop
codelens projects remove blog
```

Each project has its own database file. `remove` only unregisters it; it does not delete `.codelens`. A later command inside that project can register it again. The registry is a locator, not a shared graph.

## Settings and compatibility

```sh
codelens config show default_top_k
codelens config update default_top_k
codelens config update default_top_k 8
codelens --config-dir "/path/to/separate-config" init
```

The interactive update starts with the saved value in the input. Enter validates/saves; Ctrl+C cancels. Secrets use hidden replacement input. A custom configuration directory has its own registry and keyring account.

`graph-rag-index` and `graph-rag-ask` are compatibility aliases for embed and ask. `--db` supports adoption of older graph files, but must agree with an existing project's active path; prefer `--project` for everyday use. All entry points use saved configuration and model checks.

**Source:** [app.py](../graph_rag/cli/app.py), [projects.py](../graph_rag/cli/projects.py), [workflow.py](../graph_rag/cli/workflow.py).

[Previous](04-setup-and-first-run.md) | [Guide](README.md) | [Next: Configuration](06-configuration-reference.md)
