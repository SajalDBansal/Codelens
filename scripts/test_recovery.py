"""Run local recovery tests without Docker or provider credentials."""

from pathlib import Path
import subprocess
import sys

if __name__ == "__main__":
    raise SystemExit(
        subprocess.call(
            [sys.executable, "-m", "pytest", "tests/test_indexing_recovery.py", "-q"],
            cwd=Path(__file__).resolve().parents[1],
        )
    )
