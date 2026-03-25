"""Immutable float32 segments. SQLite publishes references only after durable writes.

An interrupted write can leave an unreferenced file, never a half-published
vector. Cleanup is explicit and runs under the project command lock. Open
memory maps are closed before deleting segments (required on Windows).
"""

import hashlib
import os
import tempfile
from pathlib import Path

import numpy as np


class VectorFiles:
    def __init__(self, directory: Path):
        self.directory = directory
        self._maps = {}

    def save(self, vectors, dimensions):
        with np.errstate(over="ignore", invalid="ignore"):
            matrix = np.asarray(vectors, dtype="<f4")
        if matrix.ndim != 2 or matrix.shape[1] != dimensions:
            raise ValueError(f"Expected {dimensions} embedding dimensions")
        # Validate the persisted representation, including float32 overflow/underflow.
        if not np.isfinite(matrix).all() or np.any(~np.any(matrix != 0, axis=1)):
            raise ValueError("Embedding must be finite and have non-zero magnitude")
        digest = hashlib.sha256(str(matrix.shape).encode() + matrix.tobytes()).hexdigest()
        name = digest + ".npy"
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self.directory / name
        if not destination.exists():
            fd, temporary = tempfile.mkstemp(prefix=".segment-", suffix=".tmp", dir=self.directory)
            try:
                with os.fdopen(fd, "wb") as stream:
                    np.save(stream, matrix, allow_pickle=False)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
                if os.name != "nt":
                    directory_fd = os.open(self.directory, os.O_RDONLY)
                    try:
                        os.fsync(directory_fd)
                    finally:
                        os.close(directory_fd)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return name

    def read(self, name, dimensions):
        if (
            len(name) != 68
            or not name.endswith(".npy")
            or any(c not in "0123456789abcdef" for c in name[:-4])
        ):
            raise ValueError("Invalid vector segment reference")
        if name not in self._maps:
            try:
                matrix = np.load(self.directory / name, mmap_mode="r", allow_pickle=False)
            except (OSError, ValueError) as error:
                raise ValueError(
                    f"Cannot read vector segment {name}; restore the index or rebuild"
                ) from error
            if matrix.ndim != 2 or matrix.shape[1] != dimensions or matrix.dtype != np.dtype("<f4"):
                if getattr(matrix, "_mmap", None) is not None:
                    matrix._mmap.close()
                raise ValueError("Vector segment shape or dtype does not match the index")
            checksum = hashlib.sha256(str(matrix.shape).encode())
            checksum.update(memoryview(matrix).cast("B"))
            if checksum.hexdigest() != name[:-4]:
                matrix._mmap.close()
                raise ValueError("Vector segment checksum mismatch; restore the index or rebuild")
            self._maps[name] = matrix
        return self._maps[name]

    def close(self):
        for matrix in self._maps.values():
            matrix._mmap.close()
        self._maps.clear()

    def prune(self, active_names):
        self.close()
        removed = 0
        if self.directory.exists():
            for path in self.directory.iterdir():
                if (
                    path.is_file()
                    and (path.suffix == ".npy" or path.name.startswith(".segment-"))
                    and path.name not in active_names
                ):
                    path.unlink()
                    removed += 1
        return removed
