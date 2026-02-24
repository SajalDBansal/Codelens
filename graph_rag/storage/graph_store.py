"""
storage/graph_store.py
======================

Every LadybugDB query in the project lives here, wrapped in one class.

HOW TO READ A CYPHER QUERY
--------------------------
Cypher draws the pattern it is looking for using ASCII art:

    MATCH (a:CodeEntity)-[:CALLS]->(b:CodeEntity)

means "find a CodeEntity node `a` with a CALLS arrow pointing at another
CodeEntity node `b`". Round brackets are nodes, square brackets are
relationships, and the arrow shows direction.

`$name` is a parameter - a value we pass in from Python separately from the
query text. Always use parameters instead of pasting values into the string:
it keeps user input separate from executable query syntax.

`UNWIND $rows AS row` takes a LIST we pass from Python and turns it into one
row per item, so a single query can write hundreds of nodes. Doing 500
separate queries would be many times slower because each one is a round trip
to the database.
"""
import re
from collections import Counter
from contextlib import contextmanager
import math
import os
from pathlib import Path
import threading
import ladybug
from .local_connection import LocalSession
from .. import config
from ..embedding.embedder import build_doc_embedding_text, build_embedding_text, build_summary_embedding_text, embedding_fingerprint
from ..models import CodeEntity, DocChunk, Relationship
from ..parsing import language_family
from . import schema

class GraphStore:
    """
    The local LadybugDB connection, plus one method per thing we need to do.

    Use it with `with`, so the connection is always closed properly:

        with GraphStore.from_config() as store:
            store.ensure_schema()
    """

    def __init__(self, path: str | Path, *, repo_path: Path | None=None, read_only=False):
        self.path = Path(path).expanduser().resolve()
        self.repo_path = repo_path.resolve() if repo_path is not None else None
        if read_only:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._keyword_cache = {}
        self._db = ladybug.Database(str(self.path), buffer_pool_size=config.LADYBUG_BUFFER_MB * 1024 * 1024, max_num_threads=config.LADYBUG_THREADS, read_only=read_only)
        try:
            self._connection = ladybug.Connection(self._db)
        except BaseException:
            self._db.close()
            raise

    @contextmanager
    def _session(self):
        with self._lock:
            yield LocalSession(self._connection, self._keyword_cache.clear)

    def query(self, statement: str, **parameters) -> list[dict]:
        """Run a parameterized local query and return detached rows."""
        with self._session() as session:
            return list(session.run(statement, **parameters))

    @classmethod
    def from_config(cls, repo_path: Path | None=None, db_path: str | Path | None=None):
        root = (repo_path or Path.cwd()).resolve()
        path = db_path or root / '.codelens' / 'graph.lbug'
        return cls(path, repo_path=repo_path)

    def __enter__(self) -> 'GraphStore':
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.close()

    def close(self) -> None:
        with self._lock:
            if self._connection is not None:
                self._connection.close()
                self._connection = None
                self._db.close()
                self._keyword_cache.clear()

    def verify_connection(self) -> None:
        self.query('RETURN 1 AS ok')

    def index_metadata(self) -> dict:
        """Inspect an existing index without creating schema or changing its model."""
        rows = self.query("MATCH (m:IndexMeta {id: 'embedding'}) RETURN m.model AS model, m.dimensions AS dimensions, m.schemaVersion AS version, m.repoRoot AS root")
        if not rows:
            raise ValueError('This database has no embedding metadata. Run codelens embed first.')
        return rows[0]

    def ensure_schema(self) -> None:
        with self._session() as session:
            for statement in schema.table_statements(config.EMBEDDING_DIM):
                session.run(statement)
        self._check_embedding_model()

    def _check_embedding_model(self) -> None:
        record = self.query("MERGE (m:IndexMeta {id: 'embedding'}) ON CREATE SET m.model=$model, m.dimensions=$dim, m.schemaVersion=$version, m.repoRoot=$root RETURN m.model AS model, m.dimensions AS dimensions, m.schemaVersion AS version, m.repoRoot AS root", model=config.EMBEDDING_MODEL, dim=config.EMBEDDING_DIM, version=schema.SCHEMA_VERSION, root=str(self.repo_path) if self.repo_path else None)[0]
        if (record['model'], record['dimensions'], record['version']) != (config.EMBEDDING_MODEL, config.EMBEDDING_DIM, schema.SCHEMA_VERSION):
            raise ValueError(f"Database {self.path} uses {record['model']} / {record['dimensions']} dimensions and schema {record['version']}. Restore the original settings or use a NEW --db path to rebuild. --force does not change the database format/model.")
        if self.repo_path is not None:
            if record['root'] and os.path.normcase(record['root']) != os.path.normcase(str(self.repo_path)):
                raise ValueError(f"Database belongs to {record['root']}, not {self.repo_path}. Choose a separate --db path for this repository.")
            if not record['root']:
                self.query("MATCH (m:IndexMeta {id:'embedding'}) SET m.repoRoot=$root", root=str(self.repo_path))
_STOP_WORDS = frozenset({'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'do', 'does', 'did', 'how', 'what', 'where', 'when', 'why', 'which', 'who', 'whom', 'this', 'that', 'these', 'those', 'of', 'in', 'on', 'at', 'to', 'for', 'with', 'from', 'by', 'and', 'or', 'not', 'it', 'its', 'i', 'we', 'you', 'can', 'could', 'should', 'would', 'will', 'shall', 'get', 'show', 'me', 'my', 'our', 'please', 'tell', 'about', 'does'})
