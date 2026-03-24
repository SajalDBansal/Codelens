# Supported versions and dependencies

CodeLens 0.1.0 is published as [codelens-rag](https://pypi.org/project/codelens-rag/0.1.0/).
The supported target is **Windows x64 with CPython 3.11–3.13**. Python 3.12 is the
recommended starting point. Linux, macOS, Windows ARM64, PyPy, and free-threaded
Python have not been verified as release targets.

## Python compatibility

`requires-python = ">=3.11,<3.14"` in [pyproject.toml](../pyproject.toml) controls
which Python versions pip accepts. LadybugDB has a wider declared range, but the
package promises only the combinations tested here.

Python 3.14 is excluded because LadybugDB 0.21.2 failed to load its native library
with the tested standard Windows Python 3.14.7 installation. The native wheel
expects OpenSSL DLL names that differ from those provided by that interpreter.
A separate uv Python runtime loaded it, which does not establish support for all
Python 3.14 distributions. No DLL-copying workaround is included.

## Verified release baseline

The Windows compatibility checks on 21 October 2026 passed 462 tests, dependency
checks, and the local graph smoke test on CPython 3.11.16, 3.12.14, and 3.13.15.
A separate Python 3.12 run with the newest allowed dependencies also passed.

For publication on 22 October 2026, the renamed `codelens-rag` package passed all
462 tests on Python 3.12.14. A fresh installation downloaded from PyPI passed
dependency checks, CLI entry points, and the local graph workflow. The published
wheel and source archive hashes matched the validated local files.

These checks use deterministic model responses and may reuse download/parser
caches. They verify package mechanics and the tested Windows environments, not
OpenAI answer quality or every first-install environment.

## Dependency ranges and exact versions

| File | Purpose |
| --- | --- |
| [pyproject.toml](../pyproject.toml) | Python requirement and dependency ranges used by normal package installations. |
| [requirements.txt](../requirements.txt) | Matching runtime library ranges; installing only this file does not install the CodeLens CLI. |
| [constraints/tested-windows.txt](../constraints/tested-windows.txt) | Exact tested runtime/development versions, including indirect dependencies and Python-specific conditions. |

Constraints apply only when passed with `-c`. They are a tested version snapshot,
not a hash lockfile or a record of every parser binary and operating-system detail.
Normal installs resolve versions within the ranges in the package metadata.

| Direct dependency | Allowed range | Baseline |
| --- | --- | --- |
| LadybugDB | `==0.21.2` | `0.21.2` |
| Tree-sitter | `>=0.26.0,<0.27` | `0.26.0` |
| Tree-sitter language pack | `>=1.22.1,<2` | `1.22.1` |
| OpenAI SDK | `>=3.35.1,<4` | `3.35.1` |
| Pydantic | `>=2.14.1,<3` | `2.14.1` |
| Keyring | `>=25.7.0,<26` | `25.7.0` |
| Filelock | `>=3.32.7,<4` | `3.32.7` |
| Typer | `>=0.27.3,<0.28` | `0.27.3` |
| Rich | `>=14.3.4,<15` | `14.3.4` |
| Prompt Toolkit | `>=3.0.53,<4` | `3.0.53` |
| pytest (development) | `>=9.1.1,<10` | `9.1.1` |
| Ruff (development) | `>=0.17.1,<0.18` | `0.17.1` |

LadybugDB is pinned because it loads native code and reads persistent graph files.
The parser and grammar bundle are tested together. Upper bounds limit upgrades;
they do not guarantee every possible dependency combination works. The baseline
also contains indirect dependencies needed only on Python 3.11.

## Check a source change locally

From the source checkout, use a disposable environment. Substitute `3.11` or
`3.13` to check another supported interpreter:

```powershell
py -3.12 -m venv .venv-test
.\.venv-test\Scripts\python.exe -m pip install -c constraints/tested-windows.txt ".[dev]"
.\.venv-test\Scripts\python.exe -m pip check
.\.venv-test\Scripts\python.exe -m pytest -q tests
.\.venv-test\Scripts\python.exe scripts/smoke_local.py
```

Use a different fresh environment without `-c` to check the newest permitted
dependencies. These tests do not need an OpenAI API key, but dependency and parser
downloads can require network access. See [Testing](18-testing.md) for coverage,
recovery checks, and model-backed retrieval evaluation.

When changing supported versions, keep the metadata, runtime requirements,
constraints, and this reference consistent. Test changed dependency ranges on the
supported interpreters before expanding compatibility claims.

[Testing guide](18-testing.md) | [Documentation index](README.md)
