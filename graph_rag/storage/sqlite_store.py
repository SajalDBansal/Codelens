"""SQLite graph metadata, persistent BM25, and immutable float32 vector files."""

import itertools
import json
import math
import os
import sqlite3
import threading
from collections import Counter, OrderedDict, defaultdict
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from .. import config
from ..embedding.embedder import (
    build_doc_embedding_text,
    build_embedding_text,
    build_summary_embedding_text,
    embedding_fingerprint,
)
from ..parsing import language_family
from . import schema
from .search import _STOP_WORDS, _describe_connection, _search_tokens
from .source_text import SourceText
from .sqlite_schema import DDL, SCHEMA_VERSION
from .vector_files import VectorFiles


def _component_sizes(adjacency):
    seen, sizes = set(), []
    for start in adjacency:
        if start in seen:
            continue
        stack, size = [start], 0
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            size += 1
            stack.extend(set(adjacency.get(node, ())) - seen)
        sizes.append(size)
    return sizes


def _json(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


class Rows(list):
    def single(self):
        if len(self) > 1:
            raise ValueError("Expected at most one row")
        return self[0] if self else None

    def consume(self):
        return None


class SQLSession:
    def __init__(self, store):
        self.store = store

    def run(self, statement, **parameters):
        return Rows(self.store.query(statement, **parameters))

    def execute_write(self, callback):
        with self.store.transaction():
            return callback(self)


class GraphStore:
    def __init__(self, path, *, repo_path=None, read_only=False):
        self.path = Path(path).expanduser().resolve()
        self.read_only = read_only
        self.repo_path = Path(repo_path).resolve() if repo_path is not None else None
        if read_only and not self.path.is_file():
            raise FileNotFoundError(self.path)
        if self.path.is_file():
            with self.path.open("rb") as stream:
                header = stream.read(16)
            if header and header != b"SQLite format 3\x00":
                raise ValueError(
                    f"{self.path} is not a SQLite index. Choose a new index path or restore a valid backup."
                )
        if not read_only:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._connection = sqlite3.connect(
            self.path.as_uri() + ("?mode=ro" if read_only else "?mode=rwc"),
            uri=True,
            isolation_level=None,
            check_same_thread=False,
        )
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA foreign_keys=ON")
        self._connection.execute("PRAGMA busy_timeout=5000")
        if not read_only:
            self._connection.execute("PRAGMA synchronous=FULL")
        self._lock = threading.RLock()
        self._vectors = VectorFiles(Path(str(self.path) + ".vectors"))
        self._norms = {}
        self._keyword_cache = OrderedDict()
        self._keyword_version = None
        self.dimensions = config.EMBEDDING_DIM
        source_root = self.repo_path
        try:
            meta = self.index_metadata()
            self.dimensions = meta["dimensions"]
            source_root = source_root or (Path(meta["root"]) if meta["root"] else None)
        except sqlite3.OperationalError:
            pass
        except BaseException:
            self.close()
            raise
        self._sources = SourceText(self._connection, source_root)

    @classmethod
    def from_config(cls, repo_path=None, db_path=None):
        root = Path(repo_path or Path.cwd()).resolve()
        return cls(db_path or root / ".codelens" / "graph.sqlite", repo_path=repo_path)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()

    def close(self):
        with self._lock:
            self._vectors.close()
            self._norms.clear()
            self._keyword_cache.clear()
            if self._connection is not None:
                self._connection.close()
                self._connection = None

    @contextmanager
    def _session(self):
        with self._lock:
            yield SQLSession(self)

    @contextmanager
    def transaction(self):
        with self._lock:
            nested = self._connection.in_transaction
            self._connection.execute("SAVEPOINT operation" if nested else "BEGIN IMMEDIATE")
            try:
                yield
                self._connection.execute("RELEASE operation" if nested else "COMMIT")
            except BaseException:
                self._connection.execute("ROLLBACK TO operation" if nested else "ROLLBACK")
                if nested:
                    self._connection.execute("RELEASE operation")
                raise
            finally:
                self._keyword_cache.clear()

    def query(self, statement, **parameters):
        """Execute parameterized SQL against the local index."""
        with self._lock:
            changes = self._connection.total_changes
            rows = [dict(row) for row in self._connection.execute(statement, parameters)]
            if self._connection.total_changes != changes:
                self._keyword_cache.clear()
            return rows

    def verify_connection(self):
        self.query("SELECT 1 AS ok")

    def index_metadata(self):
        rows = self.query(
            "SELECT model,dimensions,schema_version AS version,repo_root AS root FROM index_meta WHERE id=1"
        )
        if not rows:
            raise ValueError("This index has no embedding metadata. Run codelens embed first.")
        return rows[0]

    def ensure_schema(self):
        if self.read_only:
            self._check_embedding_model()
            return
        with self._lock:
            self._connection.executescript(DDL)
            with self.transaction():
                self._connection.execute(
                    "INSERT OR IGNORE INTO index_meta VALUES(1,?,?,?,?)",
                    (
                        config.EMBEDDING_MODEL,
                        config.EMBEDDING_DIM,
                        SCHEMA_VERSION,
                        str(self.repo_path) if self.repo_path else None,
                    ),
                )
                self._check_embedding_model()

    def _check_embedding_model(self):
        meta = self.index_metadata()
        if (meta["model"], meta["dimensions"], meta["version"]) != (
            config.EMBEDDING_MODEL,
            config.EMBEDDING_DIM,
            SCHEMA_VERSION,
        ):
            raise ValueError(
                "Index model, dimensions or schema differ; restore the original settings or build a NEW index."
            )
        if self.repo_path:
            if meta["root"] and os.path.normcase(
                str(Path(meta["root"]).resolve())
            ) != os.path.normcase(str(self.repo_path)):
                raise ValueError(f"Index belongs to {meta['root']}, not {self.repo_path}")
            if not meta["root"] and not self.read_only:
                self._connection.execute(
                    "UPDATE index_meta SET repo_root=? WHERE id=1", (str(self.repo_path),)
                )
            self._sources.root = self.repo_path
        self.dimensions = meta["dimensions"]

    def save_source(self, file_path, raw):
        with self.transaction():
            self._sources.register(file_path, raw)

    def _record(self, row):
        data = json.loads(row["data"])
        data.update(id=row["id"], contentHash=row["content_hash"] or "")
        if row["label"] in ("CodeEntity", "Doc", "Summary"):
            data["code" if row["label"] == "CodeEntity" else "text"] = self._sources.hydrate(row)
        return data

    def _object(self, row):
        mapper = {
            "CodeEntity": schema.record_to_entity,
            "Doc": schema.record_to_doc,
            "Summary": schema.record_to_summary,
        }[row["label"]]
        return mapper(self._record(row))

    def _nodes(self, label, ids=None):
        if ids is None:
            return self.query("SELECT * FROM nodes WHERE label=:label ORDER BY id", label=label)
        return self.query(
            "SELECT * FROM nodes WHERE label=:label AND id IN (SELECT value FROM json_each(:ids)) ORDER BY id",
            label=label,
            ids=_json(list(ids)),
        )

    def _hashes(self, label):
        return {r["id"]: r["content_hash"] for r in self._nodes(label)}

    def get_folder_hashes(self):
        return self._hashes("Folder")

    def get_file_hashes(self):
        return self._hashes("File")

    def _block_hashes(self, label, path):
        return {
            r["id"]: r["content_hash"]
            for r in self.query(
                "SELECT id,content_hash FROM nodes WHERE label=:label AND file_path=:path",
                label=label,
                path=path,
            )
        }

    def get_entity_hashes(self, file_path):
        return self._block_hashes("CodeEntity", file_path)

    def get_doc_hashes(self, file_path):
        return self._block_hashes("Doc", file_path)

    def _embedding_hashes(self, label, paths):
        result = {p: {} for p in paths}
        for row in self.query(
            "SELECT id,file_path,embedding_hash,vector_segment FROM nodes WHERE label=:label AND file_path IN (SELECT value FROM json_each(:paths))",
            label=label,
            paths=_json(paths),
        ):
            result[row["file_path"]][row["id"]] = (
                row["embedding_hash"] if row["vector_segment"] else None
            )
        return result

    def get_entity_hashes_for_files(self, file_paths):
        return self._embedding_hashes("CodeEntity", file_paths)

    def get_doc_hashes_for_files(self, file_paths):
        return self._embedding_hashes("Doc", file_paths)

    def get_entity_ids_with_embeddings(self, file_path):
        return {
            k for k, v in self._embedding_hashes("CodeEntity", [file_path])[file_path].items() if v
        }

    def get_doc_ids_with_embeddings(self, file_path):
        return {k for k, v in self._embedding_hashes("Doc", [file_path])[file_path].items() if v}

    def invalidate_hashes(self, file_paths, folder_paths):
        with self.transaction():
            for label, paths in (("File", file_paths), ("Folder", folder_paths)):
                self.query(
                    "UPDATE nodes SET content_hash=NULL WHERE label=:label AND id IN (SELECT value FROM json_each(:paths))",
                    label=label,
                    paths=_json(paths),
                )

    def _save_hashes(self, rows, label):
        with self.transaction():
            self._connection.executemany(
                "UPDATE nodes SET content_hash=? WHERE label=? AND id=?",
                [(r["hash"], label, r["path"]) for r in rows],
            )

    def save_folder_hashes(self, rows):
        self._save_hashes(rows, "Folder")

    def save_file_hashes(self, rows):
        self._save_hashes(rows, "File")

    def _containers(self, rows, label, with_hash):
        with self.transaction():
            for row in rows:
                path = row["path"]
                self._connection.execute(
                    "INSERT OR IGNORE INTO nodes(label,id,path) VALUES(?,?,?)", (label, path, path)
                )
                if with_hash:
                    self._connection.execute(
                        "UPDATE nodes SET content_hash=? WHERE label=? AND id=?",
                        (row.get("hash"), label, path),
                    )
                self._connection.execute(
                    "UPDATE nodes SET language=? WHERE label=? AND id=?",
                    (row.get("language"), label, path),
                )
                if row.get("parent") is not None:
                    self._connection.execute(
                        "INSERT OR IGNORE INTO nodes(label,id,path) VALUES('Folder',?,?)",
                        (row["parent"], row["parent"]),
                    )
                    self._edge("Folder", row["parent"], label, path, "CONTAINS")

    def save_folders(self, rows, with_hash=True):
        self._containers(rows, "Folder", with_hash)

    def save_files(self, rows, with_hash=True):
        self._containers(rows, "File", with_hash)

    def _delete_missing(self, label, paths):
        with self.transaction():
            return self._connection.execute(
                "DELETE FROM nodes WHERE label=? AND path NOT IN (SELECT value FROM json_each(?))",
                (label, _json(paths)),
            ).rowcount

    def delete_missing_folders(self, existing_paths):
        return self._delete_missing("Folder", existing_paths)

    def delete_missing_summaries(self, existing_paths):
        return self._delete_missing("Summary", existing_paths)

    def delete_file(self, file_path):
        with self.transaction():
            self._connection.execute(
                "DELETE FROM nodes WHERE (label IN ('CodeEntity','Doc') AND file_path=?) OR (label='File' AND id=?)",
                (file_path, file_path),
            )

    def _delete_ids(self, label, ids):
        with self.transaction():
            self._connection.execute(
                "DELETE FROM nodes WHERE label=? AND id IN (SELECT value FROM json_each(?))",
                (label, _json(ids)),
            )

    def delete_entities(self, entity_ids):
        self._delete_ids("CodeEntity", entity_ids)

    def delete_docs(self, doc_ids):
        self._delete_ids("Doc", doc_ids)

    def delete_summaries(self, folder_paths):
        with self.transaction():
            self._connection.execute(
                "DELETE FROM nodes WHERE label='Summary' AND path IN (SELECT value FROM json_each(?))",
                (_json(folder_paths),),
            )

    def _save_content(self, items, label, properties, embedding_text):
        with self.transaction():
            for item in items:
                data = properties(item)
                data.pop("embedding", None)
                body = data.pop("code" if label == "CodeEntity" else "text")
                fingerprint = embedding_fingerprint(embedding_text(item))
                source = self._sources.reference(
                    data.get("filePath"), body, data.get("startLine", 1), data.get("endLine", 1)
                )
                self._connection.execute(
                    """
                    INSERT INTO nodes(label,id,file_path,name,kind,language,path,content_hash,data,
                        embedding_input_hash,source_hash,start_char,end_char,text_hash,body_hash)
                    VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) ON CONFLICT(label,id) DO UPDATE SET
                        file_path=excluded.file_path,name=excluded.name,kind=excluded.kind,
                        language=excluded.language,path=excluded.path,content_hash=excluded.content_hash,
                        data=excluded.data,embedding_input_hash=excluded.embedding_input_hash,
                        source_hash=excluded.source_hash,start_char=excluded.start_char,end_char=excluded.end_char,
                        text_hash=excluded.text_hash,body_hash=excluded.body_hash,
                        vector_segment=CASE WHEN nodes.embedding_hash=excluded.embedding_input_hash THEN nodes.vector_segment END,
                        vector_row=CASE WHEN nodes.embedding_hash=excluded.embedding_input_hash THEN nodes.vector_row END,
                        embedding_hash=CASE WHEN nodes.embedding_hash=excluded.embedding_input_hash THEN nodes.embedding_hash END
                    """,
                    (
                        label,
                        item.id,
                        data.get("filePath"),
                        data.get("name", data.get("title")),
                        data.get("kind"),
                        data.get("language"),
                        data.get("path"),
                        data.get("contentHash"),
                        _json(data),
                        fingerprint,
                        *source,
                    ),
                )
                self._edge(
                    "Folder" if label == "Summary" else "File",
                    data.get("path") if label == "Summary" else data.get("filePath"),
                    label,
                    item.id,
                    "CONTAINS",
                )
                if label != "Summary":
                    name = data.get("name", data.get("title", ""))
                    doc = (
                        data.get("docstring") if label == "CodeEntity" else data.get("headingPath")
                    )
                    tokens = _search_tokens(
                        " ".join(str(x or "") for x in (name, data.get("filePath"), body, doc))
                    )
                    self._connection.execute(
                        "INSERT INTO keyword_docs(label,id,name,length) VALUES(?,?,?,?) "
                        "ON CONFLICT(label,id) DO UPDATE SET name=excluded.name,length=excluded.length",
                        (label, item.id, name.lower(), len(tokens)),
                    )
                    key = self._connection.execute(
                        "SELECT key FROM keyword_docs WHERE label=? AND id=?", (label, item.id)
                    ).fetchone()[0]
                    self._connection.execute("DELETE FROM keyword_terms WHERE doc_key=?", (key,))
                    self._connection.executemany(
                        "INSERT INTO keyword_terms VALUES(?,?,?)",
                        [(key, token, n) for token, n in Counter(tokens).items()],
                    )

    def save_entities(self, entities):
        self._save_content(
            entities, "CodeEntity", schema.entity_to_properties, build_embedding_text
        )

    def save_docs(self, chunks):
        self._save_content(chunks, "Doc", schema.doc_to_properties, build_doc_embedding_text)

    def save_summaries(self, summaries):
        self._save_content(
            summaries, "Summary", schema.summary_to_properties, build_summary_embedding_text
        )

    def _validate_vector(self, vector):
        if len(vector) != self.dimensions:
            raise ValueError(f"Expected {self.dimensions} embedding dimensions, got {len(vector)}")
        if not all(math.isfinite(v) for v in vector) or not any(vector):
            raise ValueError("Embedding must be finite and have non-zero magnitude")

    def _save_vectors(self, values, label):
        if not values:
            return
        for vector in values.values():
            self._validate_vector(vector)
        with self.transaction():
            ids = sorted(r["id"] for r in self._nodes(label, values))
            if ids:
                name = self._vectors.save([values[key] for key in ids], self.dimensions)
                self._connection.executemany(
                    "UPDATE nodes SET vector_segment=?,vector_row=?,embedding_hash=embedding_input_hash WHERE label=? AND id=?",
                    [(name, i, label, key) for i, key in enumerate(ids)],
                )

    def save_embeddings(self, id_to_vector):
        self._save_vectors(id_to_vector, "CodeEntity")

    def save_doc_embeddings(self, id_to_vector):
        self._save_vectors(id_to_vector, "Doc")

    def save_summary_embeddings(self, id_to_vector):
        self._save_vectors(id_to_vector, "Summary")

    def _similarities(self, rows, vector):
        self._validate_vector(vector)
        query = np.asarray(vector, dtype="float64")
        query /= np.linalg.norm(query)
        grouped, result = defaultdict(list), {}
        for row in rows:
            if row["vector_segment"]:
                grouped[row["vector_segment"]].append(row)
            else:
                result[row["id"]] = 0.0
        for name, group in grouped.items():
            matrix = self._vectors.read(name, self.dimensions)
            if name not in self._norms:
                self._norms[name] = np.sqrt(np.einsum("ij,ij->i", matrix, matrix, dtype="float64"))
            positions = np.asarray([row["vector_row"] for row in group], dtype=np.intp)
            if np.any(positions < 0) or np.any(positions >= len(matrix)):
                raise ValueError("Invalid vector row reference")
            scores = (
                np.einsum("ij,j->i", matrix, query, dtype="float64")[positions]
                / self._norms[name][positions]
            )
            if not np.isfinite(scores).all():
                raise ValueError("Corrupt non-finite vector segment")
            result.update(
                (row["id"], float(score))
                for row, score in zip(group, np.clip((1 + scores) / 2, 0, 1))
            )
        return result

    def _vector_search(self, vector, top_k, label):
        if top_k <= 0:
            return []
        with self._lock:
            rows = self.query(
                "SELECT id,vector_segment,vector_row FROM nodes WHERE label=:label AND vector_segment IS NOT NULL AND embedding_hash=embedding_input_hash ORDER BY id",
                label=label,
            )
            scores = self._similarities(rows, vector)
            ids = sorted(scores, key=lambda key: (-scores[key], key))[:top_k]
            objects = {r["id"]: self._object(r) for r in self._nodes(label, ids)}
            return [(objects[key], scores[key]) for key in ids]

    def vector_search(self, query_embedding, top_k):
        return self._vector_search(query_embedding, top_k, "CodeEntity")

    def doc_vector_search(self, query_embedding, top_k):
        return self._vector_search(query_embedding, top_k, "Doc")

    def summary_vector_search(self, query_embedding, top_k):
        return self._vector_search(query_embedding, top_k, "Summary")

    def _keyword_rows(self, key, statement, parameters):
        if key not in self._keyword_cache:
            self._keyword_cache[key] = self._connection.execute(statement, parameters).fetchall()
            while len(self._keyword_cache) > 128:
                self._keyword_cache.popitem(last=False)
        self._keyword_cache.move_to_end(key)
        return self._keyword_cache[key]

    def _keyword_search(self, label, question, top_k):
        terms = {t for t in _search_tokens(question) if t not in _STOP_WORDS and len(t) > 1}
        if not terms or top_k <= 0:
            return []
        with self._lock:
            version = self._connection.execute("PRAGMA data_version").fetchone()[0]
            if version != self._keyword_version:
                self._keyword_cache.clear()
                self._keyword_version = version
            size, total = self._keyword_rows(
                (label, None),
                "SELECT count(*),sum(length) FROM keyword_docs WHERE label=?",
                (label,),
            )[0]
            if not size:
                return []
            average, scores = max(1.0, total / size), defaultdict(float)
            for term in sorted(terms):
                rows = self._keyword_rows(
                    (label, term),
                    "SELECT d.id,t.frequency,d.name,d.length FROM keyword_terms t JOIN keyword_docs d ON t.doc_key=d.key WHERE d.label=? AND t.token=?",
                    (label, term),
                )
                idf = math.log(1 + (size - len(rows) + 0.5) / (len(rows) + 0.5))
                for row in rows:
                    freq = row["frequency"]
                    score = (
                        idf * freq * 2.2 / (freq + 1.2 * (0.25 + 0.75 * row["length"] / average))
                    )
                    scores[row["id"]] += score * (3 if row["name"] == term else 1)
            ids = sorted(scores, key=lambda key: (-scores[key], key))[:top_k]
            objects = {r["id"]: self._object(r) for r in self._nodes(label, ids)}
            return [(objects[key], scores[key]) for key in ids]

    def fulltext_search(self, question, top_k):
        return self._keyword_search("CodeEntity", question, top_k)

    def doc_fulltext_search(self, question, top_k):
        return self._keyword_search("Doc", question, top_k)

    def _edge(self, sl, source, tl, target, kind, confidence=1.0, resolved_by=None):
        self._connection.execute(
            """INSERT INTO edges SELECT ?,?,?,?,?,?,?
            WHERE EXISTS(SELECT 1 FROM nodes WHERE label=? AND id=?)
              AND EXISTS(SELECT 1 FROM nodes WHERE label=? AND id=?)
            ON CONFLICT(source_label,source,target_label,target,kind)
            DO UPDATE SET confidence=excluded.confidence,resolved_by=excluded.resolved_by""",
            (sl, source, tl, target, kind, confidence, resolved_by, sl, source, tl, target),
        )

    def save_relationships(self, relationships):
        with self.transaction():
            for rel in relationships:
                if rel.kind in schema.ALLOWED_ENTITY_RELATIONSHIPS:
                    self._edge(
                        "CodeEntity",
                        rel.source_id,
                        "CodeEntity",
                        rel.target_id,
                        rel.kind,
                        rel.confidence,
                        rel.resolved_by,
                    )

    def save_doc_relationships(self, relationships):
        with self.transaction():
            for rel in relationships:
                if rel.kind in schema.ALLOWED_DOC_RELATIONSHIPS:
                    self._edge(
                        "Doc",
                        rel.source_id,
                        "Doc" if rel.kind == "CONTAINS" else "CodeEntity",
                        rel.target_id,
                        rel.kind,
                    )

    def _clear_outgoing(self, path, label, kinds):
        with self.transaction():
            self._connection.execute(
                "DELETE FROM edges WHERE source_label=? AND source IN (SELECT id FROM nodes WHERE label=? AND file_path=?) AND kind IN (SELECT value FROM json_each(?))",
                (label, label, path, _json(kinds)),
            )

    def clear_outgoing_edges(self, file_path):
        self._clear_outgoing(file_path, "CodeEntity", sorted(schema.ALLOWED_ENTITY_RELATIONSHIPS))

    def clear_doc_outgoing_edges(self, file_path):
        self._clear_outgoing(file_path, "Doc", ["CONTAINS", "MENTIONS"])

    def clear_interface_refs(self, file_path):
        with self.transaction():
            for label in ("CodeEntity", "Doc"):
                self._clear_outgoing(file_path, label, ["REFERENCES"])

    def clear_file_imports(self, file_path):
        with self.transaction():
            self._connection.execute(
                "DELETE FROM edges WHERE source_label='File' AND source=? AND kind='IMPORTS'",
                (file_path,),
            )

    def save_file_imports(self, file_path, imported_paths):
        with self.transaction():
            for target in imported_paths:
                self._edge("File", file_path, "File", target, "IMPORTS")

    def get_import_map(self):
        result = defaultdict(set)
        for row in self.query("SELECT source,target FROM edges WHERE kind='IMPORTS'"):
            result[row["source"]].add(row["target"])
        return dict(result)

    def save_interface_refs(self, refs):
        with self.transaction():
            for source, kind, value in refs:
                key = schema.interface_id(kind, value)
                self._connection.execute(
                    "INSERT INTO nodes(label,id,kind,data) VALUES('Interface',?,?,?) ON CONFLICT(label,id) DO UPDATE SET kind=excluded.kind,data=excluded.data",
                    (key, kind, _json({"kind": kind, "value": value})),
                )
                for label in ("CodeEntity", "Doc"):
                    self._edge(label, source, "Interface", key, "REFERENCES")

    def prune_noisy_interfaces(self, max_fanout):
        rows = self.query(
            "SELECT n.id FROM nodes n LEFT JOIN edges e ON e.target_label='Interface' AND e.target=n.id AND e.kind='REFERENCES' WHERE n.label='Interface' GROUP BY n.id HAVING count(e.source)>:cap OR count(e.source)<2",
            cap=max_fanout,
        )
        self._delete_ids("Interface", [r["id"] for r in rows])
        return len(rows)

    def entities_sharing_interfaces(self, entity_ids, limit):
        if not entity_ids or limit <= 0:
            return []
        found = {}
        for label in ("CodeEntity", "Doc"):
            rows = self.query(
                """SELECT n.*,i.data AS interface_data FROM edges a
                JOIN nodes seed ON seed.label=a.source_label AND seed.id=a.source
                JOIN nodes i ON i.label='Interface' AND i.id=a.target
                JOIN edges b ON b.target_label='Interface' AND b.target=a.target AND b.kind='REFERENCES'
                JOIN nodes n ON n.label='CodeEntity' AND b.source_label=n.label AND b.source=n.id
                WHERE a.kind='REFERENCES' AND a.source_label=:label
                  AND seed.id IN (SELECT value FROM json_each(:ids))
                  AND n.id NOT IN (SELECT value FROM json_each(:ids))
                  AND (seed.language IS NULL OR n.language<>seed.language OR n.file_path<>seed.file_path)
                ORDER BY n.id,i.kind,json_extract(i.data,'$.value')""",
                label=label,
                ids=_json(entity_ids),
            )
            for row in rows:
                token = json.loads(row["interface_data"])
                found.setdefault(
                    row["id"],
                    {
                        "entity": self._object(row),
                        "reason": f"shares the {token['kind']} `{token['value']}` with a match",
                    },
                )
        return list(found.values())[:limit]

    def resolve_names(self, names):
        result = {}
        for row in self.query(
            "SELECT * FROM nodes WHERE label='CodeEntity' AND name IN (SELECT value FROM json_each(:names)) ORDER BY id",
            names=_json(sorted(set(names))),
        ):
            data = json.loads(row["data"])
            entry = result.setdefault(row["name"], {"candidates": [], "total": 0})
            entry["total"] += 1
            if len(entry["candidates"]) < config.MAX_NAME_CANDIDATES:
                entry["candidates"].append(
                    {
                        k: data.get(k)
                        for k in (
                            "id",
                            "language",
                            "filePath",
                            "kind",
                            "ownerName",
                            "paramCount",
                            "flexibleArity",
                        )
                    }
                )
        return result

    def get_reference_state(self, new_names):
        known, result = set(new_names), []
        for row in self.query("SELECT data FROM nodes WHERE label='CodeEntity' ORDER BY id"):
            data = json.loads(row["data"])
            if any(
                data.get(k) for k in ("unresolvedCalls", "unresolvedTypes", "unresolvedExtends")
            ) or known.intersection(data.get("externalCalls", []) + data.get("externalTypes", [])):
                result.append(
                    {
                        k: data.get(k, [])
                        for k in (
                            "id",
                            "language",
                            "callOwnerHints",
                            "callArityHints",
                            "unresolvedCalls",
                            "unresolvedTypes",
                            "unresolvedExtends",
                            "externalCalls",
                            "externalTypes",
                        )
                    }
                )
        return result

    def save_reference_state(self, rows):
        with self.transaction():
            for row in rows:
                found = self._nodes("CodeEntity", [row["id"]])
                if found:
                    data = json.loads(found[0]["data"])
                    for key in (
                        "unresolvedCalls",
                        "unresolvedTypes",
                        "unresolvedExtends",
                        "externalCalls",
                        "externalTypes",
                    ):
                        data[key] = row.get(key, [])
                    self._connection.execute(
                        "UPDATE nodes SET data=? WHERE label='CodeEntity' AND id=?",
                        (_json(data), row["id"]),
                    )

    def get_pending_references(self):
        return [
            {"id": r["id"], "language": r["language"], "names": r[field], "kind": kind}
            for r in self.get_reference_state([])
            for field, kind in (("unresolvedCalls", "CALLS"), ("unresolvedExtends", "EXTENDS"))
            if r[field]
        ]

    def get_external_references(self, new_names):
        result = []
        for row in self.get_reference_state(new_names):
            names = sorted(set(new_names).intersection(row["externalCalls"]))
            if names:
                result.append(
                    {"id": row["id"], "language": row["language"], "names": names, "kind": "CALLS"}
                )
        return result

    def get_unowned_members(self):
        rows = self.query("""SELECT n.* FROM nodes n WHERE n.label='CodeEntity'
            AND json_extract(n.data,'$.ownerName') IS NOT NULL AND NOT EXISTS (
                SELECT 1 FROM edges e JOIN nodes o ON o.label=e.source_label AND o.id=e.source
                WHERE e.target_label='CodeEntity' AND e.target=n.id AND e.kind='CONTAINS'
                AND o.label='CodeEntity' AND o.name=json_extract(n.data,'$.ownerName')) ORDER BY n.id""")
        return [
            {k: json.loads(r["data"]).get(k) for k in ("id", "ownerName", "language", "filePath")}
            for r in rows
        ]

    def expand_neighbours(self, seed_ids, hops, limit, query_embedding=None):
        if not seed_ids or limit <= 0:
            return []
        seeds = {r["id"]: r for r in self._nodes("CodeEntity", seed_ids)}
        adjacent = defaultdict(list)
        for edge in self.query(
            "SELECT * FROM edges WHERE source_label='CodeEntity' AND target_label='CodeEntity' AND kind IN ('CALLS','CONTAINS','EXTENDS','USES_TYPE')"
        ):
            adjacent[edge["source"]].append((edge["target"], edge, True))
            adjacent[edge["target"]].append((edge["source"], edge, False))
        best = {}
        for hop in range(1, max(1, min(int(hops), 2)) + 1):
            for outgoing in (True, False):
                candidates = []
                for seed in sorted(seeds):
                    for target, edge, direction in adjacent[seed]:
                        if direction == outgoing:
                            targets = [target] if hop == 1 else [x[0] for x in adjacent[target]]
                            for identifier in targets:
                                if identifier not in seeds:
                                    candidates.append(
                                        (seed, edge["kind"], identifier, edge["confidence"])
                                    )
                for seed, kind, identifier, confidence in sorted(candidates):
                    best.setdefault(
                        identifier,
                        {
                            "id": identifier,
                            "seedId": seed,
                            "seedKind": seeds[seed]["kind"],
                            "relType": kind,
                            "confidence": confidence,
                            "hop": hop,
                            "outgoing": outgoing,
                        },
                    )
        nodes = {r["id"]: r for r in self._nodes("CodeEntity", best)}
        scores = (
            self._similarities(list(nodes.values()), query_embedding)
            if query_embedding is not None
            else {}
        )
        kept, groups = [], Counter()
        for row in sorted(best.values(), key=lambda r: (-scores.get(r["id"], 0), r["id"])):
            key = row["seedId"], row["relType"]
            if row["seedKind"] == "module" and row["relType"] == "CONTAINS":
                if groups[key] >= config.MAX_MODULE_EXPANSION:
                    continue
                groups[key] += 1
            row["relevance"] = scores.get(row["id"], 0)
            kept.append(row)
        return [
            {
                "entity": self._object(nodes[r["id"]]),
                "hop": r["hop"],
                "confidence": r["confidence"],
                "relevance": r["relevance"],
                "rel_type": r["relType"],
                "reason": _describe_connection(r["relType"], r["outgoing"], r["seedId"]),
            }
            for r in sorted(kept, key=lambda r: (r["hop"], -r["relevance"], r["id"]))[:limit]
        ]

    def docs_mentioning(self, entity_ids, limit):
        if not entity_ids or limit <= 0:
            return []
        rows = self.query(
            "SELECT e.source,n.name FROM edges e JOIN nodes n ON n.label='CodeEntity' AND n.id=e.target WHERE e.kind='MENTIONS' AND e.target IN (SELECT value FROM json_each(:ids)) ORDER BY e.source,n.name",
            ids=_json(entity_ids),
        )
        names = defaultdict(set)
        for row in rows:
            names[row["source"]].add(row["name"])
        return [
            (self._object(r), "documents " + ", ".join(sorted(names[r["id"]])[:3]))
            for r in self._nodes("Doc", list(names)[:limit])
        ]

    def entities_mentioned_by(self, doc_ids, limit):
        if not doc_ids or limit <= 0:
            return []
        rows = self.query(
            "SELECT e.target,n.name FROM edges e JOIN nodes n ON n.label='Doc' AND n.id=e.source WHERE e.kind='MENTIONS' AND e.source IN (SELECT value FROM json_each(:ids)) ORDER BY e.target,n.name",
            ids=_json(doc_ids),
        )
        titles = {}
        for row in rows:
            titles.setdefault(row["target"], row["name"])
        return [
            (self._object(r), f'mentioned in "{titles[r["id"]]}"')
            for r in self._nodes("CodeEntity", list(titles)[:limit])
        ]

    def get_entities_for_folders(self, folder_paths):
        result = {p: [] for p in folder_paths}
        rows = self.query(
            """SELECT n.*,a.source AS folder FROM edges a JOIN edges b
            ON b.source_label='File' AND b.source=a.target AND b.kind='CONTAINS'
            JOIN nodes n ON n.label='CodeEntity' AND n.id=b.target AND b.target_label=n.label
            WHERE a.source_label='Folder' AND a.target_label='File' AND a.kind='CONTAINS'
            AND a.source IN (SELECT value FROM json_each(:paths)) ORDER BY n.file_path,json_extract(n.data,'$.startLine'),n.id""",
            paths=_json(folder_paths),
        )
        for row in rows:
            result[row["folder"]].append(self._object(row))
        return result

    def get_summary_hashes(self):
        return {
            r["path"]: r["content_hash"]
            if r["vector_segment"] and r["embedding_hash"] == r["embedding_input_hash"]
            else None
            for r in self._nodes("Summary")
        }

    def stats(self):
        counts = {
            r["label"]: r["n"]
            for r in self.query("SELECT label,count(*) AS n FROM nodes GROUP BY label")
        }
        result = {
            name: counts.get(label, 0)
            for name, label in (
                ("folders", "Folder"),
                ("files", "File"),
                ("entities", "CodeEntity"),
                ("doc chunks", "Doc"),
                ("interfaces", "Interface"),
                ("folder summaries", "Summary"),
            )
        }
        for name, label in (("embedded", "CodeEntity"), ("docs embedded", "Doc")):
            result[name] = self.query(
                "SELECT count(*) AS n FROM nodes WHERE label=:label AND vector_segment IS NOT NULL",
                label=label,
            )[0]["n"]
        edges = {
            r["kind"]: r["n"]
            for r in self.query("SELECT kind,count(*) AS n FROM edges GROUP BY kind")
        }
        for kind in (
            "CALLS",
            "CONTAINS",
            "EXTENDS",
            "USES_TYPE",
            "IMPORTS",
            "MENTIONS",
            "REFERENCES",
        ):
            result[kind + " edges"] = edges.get(kind, 0)
        return result

    def connectivity_stats(self):
        languages = {
            r["id"]: r["language"] or "unknown"
            for r in self.query("SELECT id,language FROM nodes WHERE label='CodeEntity'")
        }
        adjacency = {key: set() for key in languages}
        direct = [
            (r["source"], r["target"])
            for r in self.query(
                "SELECT source,target FROM edges WHERE source_label='CodeEntity' AND target_label='CodeEntity' AND kind IN ('CALLS','CONTAINS','EXTENDS','USES_TYPE')"
            )
        ]
        bridges, mentions = defaultdict(set), defaultdict(set)
        for row in self.query("SELECT * FROM edges WHERE kind IN ('REFERENCES','MENTIONS')"):
            if row["kind"] == "REFERENCES" and row["source_label"] == "CodeEntity":
                bridges[row["target"]].add(row["source"])
            elif row["kind"] == "MENTIONS":
                mentions[row["source"]].add(row["target"])
        pairs = {p for group in bridges.values() for p in itertools.combinations(sorted(group), 2)}
        cross, language_pairs = 0, set()
        for a, b in direct + sorted(pairs):
            adjacency[a].add(b)
            adjacency[b].add(a)
            first, second = (
                language_family(languages[a]) or "unknown",
                language_family(languages[b]) or "unknown",
            )
            if first != second:
                cross += 1
                language_pairs.add(tuple(sorted((first, second))))
        sizes = _component_sizes(adjacency)
        isolated = sum(not neighbors for neighbors in adjacency.values())
        for group in mentions.values():
            for a, b in itertools.combinations(sorted(group), 2):
                adjacency[a].add(b)
                adjacency[b].add(a)
        return {
            "entities": len(adjacency),
            "languages": len(set(languages.values())),
            "components": len(sizes),
            "components_with_docs": len(_component_sizes(adjacency)),
            "largest_component": max(sizes, default=0),
            "isolated_entities": isolated,
            "cross_language_edges": cross,
            "language_pairs_linked": len(language_pairs),
        }

    def compact(self):
        """Repack active vectors under an exclusive project/command lock."""
        with self.transaction():
            for label in ("CodeEntity", "Doc", "Summary"):
                rows = [r for r in self._nodes(label) if r["vector_segment"]]
                if rows:
                    self._save_vectors(
                        {
                            r["id"]: self._vectors.read(r["vector_segment"], self.dimensions)[
                                r["vector_row"]
                            ].copy()
                            for r in rows
                        },
                        label,
                    )
            self._connection.execute(
                "DELETE FROM blobs WHERE hash NOT IN (SELECT source_hash FROM nodes WHERE source_hash IS NOT NULL UNION SELECT text_hash FROM nodes WHERE text_hash IS NOT NULL)"
            )
        active = {
            r["vector_segment"]
            for r in self.query(
                "SELECT DISTINCT vector_segment FROM nodes WHERE vector_segment IS NOT NULL"
            )
        }
        removed = self._vectors.prune(active)
        self._norms.clear()
        self._connection.execute("VACUUM")
        return {"removed_segments": removed}

    def check_integrity(self):
        """Validate SQLite, source reconstruction and every active vector reference."""
        with self._lock:
            if self.query("PRAGMA integrity_check") != [{"integrity_check": "ok"}]:
                raise ValueError("SQLite integrity check failed")
            if self.query("PRAGMA foreign_key_check"):
                raise ValueError("Dangling graph relationship or source reference")
            checked = 0
            for label in ("CodeEntity", "Doc", "Summary"):
                for row in self._nodes(label):
                    self._sources.hydrate(row)
                    if row["vector_segment"]:
                        matrix = self._vectors.read(row["vector_segment"], self.dimensions)
                        position = row["vector_row"]
                        if position is None or not 0 <= position < len(matrix):
                            raise ValueError("Invalid vector row reference")
                        self._validate_vector(matrix[position])
                        if row["embedding_hash"] != row["embedding_input_hash"]:
                            raise ValueError("Vector belongs to stale embedding text")
                        checked += 1
            return {"vectors_checked": checked, "sqlite": "ok", "sources": "ok"}
