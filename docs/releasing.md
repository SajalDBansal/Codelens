# Publish CodeLens: the simple checklist

**Goal:** let other people install your package with `pip install codelens`.
Your code is already on GitHub. The remaining job is uploading the checked package
to **PyPI**, the website from which pip installs packages.

We are using a direct PyPI release after local checks. You do **not** need a
TestPyPI account for this route: TestPyPI already has a `codelens 0.1.0` package
whose project links do not match yours. Nothing has been uploaded for you.

## Already handled on your computer

- Your latest GitHub commit was checked against the remote `main` branch.
- Package tests and local graph checks have passed; the runtime code is unchanged.
- The release files are in `E:\projects\6. Codelens\dist`.
- The tools needed by the commands below are installed in `.venv-release`.
- A one-command check is provided for testing the package after you upload it.

You do not need to recreate an environment, set up CI, or build the package again
unless you change packaged files. The local check report is in
`planning/codelens-release-check-2026-10-22.md`. On a different computer, use the
[setup reference](release-maintenance.md#2-prepare-a-release-environment).

## 1. Create your PyPI account

1. Open [PyPI registration](https://pypi.org/account/register/).
2. Choose a username and password, and enter your email address.
3. Open PyPI's email and verify your email address.
4. Sign in and open **Account settings**. Set up **Two-factor authentication** using
   an authenticator app or security key, and save the recovery codes privately.

This account is separate from GitHub and OpenAI. You do not create the CodeLens
project manually; the first successful upload creates it.
[PyPI account instructions](https://docs.pypi.org/your-account/managing-your-account/).

**Done when:** you can sign in, your email is verified, and two-factor authentication is enabled.

## 2. Create an upload token

1. Open [your PyPI account settings](https://pypi.org/manage/account/#api-tokens).
2. Find **API tokens** and choose **Add API token**.
3. Give it a name such as `codelens-first-release`.
4. Choose **Entire account** for Scope, because your project does not exist yet.
5. Create the token and save it privately. You will paste it into the terminal in step 4.

This is a PyPI upload token, usually starting with `pypi-`. It is **not your OpenAI
key**. Do not put it in source files or send it in chat. After publishing, you can
replace this first token with one restricted to the CodeLens project.

**Done when:** you have the token saved somewhere private.

## 3. Save this small guide/tool update to GitHub

Your earlier commit is already pushed. This clearer guide adds one checking script
and keeps the longer instructions as a reference. Open **PowerShell**, then run:

```powershell
Set-Location "E:\projects\6. Codelens"
git diff
git add docs/releasing.md docs/release-maintenance.md scripts/check_published.py scripts/prepare_release.py
git commit -m "Simplify the manual publishing steps"
git push origin main
```

Review the changes before committing. `dist/` and `.venv-release/` stay ignored;
do not add them to Git. If these exact changes are already committed and pushed,
skip the commit commands. Committing alone does not require rebuilding the files.

**Done when:** this guide and `scripts/check_published.py` appear on GitHub.

## 4. Upload CodeLens to PyPI

Use PowerShell in the same project directory. Copy and run this one command:

```powershell
.\.venv-release\Scripts\python.exe -m twine upload --repository pypi --username __token__ dist/codelens-0.1.0-py3-none-any.whl dist/codelens-0.1.0.tar.gz
```

When asked for a password or API token, paste the **PyPI token from step 2** and
press Enter. Hidden input may show no characters; that is normal. The command
uploads the two package files. It does not need your OpenAI key.

**Done when:** both uploads finish successfully and you can open your
[CodeLens page on PyPI](https://pypi.org/project/codelens/).

If the name is unavailable, permission is denied, or the version already exists,
stop and share the error message without the token. A missing public project page
does not reserve the name. Do not rename the archive files or delete/re-upload an
existing version to get around an error.

## 5. Check that other people can install it

After the upload succeeds, run:

```powershell
.\.venv-release\Scripts\python.exe scripts/check_published.py pypi
```

This downloads the published wheel, checks that it is exactly the file we prepared,
installs it in a fresh temporary environment, and checks the commands and local
graph workflow. It does not use OpenAI or modify your saved projects.

**Done when:** the command ends with `PASS: codelens 0.1.0 from pypi installed and worked.`

If it fails, share the error so we can check it. After it passes, people using a
supported Python version can install with `pip install codelens`, then run
`codelens init`. Link them to the [installation guide](install-upgrade-uninstall.md).

## 6. Add the release to your GitHub page

Open your [repository's Releases page](https://github.com/SajalDBansal/Codelens/releases)
and choose **Draft a new release**. Use:

| Field | What to enter |
| --- | --- |
| Tag | Create `v0.1.0`, targeting the commit pushed in step 3. |
| Title | `CodeLens 0.1.0` |
| Description | Copy [the prepared release notes](release-notes-0.1.0.md). Add your PyPI link after step 5 passes. |
| Attachments | The `.whl`, `.tar.gz`, and `SHA256SUMS` files from `dist/`. |
| Pre-release | Select it to label this initial version as a beta. |

Review the draft, then click **Publish release**. Finally, record the actual date
and URLs and update the changelog's pending-publication wording.

You are finished when the PyPI installation check passes and your GitHub release
contains the download files. No CI pipeline is needed.

For later builds or version changes, use the [maintenance reference](release-maintenance.md).
Publishing reference: [PyPA packaging guide](https://packaging.python.org/en/latest/tutorials/packaging-projects/).
