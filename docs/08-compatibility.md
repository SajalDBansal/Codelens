# 08 - Python compatibility

CodeLens requires **Python 3.11 or newer**. SQLite is included with Python; NumPy
stores and searches embedding arrays. Use **Python 3.14** for the simplest setup.
Python **3.15** works with a compiler available for the parser dependency.

## Tested environments

Validated on 25 October 2026:

| Environment | Result |
| --- | --- |
| Windows x64, CPython 3.11.16 | 473 tests pass. |
| Windows x64, CPython 3.12.14 | 473 tests pass. |
| Windows x64, CPython 3.13.15 | 473 tests pass. |
| Windows x64, CPython 3.14.7 | 473 tests pass. |
| Windows x64, CPython 3.15.1 | 473 tests pass; parser installed with MSVC and a Windows SDK. |
| Linux x64, CPython 3.15.1 (`python:3.15.1-bookworm`) | 473 tests pass; installed-package smoke test passes outside the checkout. |

Tests use deterministic model doubles and cover
parsing, graph relationships, embedding reuse, source hydration, search, CLI
configuration, compaction, recovery, and mocked answer generation.

They do not establish live OpenAI availability, answer quality, or a Linux desktop
credential-store setup. macOS, ARM64, PyPy, and free-threaded Python builds have
not been validated. Future Python releases need their own dependency and pipeline
checks.

## Python 3.15 installation requirements

The tested parser dependency, `tree-sitter==0.26.0`, has Windows wheels through
CPython 3.14. Installing it on 3.15 builds its source distribution. See the
[upstream release files](https://pypi.org/project/tree-sitter/0.26.0/#files).

On Windows, install [Microsoft C++ Build Tools](https://visualstudio.microsoft.com/visual-cpp-build-tools/)
with an x64 MSVC compiler and Windows SDK before installing CodeLens. Validation
used MSVC 19.44.35229 and Windows SDK 10.0.26100.0. Linux validation uses the
compiler in the official `python:3.15.1-bookworm` image. No parser source changes
are required.

Windows 3.15 installation without build tools fails at the parser build. Use
Python 3.14 if you want the verified installation that does not need a compiler.

## Run the checks

From the source checkout in PowerShell:

```powershell
py -3.15 -m venv .venv-test
.venv-test\Scripts\python -m pip install ".[dev]"
.venv-test\Scripts\python -m pip check
.venv-test\Scripts\python -m pytest -q tests
.venv-test\Scripts\python scripts/smoke_local.py
```

Use `py -3.14` instead for Python 3.14. In an activated Linux environment, use
`python` for the same pip, pytest, and smoke commands. Dependency and first-use
grammar downloads may need network access; these checks make no OpenAI calls.

The [CI matrix](../.github/workflows/compatibility.yml) covers Python 3.11–3.15
on Windows and Linux, with compiler-equipped runners for 3.15. Its smoke test runs
outside the checkout to exercise the installed package. The workflow also runs
monthly to detect upstream dependency changes. Local checks do not establish that
the GitHub workflow has passed; review its results before publishing.

Each CI job creates a fresh virtual environment so preinstalled runner tools do
not affect dependency checks. Tests explicitly disable Typer's forced terminal
formatting so captured help output stays plain text in GitHub Actions.

## Dependencies and reproducibility

| Dependency | Versions used in local validation |
| --- | --- |
| NumPy | 2.4.6 on Python 3.11; 2.5.4 on 3.12–3.15 |
| Tree-sitter / language pack | 0.26.0 / 1.22.1 |
| OpenAI SDK | 3.37.0 |
| Pydantic / core | 2.14.1 / 2.50.1 |
| Keyring / filelock | 25.7.0 / 3.32.7 |
| Typer / Rich / prompt-toolkit | 0.27.3 / 14.3.4 / 3.0.53 |
| pytest / Ruff | 9.1.1 / 0.17.2 |

[pyproject.toml](../pyproject.toml) and [requirements.txt](../requirements.txt)
define runtime dependency ranges. [constraints/tested-windows.txt](../constraints/tested-windows.txt)
records resolved Windows versions for Python 3.11–3.14. To reproduce those versions,
add `-c constraints/tested-windows.txt` to the pip install command. Python 3.15
validation uses normal package ranges; installed transitive versions can differ.

[Previous: Configuration reference](07-configuration-reference.md) | [Guide](README.md) | [Next: Architecture](09-architecture.md)
