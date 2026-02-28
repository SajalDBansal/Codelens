"""
pipeline/import_resolver.py
===========================

"This file says `from .db import GraphStore` - which file on disk is that?"

WHY THIS IS ITS OWN FILE
------------------------
Turning an import statement into a file path is different in every language
and full of special cases (package roots, path aliases in tsconfig.json, Go
module paths, `__init__.py`, index.ts...). Doing it PERFECTLY is a large
project on its own.

So we do the easy 80% here, keep it isolated in one small file, and accept
that some imports will not resolve. An import we cannot resolve simply
produces no IMPORTS edge - which is a missing edge, not a WRONG edge. That
trade is deliberate: a wrong edge would send the retriever off to unrelated
code and quietly poison answers, which is far worse than a missing one.
"""

import re
from typing import Optional


# Matches the module path inside a quoted import, e.g.
#   import { x } from "./db"     -> ./db
#   use crate::storage;          -> (no quotes, handled separately)
_QUOTED_RE = re.compile(r"""['"]([^'"]+)['"]""")

# Matches Python's two import forms.
_PY_FROM_RE = re.compile(r"^\s*from\s+([.\w]+)\s+import\b")
_PY_IMPORT_RE = re.compile(r"^\s*import\s+([\w.]+)")


def _normalise(path: str) -> str:
    """
    Clean up a path so it can be compared against our stored file paths.

    We always store paths repo-root relative with forward slashes, and we
    resolve any ".." segments here so "src/auth/../db.py" becomes "src/db.py".
    """
    parts: list[str] = []
    for part in path.replace("\\", "/").split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if parts:
                parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _candidates_for_module(base: str) -> list[str]:
    """
    Given a path with no extension, list the files it could actually be.

        "src/db"  ->  src/db.py, src/db/__init__.py, src/db.ts,
                      src/db/index.ts, src/db.go, ...

    We try each and keep the first one that exists in the repository.
    """
    return [
        f"{base}.py",
        f"{base}/__init__.py",
        f"{base}.ts",
        f"{base}.tsx",
        f"{base}/index.ts",
        f"{base}/index.tsx",
        f"{base}.js",
        f"{base}.jsx",
        f"{base}/index.js",
        f"{base}.go",
        f"{base}.rs",
        f"{base}/mod.rs",
        f"{base}.java",
    ]


def resolve_import(
    import_text: str,
    importing_file: str,
    known_files: set[str],
    language: str,
) -> Optional[str]:
    """
    Try to turn one import statement into a repo-relative file path.

    `known_files` is every file path currently in the graph - we only return
    a path that actually exists there, so we can never invent an edge to a
    file that is not real.

    Returns None when we cannot work it out, which is normal for third-party
    imports (`import os`, `from react import ...`) - those genuinely are not
    files in this repository.
    """
    # The folder the importing file sits in, e.g. "src/auth" for
    # "src/auth/user.py". Needed for relative imports.
    folder = importing_file.rsplit("/", 1)[0] if "/" in importing_file else ""

    if language == "python":
        module = _python_module_from(import_text)
        if module is None:
            return None

        if module.startswith("."):
            # Relative import. One leading dot = this folder, two = go up one
            # level, three = up two, and so on.
            dots = len(module) - len(module.lstrip("."))
            remainder = module.lstrip(".").replace(".", "/")
            base_parts = folder.split("/") if folder else []
            # The first dot means "here", so we only climb for the extra ones.
            climb = dots - 1
            if climb > 0:
                base_parts = base_parts[:-climb] if climb <= len(base_parts) else []
            base = "/".join([part for part in base_parts if part] + ([remainder] if remainder else []))
        else:
            # Absolute import: treat it as a path from the repository root.
            # This works for imports inside the project and simply fails to
            # match for standard-library or third-party modules.
            base = module.replace(".", "/")

        return _first_existing(_normalise(base), known_files)

    # --- JavaScript / TypeScript / Go / Java: the path is in quotes -------
    quoted = _QUOTED_RE.search(import_text)
    if quoted:
        target = quoted.group(1)
        if target.startswith("."):
            # "./db" or "../shared/types" - relative to the current folder.
            base = _normalise(f"{folder}/{target}")
        else:
            # A bare package name ("react", "fmt", "github.com/x/y").
            # Could be third-party, or could be an absolute path from the
            # repo root. We try the repo-root reading and accept it only if
            # a real file matches.
            base = _normalise(target)
        return _first_existing(base, known_files)

    # --- Rust: `use crate::storage::graph_store;` --------------------------
    if language == "rust" and "::" in import_text:
        cleaned = import_text.replace("use ", "").strip(" ;")
        # `crate::` means the crate root, which for our purposes is "src".
        cleaned = cleaned.replace("crate::", "src::")
        base = _normalise(cleaned.split("::{")[0].replace("::", "/"))
        return _first_existing(base, known_files)

    return None


def _python_module_from(import_text: str) -> Optional[str]:
    """Pull the module path out of either Python import form."""
    match = _PY_FROM_RE.match(import_text)
    if match:
        return match.group(1)
    match = _PY_IMPORT_RE.match(import_text)
    if match:
        return match.group(1)
    return None


def _first_existing(base: str, known_files: set[str]) -> Optional[str]:
    """Return the first candidate path that is really in the repository."""
    if not base:
        return None
    # The import might already include the extension (common in Go/JS).
    if base in known_files:
        return base
    for candidate in _candidates_for_module(base):
        if candidate in known_files:
            return candidate
    return None
