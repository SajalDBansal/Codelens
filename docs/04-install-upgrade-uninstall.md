# 04 - Install, upgrade, and uninstall CodeLens

The package name is `codelens-rag`; the command you run is `codelens`.
Install from the source folder using the steps below.

Use **Python 3.14** for the simplest installation. **Python 3.15** is verified on
Windows and Linux with a compiler; Windows needs Microsoft C++ Build Tools and
a Windows SDK to build the parser. See [compatibility](08-compatibility.md) for
platform details, including the unverified macOS and free-threaded configurations.

## Recommended: install as a tool with uv

### Get uv

Skip this step if `uv --version` works.

On Windows, run in PowerShell:

```powershell
winget install --id astral-sh.uv -e
```

On macOS or Linux:

```sh
curl -LsSf https://astral.sh/uv/install.sh | sh
```

Reopen the terminal. See [uv's installation guide](https://docs.astral.sh/uv/getting-started/installation/)
for other installation methods.

### Get the CodeLens source

If you already have this checkout, open its folder and skip cloning. Otherwise,
clone the repository:

```sh
git clone https://github.com/SajalDBansal/codelens.git
cd codelens
```

You can also use **Code → Download ZIP** on the
[repository page](https://github.com/SajalDBansal/codelens), extract it, and open a
terminal in the extracted folder. The folder must contain `pyproject.toml`.

### Install and start

From the CodeLens source folder:

```sh
uv tool install --python 3.14 .
uv tool update-shell
```

Close and reopen the terminal, then run:

```sh
codelens --help
codelens init
```

uv manages the isolated environment and can download the requested Python version.
Its [tool installation](https://docs.astral.sh/uv/guides/tools/) makes the command
available from other folders; `update-shell` sets up the command location for
future terminals. No manual alias or symbolic link is needed.

Follow [Setup and first run](05-setup-and-first-run.md#3-index-a-project) to index
your project. An OpenAI API key and internet access are needed for model requests.

## Alternative: use pip and a virtual environment

Use this if you already manage Python environments and prefer pip. Install
Python 3.14 first, then run these commands from the CodeLens source folder.

### Windows PowerShell

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install .
.\.venv\Scripts\codelens.exe init
.\.venv\Scripts\codelens.exe embed "C:/Projects/my-app"
.\.venv\Scripts\codelens.exe ask "Explain the main entry point" --project my-app
```

Replace the project path and name. These commands call the environment directly,
so PowerShell script activation is unnecessary. For later commands, use the same
`codelens.exe` path. The uv method above provides the shorter `codelens` command
across terminals.

### Linux or macOS shell

```sh
python3.14 -m venv .venv
source .venv/bin/activate
python -m pip install .
codelens init
```

Keep this environment active when running `codelens`. In a new terminal, activate
it again using the environment's full path. macOS has not been validated in the
current compatibility matrix; headless Linux may need a keyring backend configured.

## Development installation

From the source folder, install the editable package and test dependencies.
Windows PowerShell:

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest -q tests
.\.venv\Scripts\python.exe scripts/smoke_local.py
```

In an activated Linux/macOS environment, use `python` for the same pip, pytest,
and smoke commands. Editable installation uses source changes immediately.
The [testing guide](21-testing.md) explains the automated checks.

## Install a downloaded wheel

A `.whl` file is an installable package; its dependencies are still downloaded.
Install the downloaded wheel with Python 3.14:

```powershell
uv tool install --python 3.14 "C:/Downloads/YOUR_CODELENS_WHEEL.whl"
uv tool update-shell
```

Replace the filename with the actual downloaded wheel and reopen your terminal.

## Upgrade

Close running CodeLens commands. From an updated source checkout, replace the
tool installation with:

```sh
uv tool install --force --python 3.14 .
codelens doctor
```

For a pip installation, use that environment's Python with `-m pip install --upgrade .`.
For a new wheel, repeat the wheel installation with `uv tool install --force`.
These commands update the application; they do not re-embed your projects.

Back up each project's entire `.codelens` folder before an upgrade. See
[storage and maintenance](14-local-storage-and-maintenance.md) for backup details.

## Uninstall

For the recommended tool installation:

```sh
uv tool uninstall codelens-rag
```

For pip, use the environment where you installed the package:

```powershell
.\.venv\Scripts\python.exe -m pip uninstall codelens-rag
```

Uninstalling leaves your data in place:

| Data | Location |
| --- | --- |
| Settings and project list | `~/.codelens`, or the directory selected with `--config-dir`. |
| Project index, embeddings, and source snapshots | Each project's `.codelens` folder. |
| OpenAI API key | OS credential store, service `codelens.openai`. |

`codelens projects remove NAME` only unregisters a project; it does not delete
its index. Remove a saved API key through your OS credential manager if needed.
Removing the local saved key does not revoke it at OpenAI.

[Previous: What the product does](03-what-this-product-does.md) | [Guide](README.md) | [Next: Setup and first run](05-setup-and-first-run.md)
