# 04 - Setup and first run

CodeLens is available on PyPI as [codelens-rag](https://pypi.org/project/codelens-rag/).
Install that package, then run the `codelens` command. Use Windows x64 with Python
3.11–3.13; Python 3.12 is recommended. See [compatibility](compatibility.md) for
tested versions and platform limits. Installation and model requests need internet
access, and model requests require your own OpenAI key.

## 1. Install the package

Open PowerShell in a folder where you want to keep the Python environment:

```powershell
py -3.12 -m venv .venv-codelens
.\.venv-codelens\Scripts\python.exe -m pip install codelens-rag
.\.venv-codelens\Scripts\codelens.exe --help
Set-Alias -Name codelens -Value (Join-Path (Get-Location).Path '.venv-codelens\Scripts\codelens.exe')
```

A virtual environment keeps these dependencies separate from other Python projects.
The alias works in this terminal, including after you change folders. Direct
executable calls do not require activating PowerShell scripts. For a command that
also works in new terminals, see [installation options](install-upgrade-uninstall.md).

If this environment already contains an earlier development copy of this project
named `codelens` or `graph-rag-reviewer`, remove that old distribution first. It
shares Python modules with `codelens-rag`. Your saved settings and graphs remain.

## 2. Configure once

```sh
codelens init
```

The prompts ask for your API key using hidden input, then an embedding model and
an answer/reasoning model. Settings go in `~/.codelens/config.json`; Windows uses
`%USERPROFILE%\.codelens`. The key goes in the OS credential store, outside JSON.

`init` does not contact OpenAI or initialize the current folder as a project.
Running it again preserves advanced settings. Cancelling before completion leaves
unsaved changes unapplied. No `.env` file is needed.

```sh
codelens doctor
codelens config list
codelens config check
```

`doctor` checks local configuration and paths. `config check` makes an authenticated
model-list request without sending repository content. It checks credentials and
listed model IDs, not a complete embedding or answer request.

## 3. Index a project and ask a question

Use a repository already present on your computer. Replace the example path:

```sh
codelens projects add "C:\Projects\my-app" --name my-app
codelens embed --project my-app
codelens status --project my-app
codelens retrieve "Where is authentication checked?" --project my-app
codelens ask "Explain the main entry point" --project my-app --show-context
```

`projects add` registers a location without model requests. Project initialization
adds `.codelens/` to the root `.gitignore`, creating the file if needed. The graph
and project metadata are stored under that project's `.codelens` directory.
Ignore rules do not untrack files already committed to Git.

`embed`, `retrieve`, and `ask` use your OpenAI account and can incur charges.
`retrieve` shows evidence; `ask` also generates an answer. Check the answer against
the cited source and use `--show-context` to inspect the supplied evidence.

## 4. Work with later source changes

Repeating `embed` on unchanged input reuses valid stored work. By default, `ask`
and `retrieve` check the source for changes and update the graph before searching.
If an update fails, fix the reported problem and retry.

Use `--no-update` only when you intentionally want to query the stored version.
Switch among registered projects with `--project`; list them with `codelens projects list`.

## Optional: install the source for development

The source checkout includes the tests, pipeline verification scripts, and demo
project. These supporting files are not installed as part of the runtime wheel.

```sh
git clone https://github.com/SajalDBansal/codelens.git
cd codelens
```

In PowerShell from that checkout:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\codelens.exe --help
Set-Alias -Name codelens -Value (Join-Path (Get-Location).Path '.venv\Scripts\codelens.exe')
```

`-e` makes the installation editable, so changes to the source take effect without
reinstalling. Use this environment's Python and CLI when testing changes. Follow
[Testing](18-testing.md) for checks without OpenAI requests, or the
[end-to-end walkthrough](22-end-to-end-walkthrough.md) to try the bundled demo with
your configured models. Other shell examples are in the [README](../README.md).

**Source:** [config commands](../graph_rag/cli/config_commands.py),
[settings](../graph_rag/cli/settings.py), [project workflow](../graph_rag/cli/workflow.py).

[Previous](03-what-this-product-does.md) | [Guide](README.md) | [Next: Commands](05-the-two-commands.md)
