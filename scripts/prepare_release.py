"""Run manual release checks and build CodeLens; never upload or publish anything.

Setup: python -m pip install -e ".[dev]" "build>=1,<2" "twine>=6,<7"
Run:   python scripts/prepare_release.py
Use a virtual environment and an empty/missing dist directory. No OpenAI calls
are made; dependency installation and first parser use can need internet access.
"""

import argparse
import hashlib
import importlib.util
import json
import platform
import shutil
import subprocess
import sys
import tempfile
import tomllib
import venv
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*command, capture=False):
    print("\n>", subprocess.list2cmdline([str(part) for part in command]), flush=True)
    return subprocess.run(
        [str(part) for part in command], cwd=ROOT, check=True,
        text=True, stdout=subprocess.PIPE if capture else None,
    ).stdout


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    if sys.prefix == sys.base_prefix:
        raise RuntimeError("Use a virtual environment; see docs/releasing.md, step 2.")
    if not (3, 11) <= sys.version_info[:2] < (3, 14):
        raise RuntimeError("Use supported CPython 3.11-3.13; 3.12 is recommended.")
    for module in ("pytest", "build", "twine"):
        if importlib.util.find_spec(module) is None:
            raise RuntimeError(f"Missing {module}. Complete the setup in docs/releasing.md.")

    dist = ROOT / "dist"
    if dist.exists() and any(dist.iterdir()):
        raise RuntimeError("dist is not empty. Rename it as described in docs/releasing.md.")
    project = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]

    # Fail immediately if dependencies, tests, building, or metadata checks fail.
    run(sys.executable, "-m", "pip", "check")
    # Archived checkouts under planning/ must not be collected as package tests.
    run(sys.executable, "-m", "pytest", "-q", "tests")
    run(sys.executable, "-m", "build")  # Builds the wheel from the source archive.
    wheels = list(dist.glob("*.whl"))
    sources = list(dist.glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise RuntimeError("Expected exactly one wheel and one source archive.")
    artifacts = [wheels[0], sources[0]]
    run(sys.executable, "-m", "twine", "check", "--strict", *artifacts)

    # A fresh environment proves installation from this wheel, not editable source.
    with tempfile.TemporaryDirectory(prefix="codelens-release-") as directory:
        wheel_env = Path(directory) / "wheel-env"
        venv.EnvBuilder(with_pip=True).create(wheel_env)
        executable = wheel_env / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")
        run(executable, "-m", "pip", "install", "--index-url", "https://pypi.org/simple/", wheels[0])
        run(executable, "-m", "pip", "check")
        run(sys.executable, ROOT / "scripts/check_distribution.py", "--python", executable)
        installed = json.loads(run(executable, "-m", "pip", "list", "--format=json", capture=True))

    hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in artifacts}
    (dist / "SHA256SUMS").write_text(
        "".join(f"{digest}  {name}\n" for name, digest in hashes.items()), encoding="utf-8"
    )
    commit = None
    dirty = None
    if shutil.which("git"):
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=False
        )
        if result.returncode == 0:
            commit = result.stdout.strip()
            status = subprocess.run(
                ["git", "status", "--porcelain"], cwd=ROOT, capture_output=True, text=True,
                check=False,
            )
            dirty = bool(status.stdout.strip()) if status.returncode == 0 else None
    report = {
        "status": "passed",
        "checked_at_utc": datetime.now(UTC).isoformat(),
        "project": project["name"],
        "version": project["version"],
        "python": platform.python_version(),
        "system": platform.system(),
        "machine": platform.machine(),
        "git_commit": commit,
        "working_tree_dirty": dirty,
        "checks": ["dependencies", "pytest", "sdist and wheel", "twine --strict",
                   "fresh wheel install", "archive contents", "CLI entry points", "graph smoke"],
        "artifacts_sha256": hashes,
        "installed_wheel_packages": installed,
        "live_openai_test": False,
        "published": False,
    }
    (dist / "validation.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print("\nPASS: release files, SHA256SUMS, and validation.json are in dist.")
    print("Nothing was uploaded. Continue with docs/releasing.md.")


if __name__ == "__main__":
    try:
        main()
    except (RuntimeError, subprocess.CalledProcessError) as error:
        print(f"\nRelease preparation stopped: {error}", file=sys.stderr)
        sys.exit(1)
