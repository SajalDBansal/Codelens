"""Exclusive maintenance of a project's local index."""

from pathlib import Path

from ..storage import GraphStore
from .projects import load_project
from .state import locked


def compact_project(project):
    root = Path(project.root)
    with locked(root / ".codelens" / "project.lock"):
        project = load_project(root)
        with (
            locked(Path(str(project.database) + ".command.lock")),
            GraphStore(project.database) as store,
        ):
            store.check_integrity()
            result = store.compact()
            store.check_integrity()
            return result
