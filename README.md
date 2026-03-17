# CodeLens

**Ask questions about your codebase using its code, documentation, and relationships as evidence.**

CodeLens is a Python command-line tool that indexes a local project into a LadybugDB graph, finds relevant code and documentation, and uses OpenAI to generate answers with source citations. It helps you explore an unfamiliar repository, trace connected functions, and understand how parts of a project fit together.

The graph lives on your computer, inside the project's `.codelens` folder. You do not need Docker, Neo4j, or a database server. **Embedding, optional folder summaries, and answer generation use OpenAI; this is not a fully offline AI tool.**

```sh
codelens init
codelens embed "/path/to/my-project"
codelens ask "How does authentication work?" --project my-project
codelens retrieve "Where is the access token validated?" --project my-project
```

The commands above work after the [local installation](#install-and-run-locally). The Python distribution and terminal command are named `codelens`; the import package remains `graph_rag`. This checkout is a beta, and these instructions install your local source rather than assuming a public PyPI release exists.

## Learn the whole system

Start with the [documentation guide](docs/README.md) for a beginner reading path and 23 chapters covering every stage. The [setup guide](docs/04-setup-and-first-run.md) gets you running, the [end-to-end walkthrough](docs/22-end-to-end-walkthrough.md) follows the bundled checkout example, and the [configuration reference](docs/06-configuration-reference.md) explains every editable setting.

## What CodeLens does

- Parses source files into functions, classes, methods, constants, and module content.
- Indexes documentation and supported configuration files alongside the code.
- Records structural relationships, including containment, calls, imports, inheritance, type references, and documentation links where the parser and resolver support them.
- Searches by both meaning and keywords, then follows graph connections to collect related evidence.
- Generates answers with instructions to cite the retrieved file paths and line numbers. `--show-context` lets you inspect the supplied evidence.
- Detects source changes and reuses valid embeddings for unchanged content.
- Maintains a separate graph for each project and a shared registry so you can select projects from another directory.
- Lets you choose embedding and answer models separately and edit the saved configuration through the CLI.

The parser includes support for Python, JavaScript/JSX, TypeScript/TSX, Go, Rust, Java, C#, Ruby, PHP, Kotlin, Swift, Scala, C, C++, shell scripts, and Lua. Extraction and relationship coverage vary by language; this is static analysis, not a complete record of runtime behavior.

## What is RAG, and why use it for code?

**RAG means Retrieval-Augmented Generation.** Before a language model answers, a retrieval system finds relevant information and supplies it as context. The model then generates an answer using that context. The [original RAG paper](https://arxiv.org/abs/2005.11401) describes combining a language model with retrieved external knowledge.

A general model does not automatically know the files in your local project or the changes you made today. Pasting an entire repository into every question can exceed context limits and send a lot of irrelevant text. RAG selects a smaller set of useful evidence for each question and makes its source locations available for checking.

An **embedding** is a list of numbers representing a piece of text. Comparing embeddings helps find related content even when the question and code use different wording. Keyword search complements this by matching identifiers such as `validate_access_token` directly.

**Graph RAG adds connections between the retrieved pieces.** For example, a search may find `checkout()`, then the graph can bring in the `apply_discount()` and `add_tax()` functions it calls. This helps explain a flow that spans several files instead of describing one isolated fragment.

CodeLens does not train or fine-tune a model on your repository. It creates an index and supplies retrieved context when asking a model for an answer. Retrieval and generated answers can still be incomplete or wrong, so inspect the cited code for important decisions. RAG is useful when relevant information is spread across a project; ordinary text search is often sufficient when you already know an exact identifier.

## How the pipeline works

There are two connected workflows. The graph is built from parsing and reference resolution; embeddings make its content searchable by meaning.

```text
BUILD OR UPDATE A PROJECT
Local files -> exclusions + change detection -> parse code and documents
            -> save graph nodes and relationships
            -> embed changed content + generate optional folder summaries
            -> persist graph, vectors, source excerpts, and hashes

ANSWER A QUESTION
Check for source changes -> update the graph if needed -> embed the question
                        -> semantic + keyword search -> expand graph connections
                        -> rank and fit evidence into the context budget
                        -> ask OpenAI for an answer with source citations
```

The build operations can interleave; the diagram describes their responsibilities rather than a separate service for each box.

| Stage | What happens |
| --- | --- |
| Scan | Find eligible files, skipping configured folders, binary files, and other excluded content. |
| Detect changes | Compare file and folder hashes, including a Merkle-style folder tree, with the stored state. |
| Parse | Use Tree-sitter for code structure and document parsers for documentation sections. |
| Connect | Resolve relationships between code entities and documents. Unresolved references may remain. |
| Embed and summarize | Send embedding inputs to OpenAI, reuse valid existing vectors, and optionally generate folder overviews. |
| Store | Save the graph, vectors, text, and hashes in the project's LadybugDB file. |
| Retrieve | Combine vector similarity and BM25 keyword rankings, expand useful connections by one or two hops, and assemble bounded context. Folder summaries are searched separately by similarity. |
| Generate | Send the question and selected context to the configured answer model, with instructions to cite sources and acknowledge missing evidence. |

`embed` builds or updates the graph. Both `ask` and `retrieve` normally check for changes first. `retrieve` returns evidence without the final answer-generation call; it still calls OpenAI to embed the question, and an automatic update can also make embedding or summary calls.

## Install and run locally

Use **Python 3.12** for the recommended setup. The supported Python range is **3.11–3.13**, with Windows x64 compatibility checks. Python 3.14 is excluded because the pinned LadybugDB wheel failed to load with the tested standard Windows Python installation. See [supported versions and local verification](docs/compatibility-and-ci.md) for exact test results, dependency versions, and local validation instructions. GitHub Actions and CI/CD setup are deferred. Other platforms still need release verification.

You also need internet access to install dependencies and use OpenAI, an OpenAI API key with access to your selected models, and a working OS credential store. Indexing and questions can incur API charges. The package reads local folders; clone or download a repository yourself before embedding it.

Clone this package and enter its directory, or open your existing checkout:

```sh
git clone https://github.com/SajalDBansal/codelens.git
cd codelens
```

### Windows PowerShell

Open a terminal in the directory containing this package's `pyproject.toml`:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\codelens.exe --help
.\.venv\Scripts\codelens.exe init
```

For an existing checkout, first use `cd "C:\path with spaces\codelens"` with your actual path. Calling the executables directly works without changing PowerShell's script execution policy. To use the shorter command in the same terminal, set a session alias:

```powershell
Set-Alias -Name codelens -Value (Join-Path (Get-Location).Path '.venv\Scripts\codelens.exe')
codelens --help
```

You can now change to another project directory and run `codelens` there. The alias lasts for the current PowerShell session. For a command that also works in new terminals, follow [Use `codelens` from any directory](#use-codelens-from-any-directory).

### Windows Git Bash

From the package directory, using a Python 3.12 interpreter:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -e .
codelens init
```

Alternatively, point an alias at an existing installation. Quote the executable path inside the alias so spaces are preserved:

```bash
alias codelens='"/c/Code Projects/codelens/.venv/Scripts/codelens.exe"'
```

Replace that example path with your package location. The alias lasts for the current Git Bash session.

### macOS or Linux shells

The equivalent installation commands are below. Native dependency installation and a working keyring backend must be verified on your OS; headless Linux environments may need credential-store setup.

```bash
cd /path/to/codelens
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
codelens init
```

The `-e` option makes this an editable installation: changes to the package source are used without reinstalling. Use `python -m pip install .` for a regular source installation. Keep the virtual environment active, or invoke its executable by absolute path, when working in other directories.

For a persistent command outside an activated environment, use one of the options in [Use `codelens` from any directory](#use-codelens-from-any-directory).

If this same environment contains the earlier distribution named `graph-rag-reviewer`, uninstall it before installing the renamed package:

```sh
python -m pip uninstall graph-rag-reviewer
python -m pip install -e .
```

This removes the old Python distribution; it does not delete `~/.codelens` settings or your projects' graph folders. The existing `graph_rag` import and legacy command aliases remain supported.

### Use `codelens` from any directory

Make the installed command available on your **user PATH** so you can type `codelens` from any project folder, including in new terminals, without activating a virtual environment. Choose one option below. These setups apply to your user account; other accounts need their own setup.

Keep the CodeLens checkout and its environment at their installed locations. An editable installation uses that checkout, and a symlink points to the existing command. If you move or delete them, reinstall and recreate the link.

#### Option 1: Install as a tool with uv

If you have [uv installed](https://docs.astral.sh/uv/getting-started/installation/), run these commands from the CodeLens repository directory on Windows, macOS, or Linux:

```sh
uv tool install --python 3.12 --editable .
uv tool update-shell
```

uv creates a dedicated environment and exposes the package's commands in its tool executable directory. `uv tool update-shell` adds that directory to PATH where supported; follow any instructions it prints, then reopen your terminal. See [uv's tool documentation](https://docs.astral.sh/uv/concepts/tools/). This option handles command installation without a manual symlink.

#### Option 2: Link an existing Windows installation

After installing into `.venv` with the PowerShell instructions above, run the following **from the CodeLens repository directory**. It links only `codelens.exe` into a dedicated user bin folder and adds that folder to your user PATH.

In Windows PowerShell 5.1, create the link from **Run as administrator**, using the same Windows account. Recent PowerShell versions can also create it without elevation when Windows Developer Mode is enabled. See Microsoft's [symbolic link requirements](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.management/new-item#-itemtype). If that is unavailable, use the uv option above.

```powershell
$codelensExe = (Resolve-Path -LiteralPath '.venv\Scripts\codelens.exe' -ErrorAction Stop).Path
$codelensBin = Join-Path $env:LOCALAPPDATA 'CodeLens\bin'
New-Item -ItemType Directory -Path $codelensBin -Force | Out-Null
New-Item -ItemType SymbolicLink -Path (Join-Path $codelensBin 'codelens.exe') -Target $codelensExe -ErrorAction Stop | Out-Null

# Persist for future terminals, preserving the existing user PATH.
$codelensUserPath = [Environment]::GetEnvironmentVariable('Path', 'User')
if (($codelensUserPath -split ';') -notcontains $codelensBin) {
    $codelensNewPath = (@($codelensUserPath, $codelensBin) | Where-Object { $_ }) -join ';'
    [Environment]::SetEnvironmentVariable('Path', $codelensNewPath, 'User')
}

# Also make it available in this terminal.
$env:Path = "$codelensBin;$env:Path"
```

The link command stops if `codelens.exe` already exists at the destination. Inspect an existing link before replacing it. Open a new terminal after setup; restart VS Code or Windows Terminal if its terminals still inherit the old PATH.

Verify from another folder in PowerShell:

```powershell
Set-Location $env:TEMP
Get-Command codelens -All
codelens --help
```

The executable path should include `CodeLens\bin\codelens.exe`. If the earlier session alias appears first, remove that alias with `Remove-Item Alias:codelens` or use a fresh terminal. The user PATH also makes the command available in new Command Prompt and Git Bash sessions.

#### Option 3: Create a symlink on macOS or Linux

After installing into `.venv`, run this from the CodeLens repository directory in Bash or Zsh:

```sh
codelens_repo="$(pwd -P)"
mkdir -p "$HOME/.local/bin"
ln -s "$codelens_repo/.venv/bin/codelens" "$HOME/.local/bin/codelens"
export PATH="$HOME/.local/bin:$PATH"
```

This uses an absolute [symbolic link](https://www.gnu.org/software/coreutils/manual/html_node/ln-invocation.html), so changing the working directory does not change its target. If the destination already exists, inspect it before replacing it. Add `export PATH="$HOME/.local/bin:$PATH"` to `~/.bashrc` for Bash or `~/.zshrc` for Zsh if that directory is not already on PATH. For Bash login shells, ensure your login profile loads `~/.bashrc` or add the same PATH line there. Reopen the terminal, then check from another directory:

```sh
cd /tmp
command -v codelens
codelens --help
```

After any option, change to the project you want to index and run `codelens embed .`, or pass its path explicitly: `codelens embed "/path/to/my-project"`. Run `codelens init` once first if you have not configured your API key and models.

### Configure once

`codelens init` asks for your OpenAI API key using hidden input, lets you select an embedding model, then lets you select an answer/reasoning model. It saves the recommended settings to `~/.codelens/config.json`, outside the installed package.

The API key is stored separately in the OS credential store, such as Windows Credential Manager. It is not saved in the JSON configuration. CodeLens does not load `.env` files or application settings from environment variables.

The built-in defaults are `text-embedding-3-small` with 1536 dimensions and `gpt-4.1-nano` for answers and optional folder summaries. These are package defaults, not a guarantee of model availability for every account. Use the built-in lists or choose a compatible text Chat Completions answer model.

```sh
codelens models
codelens config list
codelens doctor
codelens config check
```

`doctor` checks local state without network requests. `config check` makes an authenticated model-list request without sending repository content; it checks credentials and listed model IDs, not embedding/completion behavior. `init` itself does not make an OpenAI request. If configuration is missing, the first `embed`, `ask`, or `retrieve` can launch setup in an interactive terminal.

## Try the complete workflow

For a first run, use the small demo included in this source checkout. From the package directory, after installation and initialization:

```sh
codelens embed examples/smoke_repo
codelens status --project smoke_repo
codelens retrieve "Where is the checkout total calculated?" --project smoke_repo
codelens ask "How does checkout apply discounts and tax?" --project smoke_repo --show-context
```

These commands use your configured OpenAI models. The demo should expose checkout, discount, and tax functions; the exact generated answer depends on the model and retrieved context.

To use your own project, register it with a clear name and embed it:

```sh
codelens projects add "/path/to/my-project" --name my-project
codelens embed --project my-project
codelens ask "Explain the main entry point" --project my-project
codelens retrieve "Where are database connections opened?" --project my-project
```

`projects add` registers a location without calling OpenAI. You can instead run `codelens embed "/path/to/my-project"` directly; the default project name comes from its directory. `embed` without a path or `--project` uses the current directory.

Project initialization adds `.codelens/` to the root `.gitignore`, creating that file if necessary and preserving an existing recognized entry. This happens during registration and before indexing. `codelens init` only configures the user; it does not initialize the current project. Ignore rules do not untrack previously committed files.

After editing a source file, ask another question: the CLI checks for changes and updates the graph before retrieving evidence. An unsuccessful update stops the question so it can be retried. To query the stored version intentionally:

```sh
codelens retrieve "Where is checkout?" --project my-project --no-update
```

`--no-update` skips the source check but cannot bypass a recorded incomplete update or an embedding-model compatibility check.

## Work with multiple projects

```sh
codelens projects list
codelens projects list checkout
codelens projects use my-project
codelens ask "Explain authentication" --project my-project
codelens embed --project my-project
codelens projects show my-project
codelens projects remove my-project
```

Questions select the explicit `--project` first, then a CodeLens project in the current directory or its ancestors, then the selected default. `projects use` sets the default used outside another project. Projects can be selected by name, full UUID, unique UUID prefix, or registered path; ambiguous names require an ID.

Each project has its own graph. A question queries one selected project; CodeLens does not merge several projects' graphs into one answer. `projects remove` only unregisters a location; it leaves its graph and metadata on disk. Missing source locations produce an error rather than silently associating a graph with another folder.

## View and edit configuration

```sh
codelens config list
codelens config show
codelens config show default_top_k
codelens config update default_top_k
codelens config update default_top_k 8
codelens config update generate_summaries false
codelens config update extra_ignored_directories
codelens config update api_key
```

`config update NAME` opens an input containing its current value. Edit it with Backspace or typing, use Ctrl+U to clear it, Enter to validate and save, or Ctrl+C to cancel. With no name, `config update` helps you select a setting. API keys use hidden replacement input and are never shown as editable defaults.

Configuration covers models, embedding dimensions and batches, database memory and threads, file/chunk limits, retrieval counts and depth, similarity and ranking weights, summaries, interface extraction, and file exclusions. Numbers, booleans, arrays, and objects are validated before saving. For example, the interactive `extra_ignored_directories` input accepts `["generated", "scratch"]`.

`ignored_directories` controls the full folder-name list; `extra_ignored_directories` adds to it. `.codelens` must remain excluded. Common generated/dependency directories, including `node_modules`, `.venv`, and `artifacts`, are excluded by the built-in defaults. The scanner uses CodeLens exclusions; adding its own `.gitignore` entry does not mean it applies all Git ignore rules.

`config list --json` returns the complete settings catalog. The names `default_top_k`, `engine.default_top_k`, and `DEFAULT_TOP_K` refer to the same engine option. `CHAT_MODEL` maps to `reasoning_model`, and `EMBEDDING_DIM` maps to `embedding_dimensions`. Schema/provider/credential-source metadata is read-only; the provider is OpenAI.

Settings changes are locked and written atomically. Invalid inputs do not overwrite saved values. Retrieval settings apply to the next question. After changing parsing, chunking, or exclusions, run `codelens embed --project my-project --force` to apply the settings to unchanged files too.

For scripts, bulk updates remain available:

```sh
codelens config set --option default_top_k=8 --option default_hops=2
codelens config set --embedding-model text-embedding-3-small --dimensions 512
```

Use `config set --api-key` for hidden credential input or pipe a key from your secret manager to `config set --api-key-stdin`. API keys are not accepted as positional arguments. A custom configuration directory can be selected explicitly with `codelens --config-dir "/path/to/config" init`; each directory uses a separate credential-store account.

### Change models safely

Changing only the answer/reasoning model does not require rebuilding embeddings:

```sh
codelens config update reasoning_model gpt-4.1-mini
```

The new model is used for future answers and newly generated folder summaries. Existing summaries remain cached until normal regeneration conditions apply.

Changing the embedding model **or its dimensions** makes the saved graph incompatible with the new profile. The CLI offers to keep the graph's original profile for this command, rebuild with the new profile, or cancel. Scripts must choose explicitly:

```sh
codelens ask "Explain checkout" --project my-project --on-model-change keep
codelens embed --project my-project --on-model-change rebuild
```

A rebuild creates a separate database, switches the project manifest only after success, and retains the previous graph. Failed replacements can resume with the same target profile. Ordinary incremental updates modify the active graph and must finish before answering. `--force` does not bypass model checks; equal vector dimensions do not make different embedding models interchangeable.

## Command reference

| Command | Purpose |
| --- | --- |
| `codelens init` | Set up the API key, models, and recommended settings. |
| `codelens config` | Show configuration help and the file location. |
| `codelens config list` | List editable settings and their purposes. |
| `codelens config show [NAME]` | Show full settings or one value without exposing the key. |
| `codelens config update [NAME] [VALUE]` | Edit interactively or assign a value. |
| `codelens config check` | Check credentials and model IDs online. |
| `codelens models [--available]` | Show built-in model choices or account-visible model IDs. |
| `codelens embed [PATH]` | Build or update a graph. |
| `codelens ask QUESTION` | Update, retrieve, and generate an answer. |
| `codelens retrieve QUESTION` | Update and display retrieved evidence. |
| `codelens projects list/add/use/show/remove` | Register, select, find, or unregister projects. |
| `codelens status [--project NAME]` | Inspect graph metadata, counts, and model history. |
| `codelens doctor` | Check local settings, credential availability, and registered paths. |

Use `codelens COMMAND --help` for options. Question options include `--project`, `--top-k`, `--hops` (1 or 2), `--doc-top-k`, `--show-context`, and `--no-update`. `ask --no-answer` also performs retrieval without generating an answer. `python -m graph_rag` is equivalent to `codelens` in the installed environment.

`graph-rag-index` and `graph-rag-ask` remain compatibility aliases. To adopt a graph created before project manifests existed, use `codelens projects add "/path/to/repo" --db "/path/to/repo/.codelens/existing.lbug"`. Its stored source location and model metadata must match; an existing manifest controls the active graph path.

## Storage and data flow

```text
~/.codelens/                         # Windows: %USERPROFILE%\.codelens
    config.json                     # models, dimensions, validated engine options
    projects.json                   # project names, IDs, locations, selected default
    .state.lock                     # coordinates shared metadata updates

<project>/.codelens/
    project.json                    # source location, active database, model history,
                                    # status, counts, timestamps, pending/previous graphs
    project.lock                    # one CLI operation on this project at a time
    graph.lbug                      # graph, vectors, source text, documents, hashes
    graphs/<generation>/graph.lbug  # replacement after an embedding-profile change

OS credential store                 # the OpenAI API key, outside the JSON files
```

The shared registry does not contain source code or embeddings. Graph files do contain source text and should be treated as part of the project's data. Keep the graph and its manifest together. Old graph generations are retained; automatic pruning is not implemented.

During indexing, eligible code/document chunks are sent to OpenAI for embeddings; optional summaries also send folder information. During a question, the question is embedded and, for `ask`, selected evidence and the question are sent for answer generation. Local graph storage does not mean repository content stays entirely offline. Filename-based exclusions are not a complete secret detector; check which content is eligible before indexing sensitive projects. CodeLens does not execute the repository's scripts or modify its source code, but it writes `.codelens` data and manages the root `.gitignore` entry.

## Development and checks

From the source checkout, using its Python environment:

```sh
python -m pip install -e ".[dev]"
python -m pytest -q
python scripts/smoke_local.py
```

The tests use temporary local databases and model doubles. The smoke script exercises real parsing, Ladybug persistence, incremental reuse, graph traversal, and retrieval with deterministic test embeddings and summaries; it does not call OpenAI or assess semantic answer quality. Parser installation or first use may still require dependency/grammar downloads on a clean machine.

For retrieval evaluation against an already embedded project:

```sh
python evals/run_eval.py --project my-project --questions evals/questions.demo.json
```

Use questions and expected identifiers that match your project. The evaluation reads the saved snapshot without automatically indexing source changes and uses the configured embedding service.

The main modules follow the workflow:

| Module | Responsibility |
| --- | --- |
| `graph_rag/cli/` | Typer commands, user settings, keyring access, project registry, and recovery. |
| `graph_rag/change_detection/` | File/folder hashing and incremental change detection. |
| `graph_rag/parsing/` | Code parsing, documentation chunks, and supported-language definitions. |
| `graph_rag/embedding/` | OpenAI embedding batches and query embeddings. |
| `graph_rag/storage/` | Local LadybugDB storage and search operations. |
| `graph_rag/pipeline/` | Indexing orchestration, folder summaries, and progress reporting. |
| `graph_rag/retrieval/` | Hybrid retrieval, graph expansion, ranking, and context assembly. |
| `graph_rag/generation/` | Answer prompts, model options, and answer generation. |

`graph_rag/config.py` holds engine defaults. The CLI applies the saved user settings for each command; changing that source file is not the normal way to configure an installed package. Synchronous library callers can use `engine_settings(require_settings())` from `graph_rag.cli.settings`, optionally within `config_directory(path)` from `graph_rag.cli.state`.

## Current limits and troubleshooting

- **Command not found:** activate the environment where you installed CodeLens, use its executable's full path, or use `python -m graph_rag` with that environment's Python. In Git Bash, quote paths containing spaces as shown above.
- **Credential-store error:** use `codelens doctor` and configure a supported OS keyring backend, then run `codelens init`. There is no `.env` fallback.
- **Model access error:** inspect `codelens models --available` and `codelens config check`. A listed model ID alone does not establish endpoint compatibility.
- **Project ambiguity or missing source:** use `projects list` and select an unambiguous ID or correct registered path. Graphs retain their source location for update checks.
- **Incomplete answers:** inspect `retrieve` or `ask --show-context`, ask a more specific question, or adjust retrieval settings. Static graph links and generated citations are not proofs of runtime behavior.
- **Progress and history:** the CLI prints indexing progress and completed answers. Token-by-token answer streaming, a web graph viewer, persistent conversation history, and automatic graph pruning are not implemented.

This beta is a local developer tool. The current release verification covers Windows/Python 3.12 and small demo graphs; it is not a large-repository performance guarantee.

## License and project links

CodeLens is licensed under the MIT License. See [LICENSE](https://github.com/SajalDBansal/codelens/blob/HEAD/LICENSE) for the full terms; the license is also included in source and wheel distributions. Dependencies retain their own licenses.

- Author: [SajalDBansal](https://github.com/SajalDBansal).
- Source and documentation: [SajalDBansal/codelens](https://github.com/SajalDBansal/codelens).
- Bug reports and feature requests: [GitHub issues](https://github.com/SajalDBansal/codelens/issues).
