# Release maintenance reference

For your first publication, follow the shorter [publishing checklist](releasing.md).
This page keeps the detailed build and installation commands for later maintenance.

**TestPyPI note, checked 22 October 2026:** a `codelens` version `0.1.0` already
exists there with project links that do not match this repository. The optional
TestPyPI instructions below require ownership of that name or a separately named
practice build. Do not upload this release over another project's package. A
locally validated wheel can be published directly to PyPI using the short checklist.

This is the maintainer's step-by-step guide for `codelens` version `0.1.0`.
It uses PowerShell on Windows and Python 3.12. There is no CI pipeline and nothing
publishes automatically. A wheel is the installable `.whl` file; a source archive
is the `.tar.gz` containing source and supporting files.

The changelog, user installation guide, release notes, and local validation script
are prepared in this repository. You handle your account setup, review, commits,
and uploads. An OpenAI API key runs CodeLens; PyPI/TestPyPI tokens publish the package.
They are different credentials.

## 1. Review the release files

Open the directory containing `pyproject.toml`:

```powershell
Set-Location "E:\projects\6. Codelens"
git status --short
git diff
```

Use your checkout path if different. Review [CHANGELOG.md](../CHANGELOG.md), the
[user installation guide](install-upgrade-uninstall.md), and the
[GitHub release notes](release-notes-0.1.0.md). Version `0.1.0` is already in
`pyproject.toml`. Keep the version, notes, and eventual tag `v0.1.0` consistent.

Commit the intended source and documentation changes using your normal Git workflow.
Keep `.venv*`, graphs, credentials, and `dist/` out of Git. Release files are uploaded
as assets, not committed. The package currently promises Windows x64 and Python
3.11–3.13; do not expand that claim without testing.

## 2. Prepare a release environment

Do this once for the checkout, or use a new environment when testing newer dependencies:

```powershell
py -3.12 -m venv .venv-release
.\.venv-release\Scripts\python.exe -m pip install -e ".[dev]" "build>=1,<2" "twine>=6,<7"
```

These are development/build tools, not extra requirements for CodeLens users.
No OpenAI key or PyPI token is needed for the next step.

## 3. Build and validate locally

```powershell
.\.venv-release\Scripts\python.exe scripts/prepare_release.py
```

The script stops on the first failed command. It:

1. Checks installed dependencies and runs the full test suite.
2. Builds a source archive, then builds the wheel from that archive.
3. Checks metadata and README rendering with Twine.
4. Installs the wheel into a fresh temporary environment using normal dependency ranges.
5. Checks archive contents, imports, CLI launchers, and the real local graph smoke workflow.
6. Writes checksums and a validation record only after all checks pass.

Tests use model doubles and temporary graphs. This step does not access your API
key or re-index your real projects. Dependency/parser downloads can require internet.
The temporary wheel environment is removed when the script exits.

Expected output:

```text
dist/
    codelens-0.1.0-py3-none-any.whl
    codelens-0.1.0.tar.gz
    SHA256SUMS
    validation.json
```

`validation.json` records the interpreter, checks, resolved wheel dependencies,
artifact hashes, and Git revision when available. It explicitly records that no
live model test or upload occurred. Keep the existing fresh-clone/live-API report
as separate evidence. A successful run here covers this interpreter, not a fresh
run of every supported Python version; use [compatibility checks](compatibility-and-ci.md)
when changing dependencies or supported versions.

The script refuses a nonempty `dist/` to prevent mixing versions or accepting stale
results. For a retry after a failure or source edit, preserve the old files first:

```powershell
Rename-Item -LiteralPath dist -NewName ("dist-backup-" + (Get-Date -Format 'yyyyMMdd-HHmmss'))
.\.venv-release\Scripts\python.exe scripts/prepare_release.py
```

If an artifact set was already prepared and verified for the same source contents,
reuse it. Changing source, metadata, or packaged documentation requires rebuilding.
Run `Get-FileHash -Algorithm SHA256 dist\*.whl, dist\*.tar.gz` to compare files with
`SHA256SUMS` before uploading or transferring them.

## 4. Decide where users will download it

- For a portfolio release with downloadable files, go to step 8. PyPI is optional.
- For installation through `pip install codelens`, continue with TestPyPI, then PyPI.

The name `codelens` in GitHub and in the CLI does not reserve the distribution name
on either index. Check ownership/availability on both before uploading. A missing
project page alone does not guarantee a name can be registered. If the name is
unavailable, choose another distribution name, update `[project].name` and the
installation instructions, and rebuild. The `codelens` CLI and `graph_rag` import
can remain unchanged. Examples below assume `codelens` is the confirmed name.

## 5. Set up your publishing accounts

