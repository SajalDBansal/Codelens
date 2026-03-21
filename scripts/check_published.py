"""Check a published wheel in a temporary environment; never upload anything.

Run after uploading: python scripts/check_published.py pypi
Also supports testpypi when you own that distribution name on the test index.
"""

import argparse
import hashlib
import subprocess
import sys
import tempfile
import tomllib
import venv
from pathlib import Path

from prepare_release import ROOT, run

INDEX_URLS = {
    "pypi": "https://pypi.org/simple/",
    "testpypi": "https://test.pypi.org/simple/",
}


def verify_download(expected, downloaded):
    """Do not install a package unless its bytes match our checked release wheel."""
    expected_hash = hashlib.sha256(expected.read_bytes()).digest()
    downloaded_hash = hashlib.sha256(downloaded.read_bytes()).digest()
    if expected_hash != downloaded_hash:
        raise RuntimeError(
            "The downloaded wheel differs from your local release. "
            "Check the package name, version, and uploaded files before continuing."
        )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("index", choices=INDEX_URLS)
    args = parser.parse_args()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    wheels = list((ROOT / "dist").glob("*.whl"))
    if len(wheels) != 1:
        raise RuntimeError("Keep exactly one checked release wheel in dist.")

    with tempfile.TemporaryDirectory(prefix="codelens-published-") as directory:
        temporary = Path(directory)
        downloads = temporary / "downloads"
        downloads.mkdir()
        run(sys.executable, "-m", "pip", "--isolated", "download", "--no-deps",
            "--only-binary=:all:", "--no-cache-dir", "--index-url", INDEX_URLS[args.index],
            "--dest", downloads, f"{project['name']}=={project['version']}")
        downloaded = list(downloads.glob("*.whl"))
        if len(downloaded) != 1:
            raise RuntimeError("Expected one published wheel.")
        verify_download(wheels[0], downloaded[0])
        print("PASS: downloaded wheel exactly matches the local release.", flush=True)

        environment = temporary / "environment"
        venv.EnvBuilder(with_pip=True).create(environment)
        interpreter = environment / (
            "Scripts/python.exe" if sys.platform == "win32" else "bin/python"
        )
        # Dependencies always come from real PyPI, including during a TestPyPI check.
        run(interpreter, "-m", "pip", "--isolated", "install", "--index-url",
            "https://pypi.org/simple/", downloaded[0])
        run(interpreter, "-m", "pip", "check")
        run(sys.executable, ROOT / "scripts/check_distribution.py", "--python", interpreter)
    print(f"PASS: {project['name']} {project['version']} from {args.index} installed and worked.")
    print("No OpenAI calls or uploads were made.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"\nPublished-package check stopped: {error}", file=sys.stderr)
        sys.exit(1)
