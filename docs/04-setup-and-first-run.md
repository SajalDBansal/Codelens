# 04 - Setup and first run

Use Python 3.12 for the recommended Windows setup. CodeLens supports Python 3.11–3.13; Python 3.14 is excluded because of a native LadybugDB loading failure with the tested standard Windows interpreter. See [supported versions and local verification](compatibility-and-ci.md) for the tested dependency baseline and verification record. Other platforms still need release verification. You need internet access for installation and model requests, an OpenAI API key, and a working OS credential-store backend.

## 1. Open the package source

```sh
git clone https://github.com/SajalDBansal/codelens.git
cd codelens
```

If you already have the code, open the directory containing `pyproject.toml`. Its local folder name can still be `rag-pipeline-package`; installation uses the metadata name `codelens`.

## 2. Install into an isolated Python environment

A virtual environment is a directory containing an interpreter environment and its dependencies. It keeps the package's libraries separate from other projects.

**PowerShell:**

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
.\.venv\Scripts\codelens.exe --help
Set-Alias -Name codelens -Value (Join-Path (Get-Location).Path '.venv\Scripts\codelens.exe')
```

The alias works for this terminal session, including after changing directories. Direct executable calls avoid needing to change PowerShell's script execution policy.

**Git Bash on Windows**, using a Python 3.12 interpreter:

```bash
python -m venv .venv
source .venv/Scripts/activate
python -m pip install -e .
codelens --help
```

**macOS/Linux equivalent:**

```bash
python3.12 -m venv .venv
source .venv/bin/activate
python -m pip install -e .
```

Native dependencies and keyring setup must be checked on those platforms, particularly headless Linux. `-e` means editable: source edits take effect without reinstalling. Use `python -m pip install .` for a normal source install. Keep the environment active, use its absolute executable path, or use the optional tool installation described in the [package README](../README.md).

If the same environment contains the old distribution, run `python -m pip uninstall graph-rag-reviewer` before reinstalling this renamed package. User/project data lives outside the distribution and is retained.

## 3. Configure once

```sh
codelens init
```

The prompts ask for a hidden API key, an embedding model, and an answer/reasoning model. Setup saves all recommended settings in `~/.codelens/config.json`; Windows uses `%USERPROFILE%\.codelens`. The key goes into the OS credential store, not JSON.

`init` does not contact OpenAI. It does not create a project in the current folder. Running it again preserves advanced settings; cancellation before completing setup leaves unsaved changes unapplied.

```sh
codelens doctor
codelens config list
codelens config check
```

`doctor` checks local configuration and paths. `config check` makes an authenticated model-list request, without repository content. It checks access and listed model IDs, not a full embedding or completion request. There is no `.env` setup step.

## 4. Choose a first test

To test the local mechanics **without OpenAI requests**, run this from the source checkout:

```sh
python scripts/smoke_local.py
```

Use your environment's Python; in unactivated PowerShell, replace `python` with `.\.venv\Scripts\python.exe`. The script uses fake model outputs but real parsing, Ladybug storage, incremental updates, and retrieval. Installation or parser initialization can still need downloads on a clean computer.

For the complete **model-backed** workflow:

```sh
codelens embed examples/smoke_repo
codelens status --project smoke_repo
codelens retrieve "Where is the discount validated?" --project smoke_repo
codelens ask "Is tax applied before or after the discount?" --project smoke_repo --show-context
```

These commands use your API key and can incur charges. The answer should be checked against `checkout.py` and `pricing.py`; exact wording is not fixed.

## 5. Index your own project

```sh
codelens projects add "/path/to/your-project" --name demo
codelens embed --project demo
codelens ask "Explain the entry point" --project demo
```

Replace the path. `projects add` registers it without model requests. Initialization adds `.codelens/` to the root `.gitignore`, creating that file if absent. The graph and manifest are saved under that project. Git ignore rules do not remove files already committed.

Run embed again unchanged: the graph can reuse its existing work. Edit one function and ask again: the default question flow checks and updates before retrieval. See the [walkthrough](22-end-to-end-walkthrough.md) for the exact sequence.

**Source:** [config_commands.py](../graph_rag/cli/config_commands.py), [settings.py](../graph_rag/cli/settings.py), [smoke_local.py](../scripts/smoke_local.py).

[Previous](03-what-this-product-does.md) | [Guide](README.md) | [Next: Commands](05-the-two-commands.md)
