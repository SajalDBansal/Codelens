"""Small, versioned JSON files; credentials never belong in these files."""

import json
import os
import tempfile
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime
from pathlib import Path

from filelock import FileLock, Timeout
from pydantic import BaseModel, ConfigDict, ValidationError


class StateError(ValueError):
    """An actionable local configuration or project error."""


class Record(BaseModel):
    model_config = ConfigDict(extra="forbid")


def now() -> str:
    return datetime.now(UTC).isoformat()


_config_directory: ContextVar[Path | None] = ContextVar("codelens_config_directory", default=None)


def home() -> Path:
    return (_config_directory.get() or Path.home() / ".codelens").expanduser().resolve()


@contextmanager
def config_directory(path: str | Path | None):
    """An explicit CLI/library override; no application environment variables."""
    token = _config_directory.set(Path(path).expanduser().resolve()) if path is not None else None
    try:
        yield
    finally:
        if token is not None:
            _config_directory.reset(token)


def read_json(path: Path, model: type[Record]):
    if not path.exists():
        return None
    try:
        return model.model_validate_json(path.read_text(encoding="utf-8"))
    except (ValidationError, ValueError) as error:
        # Do not include the file's raw contents in an error (it may be hand-edited).
        raise StateError(
            f"Invalid or unsupported metadata: {path}. Restore or repair this file."
        ) from error


def write_json(path: Path, record: Record) -> None:
    """Replace a complete document atomically. Caller holds the appropriate lock."""
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
            json.dump(record.model_dump(mode="json"), stream, indent=2, ensure_ascii=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def locked(path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with FileLock(str(path), timeout=0):
            yield
    except Timeout as error:
        raise StateError(
            f"Another command is using {path.parent}. Wait for it to finish."
        ) from error