Create separate accounts on [TestPyPI](https://test.pypi.org/account/register/) and
[PyPI](https://pypi.org/account/register/), verify your email, and enable two-factor
authentication. Store recovery codes privately. PyPI requires 2FA.
[Account documentation](https://docs.pypi.org/your-account/managing-your-account/).

In each site's account settings, create an upload token. For the first upload of a
new project, the project does not exist yet, so use an account-wide token. After
creation, replace it with a project-scoped token and revoke the initial broad token.
Keep the two sites' tokens separate. Paste a token only into Twine's hidden prompt;
do not put it in source, this guide, a shell command, or a chat message.

## 6. Try the release on TestPyPI

Upload only the two distributions, not `validation.json` or `SHA256SUMS`:

```powershell
.\.venv-release\Scripts\python.exe -m twine upload --repository testpypi --username __token__ dist/*.whl dist/*.tar.gz
```

Paste your **TestPyPI** token when prompted. Check the project page's description,
version, author, license, and files. TestPyPI is a rehearsal index; it has separate
accounts and is not permanent storage. [TestPyPI guide](https://packaging.python.org/en/latest/guides/using-testpypi/).

Test installation in a fresh environment. Install dependencies from normal PyPI,
then install only CodeLens from TestPyPI:

```powershell
py -3.12 -m venv .venv-testpypi
.\.venv-testpypi\Scripts\python.exe -m pip install --index-url https://pypi.org/simple/ -r requirements.txt
.\.venv-testpypi\Scripts\python.exe -m pip install --index-url https://test.pypi.org/simple/ --no-deps "codelens==0.1.0"
.\.venv-testpypi\Scripts\python.exe -m pip check
.\.venv-testpypi\Scripts\python.exe -I -c "import importlib.metadata as m; print(m.version('codelens'))"
.\.venv-release\Scripts\python.exe scripts/check_distribution.py --python .venv-testpypi/Scripts/python.exe
```

`--no-deps` keeps dependency resolution off the test index; the preceding command
installed those dependencies from the matching release's `requirements.txt`.
The distribution checker runs the installed CLI and graph smoke outside the checkout.
Avoid `pip install -e .` in this environment: that would replace the artifact being tested.

The real OpenAI workflow was already validated separately. If your release changes
model or credential behavior, repeat `init`, `embed`, `retrieve`, and `ask` on a small
disposable project with your own configured key before publication.

## 7. Publish the approved files to PyPI

Once the TestPyPI result is satisfactory, upload the same files to the real index:

```powershell
.\.venv-release\Scripts\python.exe -m twine upload --repository pypi --username __token__ dist/*.whl dist/*.tar.gz
```

Use your **PyPI** token. Do not rebuild between the test upload and production
upload unless fixing something, in which case repeat the validation. PyPI does not
allow uploaded filenames to be reused, even after deletion. A changed release
normally needs a new version such as `0.1.1`.
[Upload troubleshooting](https://docs.pypi.org/publishing/troubleshooting/).

Verify the production installation:

```powershell
py -3.12 -m venv .venv-pypi
.\.venv-pypi\Scripts\python.exe -m pip install --index-url https://pypi.org/simple/ "codelens==0.1.0"
.\.venv-pypi\Scripts\python.exe -m pip check
.\.venv-release\Scripts\python.exe scripts/check_distribution.py --python .venv-pypi/Scripts/python.exe
```

Use fresh environment names if these already exist. Confirm the README links and
installation command point to the project you own. The build/upload/install flow
is covered by the [PyPA packaging tutorial](https://packaging.python.org/en/latest/tutorials/packaging-projects/).

## 8. Create the GitHub release

Push the reviewed release commit using your normal Git workflow. On the repository's
**Releases** page, choose **Draft a new release**. Select/create `v0.1.0` at the
commit matching the built source; check the target carefully.

Use title **CodeLens 0.1.0**, copy the [prepared release notes](release-notes-0.1.0.md),
and attach the wheel, source archive, and `SHA256SUMS`. Choose the pre-release marker
to communicate this initial beta's status. Review the draft and then publish it.
The GitHub marker is independent of Python's version string. Include a PyPI link
only if that publication succeeded. [GitHub release instructions](https://docs.github.com/en/repositories/releasing-projects-on-github/managing-releases-in-a-repository).

## 9. Record completion

Record the actual release date, tag/commit, artifact hashes, and release URLs in your
release log. Update the changelog's pending-publication wording and the README's
installation status after successful publication. A subsequent source change belongs
in the next release; keep the published tag and files intact.

You are finished when users can download/install the chosen artifact, run `codelens
--help`, initialize their own configuration, and follow the documented workflow.
For later releases, update the version/changelog, run the local checks, and repeat
the same manual publishing steps. CI is not a prerequisite.
