# CodeLens 0.1.0

Initial beta of a local command-line tool for asking questions about a codebase
using its code, documentation, and relationships as evidence.

## What you can do

- Run `codelens init` to save your API key and separate embedding/answer model choices.
- Index local repositories with `codelens embed` into per-project LadybugDB graphs.
- Find supporting code with `codelens retrieve` and ask questions with `codelens ask`.
- Detect source changes before questions and reuse valid embeddings for unchanged content.
- Switch between registered projects and inspect or update your saved configuration.

No Docker or database server is required. OpenAI embeddings and generated text
still need internet access, your own API key, and can incur charges.

## Install the attached wheel

Download the `.whl` asset, open PowerShell in its download directory, and run:

```powershell
py -3.12 -m venv .venv-codelens
.\.venv-codelens\Scripts\python.exe -m pip install .\codelens-0.1.0-py3-none-any.whl
.\.venv-codelens\Scripts\codelens.exe init
.\.venv-codelens\Scripts\codelens.exe embed "C:\path\to\your-project"
```

Use your actual project path. The source archive and SHA-256 checksums are also
attached. See the [user guide](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/install-upgrade-uninstall.md)
for upgrades, removal, and persistent command setup.

## Supported environment and limits

Windows x64, CPython 3.11–3.13; Python 3.12 recommended. Other operating systems
remain unverified. Python 3.14 is excluded due to a native dependency loading issue.
Each question uses one project. Answers request citations, but their correctness
must be checked against the source. Token streaming, a web interface, and persistent
chat history are not included in this release.

The existing compatibility suite has passed on supported Windows Python versions.
Fresh-clone validation has also exercised real embedding, retrieval, answers, and
automatic updates. Local release preparation additionally checks the built wheel;
its `validation.json` records the exact run and dependency versions.

Licensed under MIT. [Documentation](https://github.com/SajalDBansal/codelens/blob/HEAD/docs/README.md)
and [issue tracker](https://github.com/SajalDBansal/codelens/issues).
