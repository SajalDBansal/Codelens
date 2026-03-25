"""Exact source references with compressed snapshots for stored-version retrieval.

Offsets are Unicode character offsets into UTF-8 decoded with errors='replace',
version 1. A reference is created only after an exact substring match within the
reported source lines. Synthetic/transformed text uses a deduplicated blob.
"""

import hashlib
import zlib
from collections import OrderedDict
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


class SourceText:
    def __init__(self, connection, root):
        self.connection = connection
        self.root = root
        self.parsed = OrderedDict()
        self.loaded = OrderedDict()

    def _cache(self, cache, key, value):
        cache[key] = value
        cache.move_to_end(key)
        while len(cache) > 32:
            cache.popitem(last=False)
        return value

    def put_blob(self, raw):
        key = digest(raw)
        self.connection.execute(
            "INSERT OR IGNORE INTO blobs VALUES (?,?)", (key, zlib.compress(raw))
        )
        return key

    def register(self, path, raw):
        key = self.put_blob(raw)
        self._cache(self.parsed, path, (key, raw.decode("utf-8", errors="replace")))
        return key

    def reference(self, path, text, start_line, end_line):
        body_hash = digest(text.encode("utf-8"))
        source = self.parsed.get(path)
        if source is not None:
            source_hash, decoded = source
            lines = decoded.splitlines(keepends=True)
            start = sum(map(len, lines[: max(0, start_line - 1)]))
            stop = sum(map(len, lines[:end_line]))
            offset = decoded.find(text, start, stop)
            if offset >= 0:
                return source_hash, offset, offset + len(text), None, body_hash
        return None, None, None, self.put_blob(text.encode("utf-8")), body_hash

    def blob(self, key):
        row = self.connection.execute("SELECT content FROM blobs WHERE hash=?", (key,)).fetchone()
        if row is None:
            raise ValueError("Source snapshot is missing; restore the index or rebuild")
        try:
            raw = zlib.decompress(row[0])
        except zlib.error as error:
            raise ValueError("Source snapshot is corrupt; restore the index or rebuild") from error
        if digest(raw) != key:
            raise ValueError("Source snapshot checksum mismatch")
        return raw

    def hydrate(self, row):
        source_hash = row["source_hash"]
        if source_hash:
            cache_key = (row["file_path"], source_hash)
            if cache_key in self.loaded:
                text = self.loaded[cache_key]
            else:
                raw = None
                if self.root:
                    candidate = (Path(self.root) / row["file_path"]).resolve()
                    if candidate.is_relative_to(Path(self.root).resolve()):
                        try:
                            current = candidate.read_bytes()
                            if digest(current) == source_hash:
                                raw = current
                        except OSError:
                            pass
                if raw is None:
                    raw = self.blob(source_hash)
                text = self._cache(self.loaded, cache_key, raw.decode("utf-8", errors="replace"))
            result = text[row["start_char"] : row["end_char"]]
        elif row["text_hash"]:
            result = self.blob(row["text_hash"]).decode("utf-8")
        else:
            result = ""
        if row["body_hash"] and digest(result.encode("utf-8")) != row["body_hash"]:
            raise ValueError("Source reference does not reconstruct the indexed text")
        return result
