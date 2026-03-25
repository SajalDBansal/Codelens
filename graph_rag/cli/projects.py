"""User-level project directory and repository-local manifests (no code in the registry)."""

import os
import uuid
from pathlib import Path
from typing import Literal

from pydantic import Field

from ..storage import GraphStore
from ..storage.sqlite_schema import SCHEMA_VERSION
from .state import Record, StateError, home, locked, now, read_json, write_json


class Project(Record):
    schema_version: Literal[1] = 1
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    name: str
    root: str
    active_database: str
    created_at: str = Field(default_factory=now)
    last_used_at: str | None = None
    last_indexed_at: str | None = None
    status: Literal["new", "indexing", "ready", "failed"] = "new"
    embedding_model: str | None = None
    embedding_dimensions: int | None = None
    last_reasoning_model: str | None = None
    last_summary_model: str | None = None
    statistics: dict[str, int] = Field(default_factory=dict)
    pending_database: str | None = None
    pending_model: str | None = None
    pending_dimensions: int | None = None
    previous_databases: list[str] = Field(default_factory=list)

    @property
    def manifest_path(self) -> Path:
        return Path(self.root) / ".codelens" / "project.json"

    @property
    def database(self) -> Path:
        return (Path(self.root) / self.active_database).resolve()


class Entry(Record):
    id: str
    name: str
    root: str
    last_used_at: str | None = None


class Registry(Record):
    schema_version: Literal[1] = 1
    active_project_id: str | None = None
    projects: list[Entry] = Field(default_factory=list)


def same_path(left, right) -> bool:
    return os.path.normcase(str(Path(left).resolve())) == os.path.normcase(
        str(Path(right).resolve())
    )


def database_reference(root: Path, database: Path) -> str:
    try:
        return database.relative_to(root).as_posix()
    except ValueError:
        return str(database)


def registry() -> Registry:
    return read_json(home() / "projects.json", Registry) or Registry()


def remember(project: Project, *, active=False) -> None:
    with locked(home() / ".state.lock"):
        data = registry()
        existing = next((e for e in data.projects if e.id == project.id), None)
        if existing and not same_path(existing.root, project.root):
            raise StateError(
                "This project ID is registered at another path. Copied graphs need a separate project."
            )
        data.projects = [e for e in data.projects if e.id != project.id]
        data.projects.append(
            Entry(
                id=project.id,
                name=project.name,
                root=project.root,
                last_used_at=project.last_used_at,
            )
        )
        if active or data.active_project_id is None:
            data.active_project_id = project.id
        write_json(home() / "projects.json", data)


def save_project(project: Project) -> None:
    write_json(project.manifest_path, project)


def load_project(root: Path) -> Project:
    project = read_json(root / ".codelens" / "project.json", Project)
    if project is None:
        raise StateError(f'No project metadata at {root}. Run codelens projects add "{root}".')
    if not same_path(project.root, root):
        raise StateError(
            f"This graph belongs to {project.root}; its repository was moved or copied."
        )
    return project


def inspect_database(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        with GraphStore(path, read_only=True) as store:
            meta = store.index_metadata()
        if meta["version"] != SCHEMA_VERSION:
            raise StateError(f"Unsupported graph schema {meta['version']} in {path}.")
        return meta
    except (RuntimeError, ValueError) as error:
        raise StateError(f"Cannot inspect graph {path}: {error}") from error


def ensure_gitignore(root: Path) -> None:
    """Exclude project-local state without rewriting the user's ignore file.

    Call while holding the project lock so concurrent CodeLens commands cannot
    append duplicate entries. Existing bytes and line endings are preserved.
    """
    path = root / ".gitignore"
    if path.is_symlink():
        raise StateError(f"Cannot update a symlinked .gitignore: {path}. Use a regular file.")
    try:
        content = path.read_bytes()
    except FileNotFoundError:
        content = b""
    patterns = {
        b".codelens",
        b".codelens/",
        b"/.codelens",
        b"/.codelens/",
        b"**/.codelens",
        b"**/.codelens/",
    }
    lines = content.removeprefix(b"\xef\xbb\xbf").splitlines()
    if any(line.rstrip(b" \t") in patterns for line in lines):
        return
    newline = b"\r\n" if b"\r\n" in content else b"\n"
    separator = newline if content and not content.endswith(b"\n") else b""
    with path.open("ab") as stream:
        stream.write(separator + b".codelens/" + newline)


def add_project(root: Path, *, name=None, db=None) -> Project:
    root = root.expanduser().resolve()
    if not root.is_dir():
        raise StateError(f"Project source folder is unavailable: {root}")
    with locked(root / ".codelens" / "project.lock"):
        manifest = root / ".codelens" / "project.json"
        if manifest.exists():
            project = load_project(root)
            if db and not same_path(project.database, db):
                raise StateError(
                    "This project already has an active database. Omit --db or use its active path."
                )
            if name:
                project.name = name
        else:
            database = Path(db).expanduser().resolve() if db else root / ".codelens" / "graph.sqlite"
            meta = inspect_database(database)
            if meta and meta["root"] and not same_path(meta["root"], root):
                raise StateError(f"The selected graph belongs to {meta['root']}, not {root}.")
            project = Project(
                name=name or root.name,
                root=str(root),
                active_database=database_reference(root, database),
            )
            if meta:
                # An imported graph's previous indexing completion is not known.
                # A normal ask will run the recovery-aware incremental check first.
                project.embedding_model = meta["model"]
                project.embedding_dimensions = meta["dimensions"]
        ensure_gitignore(root)
        save_project(project)
        remember(project)
    return project


def entry_for(selector: str) -> Entry:
    entries = registry().projects
    exact = [
        e for e in entries if e.id == selector or e.name == selector or same_path(e.root, selector)
    ]
    found = exact or [e for e in entries if e.id.startswith(selector)]
    if len(found) != 1:
        raise StateError("Project is ambiguous or unknown. Use the ID from codelens projects list.")
    return found[0]


def resolve_project(selector=None, *, db=None) -> Project:
    if selector:
        project = load_project(Path(entry_for(selector).root))
    elif db:
        database = Path(db).expanduser().resolve()
        meta = inspect_database(database)
        if meta is None:
            raise StateError(f"Graph does not exist: {database}. Run codelens embed first.")
        root = Path(meta["root"]) if meta["root"] else database.parent.parent
        project = add_project(root, db=database)
    else:
        project = None
        for root in [Path.cwd(), *Path.cwd().parents]:
            if (root / ".codelens" / "project.json").exists():
                project = load_project(root)
                break
            if (root / ".codelens" / "graph.sqlite").is_file():
                project = add_project(root)
                break
        if project is None:
            active = registry().active_project_id
            if active is None:
                raise StateError(
                    "No project selected. Run codelens embed PATH or codelens projects use ID."
                )
            project = load_project(Path(entry_for(active).root))
    if db and not same_path(project.database, Path(db).expanduser()):
        raise StateError("--db differs from this project's active database. Run codelens status.")
    return project


def forget(selector: str) -> None:
    with locked(home() / ".state.lock"):
        entry = entry_for(selector)
        data = registry()
        data.projects = [e for e in data.projects if e.id != entry.id]
        if data.active_project_id == entry.id:
            data.active_project_id = None
        write_json(home() / "projects.json", data)
