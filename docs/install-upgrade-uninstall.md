# Install, upgrade, and uninstall CodeLens

The published package is [codelens-rag](https://pypi.org/project/codelens-rag/).
The command is `codelens`, and the Python import is `graph_rag`.
Use Windows x64 and Python 3.11–3.13; Python 3.12 is recommended. These examples
use PowerShell and do not require activation or an execution-policy change.

## Install from PyPI

Create an environment in a folder you intend to keep:

```powershell
py -3.12 -m venv .venv-codelens
.\.venv-codelens\Scripts\python.exe -m pip install codelens-rag
.\.venv-codelens\Scripts\codelens.exe --help
.\.venv-codelens\Scripts\codelens.exe init
```

To install the first release exactly, use `codelens-rag==0.1.0` as the package name
in the install command. `init` saves your model choices and stores your OpenAI key
in the OS credential store. Model requests need internet access and can incur
charges. The graph is stored locally; no Docker or database server is needed.

If the environment already has an earlier development installation of this same
project named `codelens` or `graph-rag-reviewer`, uninstall that old distribution
before installing `codelens-rag`. They share modules and command names.

## Use it from a project folder

In the same PowerShell terminal, save an alias before changing directories:

```powershell
Set-Alias -Name codelens -Value (Resolve-Path '.venv-codelens\Scripts\codelens.exe').Path
codelens embed "C:\Projects\my-project"
codelens ask "Explain the entry point" --project my-project
```

Use your actual project path and name. The alias lasts for this terminal session.
For a command available in new terminals, use the README's
[tool installation options](../README.md#use-codelens-from-any-directory).

If you already use uv, it can create and manage the tool environment:

```sh
uv tool install --python 3.12 codelens-rag
uv tool update-shell
codelens init
```

Follow any shell instructions printed by uv and reopen the terminal if needed.
See [uv's tool documentation](https://docs.astral.sh/uv/guides/tools/).

## Install a downloaded wheel

A wheel is the installable `.whl` file on the
[PyPI download page](https://pypi.org/project/codelens-rag/0.1.0/#files).
It does not bundle Python or all dependencies. In a new environment:

```powershell
py -3.12 -m venv .venv-codelens
.\.venv-codelens\Scripts\python.exe -m pip install "C:\Downloads\codelens_rag-0.1.0-py3-none-any.whl"
.\.venv-codelens\Scripts\codelens.exe init
```

Replace the download path. If the environment already exists, skip creating it.
For an editable source installation and the included examples, see
[Setup and first run](04-setup-and-first-run.md#optional-install-the-source-for-development).

## Upgrade the same installation

Close active CodeLens commands and read the [changelog](../CHANGELOG.md). Before
installing a release that changes graph storage, back up each project's entire
`.codelens` directory and retain your user configuration if you need to restore it.

```powershell
.\.venv-codelens\Scripts\python.exe -m pip install --upgrade codelens-rag
.\.venv-codelens\Scripts\codelens.exe doctor
```

For a uv installation, use `uv tool upgrade codelens-rag`. For a downloaded wheel,
use the same environment's Python with `-m pip install --upgrade` and the new wheel
path. A wheel-backed uv tool can be reinstalled with
`uv tool install --force --python 3.12 "C:\Downloads\NEW-WHEEL.whl"`.

Upgrading package files does not itself re-embed projects. Existing settings remain
in use. Changing the embedding model or dimensions invokes the CLI's
keep/rebuild/cancel workflow; changing only the answer model does not require new
embeddings. Follow the release's migration guidance for future graph-format changes.

## Uninstall the package

Use the same installer and environment used for installation:

```powershell
.\.venv-codelens\Scripts\python.exe -m pip uninstall codelens-rag
```

For a uv tool installation, use `uv tool uninstall codelens-rag`. Remove any alias
or symbolic link you created separately if it points to a missing command.

Uninstalling leaves your data intact:

| Item | Location / behavior |
| --- | --- |
| Settings and project registry | `%USERPROFILE%\.codelens`, unless `--config-dir` selected another directory. |
| Graph, source excerpts, and model history | Each project's `.codelens` directory. |
| OpenAI key | OS credential store, service `codelens.openai`. |
| Git ignore entry | The `.codelens/` entry in each project's `.gitignore`. |

For optional data cleanup, close running commands and inspect the folders before
removing them. `codelens projects remove NAME`, run before uninstalling, only
unregisters the project; it does not erase its graph. Deleting a graph means it
must be rebuilt before querying that project again.

To remove a saved key, locate the `codelens.openai` entry in Windows Credential
Manager. Its account is a hash of the configuration directory; separate
`--config-dir` profiles can have separate entries. Remove only entries belonging
to profiles you are retiring. Deleting the local saved key does not revoke the
key at OpenAI. There is no command that deletes every profile, graph, and credential.

See the [configuration reference](06-configuration-reference.md),
[compatibility](compatibility.md), and [changelog](../CHANGELOG.md).
