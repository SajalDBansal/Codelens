# Supported versions and local verification

GitHub Actions and CI/CD setup are deferred. This repository does not include an enabled GitHub workflow. The checks below run locally and do not deploy or publish the package.

For the full manual build and publication sequence, use the [release guide](releasing.md).
It provides `scripts/prepare_release.py` to run the checks on the current Python version
and test the finished wheel. Dependency compatibility changes still need the
supported-version checks described here. CI is not a release requirement.

## Supported environment

The release target is **Windows x64 with standard CPython 3.11, 3.12, or 3.13**. Python 3.12 remains the recommended starting point. Python 3.10 and 3.14 or later are outside CodeLens's declared range. PyPy, free-threaded Python, Windows ARM64, Linux, and macOS are not currently verified release targets.

`requires-python = ">=3.11,<3.14"` in [pyproject.toml](../pyproject.toml) tells installers which Python versions to accept. LadybugDB itself declares `<3.15`, but CodeLens deliberately uses a stricter limit based on the Windows test results. Dependency wheel availability and passing tests are still required; metadata alone is not proof that a platform works.

**Why exclude Python 3.14?** The tested Windows Python 3.14.7 installation could install LadybugDB 0.21.2 but could not load its native `_lbug` extension. Inspection showed that the wheel imports `libssl-3-x64.dll` and `libcrypto-3-x64.dll`, while this standard Python runtime provides `libssl-3.dll` and `libcrypto-3.dll`. Ladybug then fell back to a missing C API shared library, causing graph tests to fail. A separately downloaded uv Python 3.14 runtime did load the same extension, so this is distribution-sensitive. We do not ship DLL-copying workarounds or advertise general 3.14 support. Retest with an upstream-compatible wheel before extending the range.

Local verification on **21 October 2026**, in separate Windows x64 virtual environments:

| Environment | Full suite | Local graph smoke test | Dependency check |
| --- | --- | --- | --- |
| CPython 3.11.16, baseline | 462 passed | Passed | Passed |
| CPython 3.12.14, baseline | 462 passed | Passed | Passed |
| CPython 3.13.15, baseline | 462 passed | Passed | Passed |
| CPython 3.12.14, newest allowed dependencies | 462 passed | Passed | Passed |

The newest-dependencies run selected `httpx2==2.16.0` and `httpcore2==2.16.0`; the reproducible baseline uses `2.15.1` for both. Its other direct dependencies matched the baseline. The original Python 3.14.7 trial had 417 passing tests, 26 failures, and 19 setup errors associated with the native-library load failure; it is excluded rather than marked supported.

These environments shared this machine's download/parser caches. They establish local compatibility, not the separate first-user/clean-machine release check. No live OpenAI calls were made. **GitHub-hosted execution has not been performed; automation is deferred.** A local pass is not evidence of a hosted run.

## Three files to understand

| File | Meaning |
| --- | --- |
| [pyproject.toml](../pyproject.toml) | The Python and dependency versions normal package installations are allowed to use. |
| [requirements.txt](../requirements.txt) | The same runtime dependency ranges, for installing libraries without the CodeLens entry points. |
| [constraints/tested-windows.txt](../constraints/tested-windows.txt) | Exact runtime/test dependency versions used for the reproducible Windows baseline, including indirect dependencies. |

A dependency is a library CodeLens uses. A transitive dependency is a library that one of those libraries needs. The constraints file records both. It is a version snapshot, not a cryptographic lockfile: it does not pin package hashes, Python patch versions, the operating-system image, or downloaded grammar binaries separately.

Constraints only apply when you pass `-c`. Normal `pip install .` uses the ranges in the package metadata. Build/install tools are separate: local distribution validation uses `build>=1,<2`, while the isolated package build uses the `setuptools>=77.0.3` build requirement. They are not runtime dependencies.

## Dependency policy

The minimum direct dependency versions match the tested baseline. Older versions may work, but this release does not promise them. Upper bounds limit upgrades to the reviewed API families; they do not guarantee that every possible combination will work.

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

