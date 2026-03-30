# 05 - Setup and first run

This guide takes you from the current source checkout to your first answer.
Use Python 3.14 for installation without a compiler.
[Python 3.15 requirements](08-compatibility.md#python-315-installation-requirements)
are documented separately.

## 1. Install

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then open a
terminal in the CodeLens source folder, where `pyproject.toml` lives:

```sh
uv tool install --python 3.14 .
uv tool update-shell
```

Reopen your terminal and run `codelens --help`. You should see commands including
`init`, `embed`, `ask`, and `retrieve`. You can now use them from any folder.
See [installation options](04-install-upgrade-uninstall.md) for downloading the
source, pip instructions, upgrades, and uninstalling.

## 2. Configure once

```sh
codelens init
```

Enter your OpenAI API key using the hidden input. Choose an embedding model for
search and an answer model for generated text; the suggested choices are a good
starting point. `init` saves your choices and the key without calling OpenAI.

Settings are saved in `~/.codelens/config.json` (`%USERPROFILE%\.codelens` on
Windows). The key is stored separately in your OS credential store. You do not
need a `.env` file. Headless Linux requires a working keyring backend.

You can check setup at any time:

```sh
codelens doctor
codelens config check
```

`doctor` checks local settings and paths. `config check` contacts OpenAI to check
your key and selected model IDs without sending repository content. It does not
test a complete embedding or answer request.

## 3. Index a project

Use a repository already on your computer. Replace the path below with its folder
(`C:/Projects/my-app` is a Windows example):

```sh
cd "/path/to/my-app"
codelens embed .
```

Wait for indexing to finish. CodeLens registers the project, builds its searchable
index, and reports what it processed. It saves the index in `.codelens` and adds
that folder to the project's `.gitignore`. The default project name is `my-app`
in this example.

Indexing sends eligible code and documentation to OpenAI for embeddings and
optional summaries. These requests use your API account and can incur charges.

## 4. Ask a question

In the same project folder:

```sh
codelens ask "Explain the main entry point"
```

The answer includes source references you can check. To see the evidence supplied
to the model, add `--show-context`. To search without generating an answer:

```sh
codelens retrieve "Where is authentication checked?"
```

Both commands embed your question through OpenAI. They also check for source
changes and update the index when needed. You do not need to rerun `init` or
manually rebuild the index before every question.

## 5. Come back later

Open a terminal in the same project and run `codelens ask` again. If you are
working from another folder, select the registered project explicitly:

```sh
codelens projects list
codelens ask "Explain the main entry point" --project my-app
```

Use the name or ID shown by `projects list`. After a failed update, fix the
reported issue and rerun `codelens embed .` from that project. Use `--no-update`
only when you intentionally want the last indexed version.

Try the bundled [checkout example](26-end-to-end-walkthrough.md), or continue to
the [command reference](06-cli-commands-and-projects.md). Developers can find an editable
installation in the [installation guide](04-install-upgrade-uninstall.md#development-installation).

[Previous: Install, upgrade, and uninstall](04-install-upgrade-uninstall.md) | [Guide](README.md) | [Next: CLI commands and projects](06-cli-commands-and-projects.md)
