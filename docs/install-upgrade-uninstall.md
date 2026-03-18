# Install, upgrade, and uninstall CodeLens

Use Windows x64 and Python 3.12 for the recommended setup; supported Python
versions are 3.11–3.13. This guide uses PowerShell. No terminal activation or
PowerShell execution-policy change is needed.

## Install a downloaded wheel

A wheel is the installable `.whl` file attached to a CodeLens release. It does not
bundle Python or all dependencies. Install it in a virtual environment:

```powershell
py -3.12 -m venv .venv-codelens
.\.venv-codelens\Scripts\python.exe -m pip install "C:\Downloads\codelens-0.1.0-py3-none-any.whl"
.\.venv-codelens\Scripts\codelens.exe --help
.\.venv-codelens\Scripts\codelens.exe init
```

Replace the download path. Run `init` once to save your API key and model choices.
The API key goes into the OS credential store. Model requests need internet access
and can incur OpenAI charges. The graph itself is stored locally.

Save a session alias before switching to another project:

```powershell
Set-Alias -Name codelens -Value (Resolve-Path '.venv-codelens\Scripts\codelens.exe').Path
codelens embed "C:\Projects\my-project"
codelens ask "Explain the entry point" --project my-project
```

Use your actual project path and name. For a command available in new terminals,
see the README's [tool installation options](../README.md#use-codelens-from-any-directory).
For example, if `uv` is already installed, `uv tool install --python 3.12
"C:\Downloads\codelens-0.1.0-py3-none-any.whl"` installs the wheel as an isolated tool;
`uv tool update-shell` helps expose its command in new terminals.
[uv tool documentation](https://docs.astral.sh/uv/guides/tools/).

## Install from PyPI after the release is published

These commands are for the maintainer-owned CodeLens package after its PyPI name
and publication are confirmed. Until then, use the wheel or the [source setup](04-setup-and-first-run.md).

```powershell
py -3.12 -m venv .venv-codelens
.\.venv-codelens\Scripts\python.exe -m pip install "codelens==0.1.0"
.\.venv-codelens\Scripts\codelens.exe init
```

With `uv`, the equivalent is `uv tool install --python 3.12 codelens`. If the
published distribution name changes, replace the install name; the CLI can still
be called `codelens`.

## Upgrade the same installation

Close active CodeLens commands first. Read the new version's changelog and back
up the entire project `.codelens` directory before a release that changes storage.
Also keep a copy of your user configuration if you want to restore its settings.

For a package installed from PyPI:

```powershell
.\.venv-codelens\Scripts\python.exe -m pip install --upgrade codelens
.\.venv-codelens\Scripts\codelens.exe doctor
```

For a downloaded wheel, use `python -m pip install --upgrade "C:\Downloads\NEW-WHEEL.whl"`
with the Python executable from the same environment. Replace `NEW-WHEEL.whl` with
the actual newer file. For a PyPI-backed uv tool installation, use `uv tool upgrade
codelens`; for a wheel-backed tool installation, reinstall from the new wheel with
`uv tool install --force --python 3.12 "C:\Downloads\NEW-WHEEL.whl"`.

Upgrading package files does not itself re-embed your projects. Existing saved
settings remain in use. If you change the embedding model or dimensions, follow
the CLI's keep/rebuild/cancel prompt; changing the answer model alone does not
require new embeddings. Future graph-format changes need the migration directions
in that release's notes.

## Uninstall the package

Use the same installer and environment used for installation:

```powershell
.\.venv-codelens\Scripts\python.exe -m pip uninstall codelens
```

For a uv tool installation, use `uv tool uninstall codelens` instead. Remove any
alias or symbolic link you created separately if it now points to a missing command.

Uninstalling the package leaves these user-owned files and credentials intact:

| Item | Location / behavior |
| --- | --- |
| Settings and project registry | `%USERPROFILE%\.codelens`, unless `--config-dir` selected another directory. |
| Project graph, source excerpts, and model history | Each project's `.codelens` directory. |
| OpenAI key | OS credential store, service `codelens.openai`. |
| Project ignore entry | The `.codelens/` entry in the project's `.gitignore`. |

For optional data cleanup, close running commands and inspect the intended folders
before removing them in File Explorer. `codelens projects remove NAME`, run before
uninstalling, only unregisters the project; it does not erase its graph. Removing
a graph folder means it must be rebuilt to query that project again.

To remove a saved key as well, locate the `codelens.openai` entry in Windows
Credential Manager. Its account is a hash of the configuration directory, so
multiple `--config-dir` profiles can have separate entries. Remove only entries
belonging to profiles you are retiring. This removes the local saved key; revoking
the actual OpenAI key is a separate account action. The CLI currently has no
single command that deletes every profile, graph, and credential.

See the [configuration reference](06-configuration-reference.md) for details.