LadybugDB stays exact because it is a native dependency that reads persistent graph files. Tree-sitter, Typer, and Ruff are pre-1.0 packages, so their ranges stop at the next minor version. The parser and language pack must be tested together.

The baseline also pins Python 3.11's extra indirect dependencies (`importlib-metadata`, `zipp`, and `backports.tarfile`) using Python-version conditions. Do not remove them just because they are absent from a Python 3.12 environment.

## Local validation coverage

The test suite covers parsing, graph persistence, indexing recovery, retrieval, saved settings, and CLI behavior. Tests use temporary files, mocked credential storage and fake model responses. The [SDK tests](../tests/test_openai_sdk.py) use the installed OpenAI SDK with an in-memory HTTP transport. No OpenAI API key or paid API calls are required.

The [graph smoke script](../scripts/smoke_local.py) exercises parsing, persistence, incremental reuse and retrieval with deterministic model doubles. These checks do not measure real answer quality or prove access to a model.

The [distribution checker](../scripts/check_distribution.py) verifies source and wheel contents, then runs CLI entry points and the smoke script outside the checkout. This prevents local source files from hiding missing wheel contents. GitHub workflow files are deliberately excluded from the source archive while automation is on hold.

Internet access is required to install dependencies and obtain parsers on first use. Diagnose download failures separately from application failures. Ruff is available for development; the current verification gates are dependency installation, tests, smoke checks and packaging.

## Run the checks locally

From the package root in PowerShell, create a new disposable environment. Use `3.11` or `3.13` instead of `3.12` to test another supported interpreter:

```powershell
py -3.12 -m venv .venv-ci
.\.venv-ci\Scripts\python -m pip install -c constraints/tested-windows.txt ".[dev]"
.\.venv-ci\Scripts\python -m pip check
.\.venv-ci\Scripts\python -m pytest -q
.\.venv-ci\Scripts\python scripts/smoke_local.py
```

To test the newest permitted dependencies, create a **different fresh environment** and install with `python -m pip install --upgrade ".[dev]"`, without `-c`. Then run the same checks. Reusing the baseline environment can leave old indirect dependencies installed and weaken this check.

For packaging, use a fresh checkout/copy with no old files in `dist`, and run:

```powershell
.\.venv-ci\Scripts\python -m pip install "build>=1,<2"
.\.venv-ci\Scripts\python -m build
py -3.12 -m venv .venv-wheel
$wheel = (Get-ChildItem -LiteralPath dist -Filter *.whl).FullName
.\.venv-wheel\Scripts\python -m pip install -c constraints/tested-windows.txt $wheel
.\.venv-wheel\Scripts\python -m pip check
.\.venv-ci\Scripts\python scripts/check_distribution.py --python .venv-wheel/Scripts/python.exe
```

These commands create test environments; they do not modify your saved CodeLens configuration or real project graphs.

## How to update versions later

1. Change the appropriate range in `pyproject.toml`. Keep runtime ranges in `requirements.txt` identical. Review release notes before widening a major/pre-1.0 minor bound or changing LadybugDB.
2. Test the proposed new baseline in a clean environment. Update its exact versions in `constraints/tested-windows.txt`, including changed indirect dependencies and Python-version conditions. If the direct minimum changes, update that minimum in the package metadata too.
3. Run the baseline checks on Python 3.11, 3.12 and 3.13, plus a separate newest-dependencies check on Python 3.12. The baseline checks the direct minimum versions; the newest-dependencies check detects changes inside allowed ranges. This is not an exhaustive test of every possible dependency combination.
4. Run the distribution checks, then update the table above and the verification record. A green local run does not count as a GitHub run; record the evidence accurately.
5. To add a Python version, check LadybugDB and parser wheel support, update `requires-python`, add its classifier and include it in local compatibility checks, and pass the same checks before advertising support. Do not simply remove the Python upper bound.

References: [pip constraints](https://pip.pypa.io/en/stable/user_guide/#constraints-files), [LadybugDB release metadata](https://pypi.org/project/ladybug/0.21.2/), [OpenAI Python SDK](https://developers.openai.com/api/reference/python).

[Testing guide](18-testing.md) | [Documentation index](README.md)
