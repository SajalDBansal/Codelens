# CodeLens

**Ask questions about your codebase and get answers with file and line references.**

CodeLens searches your code and documentation, follows connections between functions,
and gives the relevant context to an OpenAI model. Its search index stays in your
project's `.codelens` folder. No database server or Docker is needed.

You need an OpenAI API key and internet access. Indexing sends eligible code and
documentation to OpenAI; questions also use the API and can incur charges.

## Get started

Have [uv installed](https://docs.astral.sh/uv/getting-started/installation/),
your OpenAI API key, and a project folder on your computer.

### 1. Install CodeLens

Open a terminal in the **CodeLens source folder**, where `pyproject.toml` lives:

```sh
uv tool install --python 3.14 .
uv tool update-shell
```

Close and reopen your terminal, then check the installation:

```sh
codelens --help
```

uv manages an isolated installation and can download Python 3.14 if needed. Once
setup is complete, `codelens` works from any folder without activating an environment.
See the [installation guide](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/04-install-upgrade-uninstall.md) for getting the
source, installing uv, or using pip instead.

Python **3.14** is the simplest setup. Python **3.15** is also verified, but its
parser dependency needs a compiler; Windows requires C++ Build Tools and a Windows
SDK. See [Python compatibility](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/08-compatibility.md).

### 2. Set up your API key

```sh
codelens init
```

Enter your OpenAI API key and follow the prompts to choose an embedding model and
an answer model. Keep the suggested models if you are unsure. You only need to do
this once. The key goes in your OS credential store; no `.env` file is needed.

### 3. Index your project and ask a question

Change to the **project you want to understand**. Replace the example path with
your project folder, such as `C:/Projects/my-app` on Windows:

```sh
cd "/path/to/my-app"
codelens embed .
codelens ask "How does authentication work?"
```

Wait for the first index to finish, then read the answer and its source references.
After editing your code, just ask another question: CodeLens checks for changes
and updates the index automatically, reusing unchanged embeddings.

For a guided example, see [Setup and first run](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/05-setup-and-first-run.md).

## Everyday commands

Run these inside your indexed project:

| What you want | Command |
| --- | --- |
| Ask a question | `codelens ask "Explain the main entry point"` |
| Inspect the evidence used for an answer | `codelens ask "How does checkout work?" --show-context` |
| Search without generating an answer | `codelens retrieve "Where is the access token checked?"` |
| Update the index yourself | `codelens embed .` |
| Check the project | `codelens status` |
| Check your local setup | `codelens doctor` |

`retrieve` still uses OpenAI to embed the question. It skips the final answer call.

To work from another folder, list your registered projects and choose one by name
or ID:

```sh
codelens projects list
codelens ask "Explain the main entry point" --project my-app
```

The default project name is its folder name. Use the ID shown by `projects list`
if two projects have the same name. See the [command reference](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/06-cli-commands-and-projects.md)
for more options and `codelens COMMAND --help` for help with a command.

## Your data

- Each project's `.codelens` folder contains its graph, embeddings, and compressed
  source snapshots. CodeLens adds this folder to the project's `.gitignore`.
- Settings and the project list live in `~/.codelens`; the API key is stored separately
  in the OS credential store.
- Back up the entire project `.codelens` folder while CodeLens is stopped.
  Uninstalling the package leaves your indexes and settings in place.

See [local storage and maintenance](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/14-local-storage-and-maintenance.md)
for backups, source snapshots, and reclaiming unused index space.

## Need help?

- **`codelens` is not found:** run `uv tool update-shell`, then reopen your terminal
  or restart your IDE terminal host.
- **API key or credential-store error:** run `codelens doctor`, then `codelens init`.
  Headless Linux may need an OS keyring configured first.
- **An answer misses something:** run `codelens retrieve` with a specific question
  and inspect the evidence. Static code analysis and generated answers can be incomplete.

More help: [Troubleshooting](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/23-troubleshooting.md) ·
[Install, upgrade, and uninstall](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/04-install-upgrade-uninstall.md) ·
[Configuration](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/07-configuration-reference.md).

## Learn more

- [Documentation guide](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/README.md): RAG concepts, supported files, and the pipeline.
- [Python compatibility](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/08-compatibility.md): tested versions and installation requirements.
- [Local storage](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/14-local-storage-and-maintenance.md): files, backups, and maintenance.
- [Performance measurement](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/22-storage-benchmark.md): benchmark commands and their limits.
- [Contributing](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/27-contributor-guide.md) and [testing](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/21-testing.md): development setup and checks.
- [Changelog](https://github.com/SajalDBansal/codelens/blob/HEAD/CHANGELOG.md) · [License](https://github.com/SajalDBansal/codelens/blob/HEAD/LICENSE).

Found a problem? [Open an issue](https://github.com/SajalDBansal/codelens/issues).
