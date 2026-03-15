"""Check a build and exercise its installed wheel outside the source checkout.

First run `python -m build`, then install the wheel into a separate environment.
Pass that environment's interpreter with --python. No OpenAI calls are made.
"""

import argparse
import subprocess
import tarfile
import tempfile
import tomllib
import zipfile
from email.parser import BytesParser
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", type=Path, required=True)
    args = parser.parse_args()
    interpreter = args.python.resolve(strict=True)
    root = Path(__file__).resolve().parents[1]
    project = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]
    wheels = list((root / "dist").glob("*.whl"))
    sources = list((root / "dist").glob("*.tar.gz"))
    if len(wheels) != 1 or len(sources) != 1:
        raise RuntimeError("Use a fresh dist directory containing one wheel and one source archive.")

    with zipfile.ZipFile(wheels[0]) as archive:
        names = set(archive.namelist())
        metadata_name = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = BytesParser().parsebytes(archive.read(metadata_name))
        assert metadata["Name"] == project["name"]
        assert metadata["Version"] == project["version"]
        # Build tools may reorder clauses: >=3.11,<3.14 and <3.14,>=3.11 are equal.
        actual_python = {clause.strip() for clause in metadata["Requires-Python"].split(",")}
        expected_python = {clause.strip() for clause in project["requires-python"].split(",")}
        assert actual_python == expected_python
        for source in (root / "graph_rag").rglob("*.py"):
            relative = source.relative_to(root).as_posix()
            assert archive.read(relative) == source.read_bytes(), relative
        assert not any(".codelens/" in name or name.endswith((".env", ".lbug")) for name in names)

    # Support files are part of the sdist so its documented workflows also work.
    with tarfile.open(sources[0], "r:gz") as archive:
        members = {name.partition("/")[2]: name for name in archive.getnames()}
        assert not any(name.startswith(".github/workflows/") for name in members)
        for folder in ("docs", "tests", "scripts", "evals", "examples", "constraints"):
            for source in (root / folder).rglob("*"):
                if source.is_file() and source.suffix in {".py", ".md", ".json", ".txt", ".yml"}:
                    relative = source.relative_to(root).as_posix()
                    with archive.extractfile(members[relative]) as stream:
                        assert stream.read() == source.read_bytes(), relative

    # -I and a temporary cwd prevent the checkout from hiding an incomplete wheel.
    with tempfile.TemporaryDirectory(prefix="codelens-wheel-") as directory:
        commands = [
            [str(interpreter), "-I", "-c",
             ("from pathlib import Path; import graph_rag, sys; "
             "assert Path(graph_rag.__file__).resolve().is_relative_to(Path(sys.prefix).resolve()); "
             "print('Wheel import:', graph_rag.__file__)")],
            [str(interpreter), "-I", "-m", "graph_rag", "--help"],
        ]
        for entry in ("codelens", "graph-rag-index", "graph-rag-ask"):
            executable = interpreter.parent / (entry + (".exe" if interpreter.suffix == ".exe" else ""))
            commands.append([str(executable), "--help"])
        commands.append([str(interpreter), "-I", str(root / "scripts" / "smoke_local.py")])
        for command in commands:
            subprocess.run(command, cwd=directory, check=True)
    print("PASS: archive contents, installed wheel imports, CLI entry points, and graph smoke test")


if __name__ == "__main__":
    main()
