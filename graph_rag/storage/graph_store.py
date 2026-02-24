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

def _chunked(items: list, size: int):
    """
    Cut a long list into smaller lists of at most `size` items.

    We send writes in chunks so a single query never has to carry tens of
    thousands of rows, which would use a lot of memory on the server.
    """
    for start in range(0, len(items), size):
        yield items[start:start + size]

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

    def get_folder_hashes(self) -> dict[str, str]:
        """{folder path: Merkle hash} for every folder we stored last run."""
        with self._session() as session:
            result = session.run('MATCH (d:Folder) RETURN d.path AS path, d.hash AS hash')
            return {record['path']: record['hash'] for record in result}

    def get_file_hashes(self) -> dict[str, str]:
        """{file path: sha256 of the file} for every file we stored last run."""
        with self._session() as session:
            result = session.run('MATCH (f:File) RETURN f.path AS path, f.hash AS hash')
            return {record['path']: record['hash'] for record in result}

    def get_entity_hashes(self, file_path: str) -> dict[str, str]:
        """
        {entity id: block hash} for one file.

        This is the third and finest level of the Merkle comparison: even
        inside a file we know changed, we only re-embed the functions whose
        own hash moved.
        """
        with self._session() as session:
            result = session.run('\n                MATCH (e:CodeEntity {filePath: $filePath})\n                RETURN e.id AS id, e.contentHash AS hash\n                ', filePath=file_path)
            return {record['id']: record['hash'] for record in result}

    def get_entity_hashes_for_files(self, file_paths: list[str]) -> dict[str, dict[str, str | None]]:
        """
        {file path: {entity id: embedding input hash or None}} for many files at once.

        The per-file version below is one round trip per changed file, and the
        indexer needs two of them for every file. On a cold index of a few
        thousand files that is tens of thousands of round trips, and the
        latency dominates the whole run. One query for the batch instead.
        """
        if not file_paths:
            return {}
        result: dict[str, dict[str, str | None]] = {path: {} for path in file_paths}
        with self._session() as session:
            for batch in _chunked(file_paths, 1000):
                records = session.run('\n                    MATCH (e:CodeEntity)\n                    WHERE e.filePath IN $paths\n                    RETURN e.filePath AS filePath, e.id AS id,\n                           e.embeddingHash AS hash,\n                           e.embedding IS NOT NULL AS embedded\n                    ', paths=batch)
                for record in records:
                    result[record['filePath']][record['id']] = record['hash'] if record['embedded'] else None
        return result

    def get_doc_hashes_for_files(self, file_paths: list[str]) -> dict[str, dict[str, str | None]]:
        """The document twin of get_entity_hashes_for_files."""
        if not file_paths:
            return {}
        result: dict[str, dict[str, str | None]] = {path: {} for path in file_paths}
        with self._session() as session:
            for batch in _chunked(file_paths, 1000):
                records = session.run('\n                    MATCH (d:Doc)\n                    WHERE d.filePath IN $paths\n                    RETURN d.filePath AS filePath, d.id AS id,\n                           d.embeddingHash AS hash,\n                           d.embedding IS NOT NULL AS embedded\n                    ', paths=batch)
                for record in records:
                    result[record['filePath']][record['id']] = record['hash'] if record['embedded'] else None
        return result

    def get_entity_ids_with_embeddings(self, file_path: str) -> set[str]:
        """
        Which entities in this file already have an embedding stored.

        Needed for the case where a previous run was interrupted after
        writing a node but before embedding it: the hash matches, but the
        vector is missing, so it must still be embedded.
        """
        with self._session() as session:
            result = session.run('\n                MATCH (e:CodeEntity {filePath: $filePath})\n                WHERE e.embedding IS NOT NULL\n                RETURN e.id AS id\n                ', filePath=file_path)
            return {record['id'] for record in result}

    def invalidate_hashes(self, file_paths: list[str], folder_paths: list[str]) -> None:
        """Remove success markers before work, including an unchanged --force run."""

        def invalidate(tx):
            tx.run('MATCH (f:Folder) WHERE f.path IN $paths SET f.hash = NULL', paths=folder_paths).consume()
            tx.run('MATCH (f:File) WHERE f.path IN $paths SET f.hash = NULL', paths=file_paths).consume()
        with self._session() as session:
            session.execute_write(invalidate)

    def save_folder_hashes(self, folders: list[dict]) -> None:
        """
        Write folder Merkle hashes, and nothing else.

        Split out from save_folders because WHEN a hash is written matters.
        A stored hash means "everything under here is already in the graph",
        so it is only true once the run has finished its expensive work. See
        the hash-writing rule in pipeline/indexer.py.
        """
        self._save_hashes(folders, 'Folder')

    def save_file_hashes(self, files: list[dict]) -> None:
        """Write file hashes, and nothing else. See save_folder_hashes."""
        self._save_hashes(files, 'File')

    def _save_hashes(self, rows: list[dict], label: str) -> None:
        if not rows:
            return
        payload = [{'path': row['path'], 'hash': row['hash']} for row in rows]
        with self._session() as session:
            for batch in _chunked(payload, 1000):
                session.run(f'\n                    UNWIND $rows AS row\n                    MATCH (n:{label} {{path: row.path}})\n                    SET n.hash = row.hash\n                    ', rows=batch)

    def save_folders(self, rows: list[dict], with_hash: bool=True) -> None:
        if not rows:
            return

        def write(tx):
            for batch in _chunked(rows, 500):
                assignments = []
                if with_hash:
                    assignments.append('n.hash=row.hash')
                setting = ' SET ' + ', '.join(assignments) if assignments else ''
                tx.run('UNWIND $rows AS row MERGE (n:Folder {path:row.path})' + setting, rows=batch)
                parents = [row for row in batch if row.get('parent') is not None]
                if parents:
                    tx.run('UNWIND $rows AS row MATCH (n:Folder {path:row.path}) MERGE (parent:Folder {path:row.parent}) MERGE (parent)-[:CONTAINS]->(n)', rows=parents)
        with self._session() as session:
            session.execute_write(write)

    def save_files(self, rows: list[dict], with_hash: bool=True) -> None:
        if not rows:
            return

        def write(tx):
            for batch in _chunked(rows, 500):
                assignments = []
                if with_hash:
                    assignments.append('n.hash=row.hash')
                assignments.append('n.language=row.language')
                setting = ' SET ' + ', '.join(assignments) if assignments else ''
                tx.run('UNWIND $rows AS row MERGE (n:File {path:row.path})' + setting, rows=batch)
                parents = [row for row in batch if row.get('parent') is not None]
                if parents:
                    tx.run('UNWIND $rows AS row MATCH (n:File {path:row.path}) MERGE (parent:Folder {path:row.parent}) MERGE (parent)-[:CONTAINS]->(n)', rows=parents)
        with self._session() as session:
            session.execute_write(write)

    def delete_missing_folders(self, existing_paths: list[str]) -> int:
        """
        Remove Folder nodes for directories that no longer exist on disk.

        `DETACH DELETE` deletes the node AND every relationship attached to
        it. Plain `DELETE` refuses to remove a node that still has
        relationships, so DETACH is what you almost always want.
        """
        with self._session() as session:
            result = session.run('\n                MATCH (d:Folder)\n                WHERE NOT d.path IN $paths\n                DETACH DELETE d\n                RETURN count(*) AS removed\n                ', paths=existing_paths)
            return result.single()['removed']

    def delete_file(self, file_path: str) -> None:
        """
        Remove a file and everything defined in it.

        Called when a file is deleted from the repo. Without this the graph
        would slowly fill with "ghost" functions that no longer exist, and
        the LLM would confidently answer questions using deleted code.
        """
        with self._session() as session:
            session.run('MATCH (e:CodeEntity {filePath: $path}) DETACH DELETE e', path=file_path)
            session.run('MATCH (d:Doc {filePath: $path}) DETACH DELETE d', path=file_path)
            session.run('MATCH (f:File {path: $path}) DETACH DELETE f', path=file_path)

    def save_entities(self, entities: list[CodeEntity]) -> None:
        """
        Create or update CodeEntity nodes, and link each to its file.

        The embedding is set separately (see `save_embeddings`) because we
        often save the node first and embed only the subset that changed.
        """
        if not entities:
            return
        rows = []
        for entity in entities:
            properties = schema.entity_to_properties(entity)
            properties.pop('embedding')
            properties['embeddingInputHash'] = embedding_fingerprint(build_embedding_text(entity))
            rows.append(properties)
        with self._session() as session:
            for batch in _chunked(rows, 200):
                session.run('\n                    UNWIND $rows AS row\n                    // MERGE on id = "update it if it exists, create it if not".\n                    MERGE (e:CodeEntity {id: row.id})\n                    // Keep a vector only when it describes this exact input.\n                    SET e.embedding = CASE\n                            WHEN e.embeddingHash = row.embeddingInputHash THEN e.embedding\n                            ELSE null END,\n                        e.embeddingHash = CASE\n                            WHEN e.embeddingHash = row.embeddingInputHash THEN e.embeddingHash\n                            ELSE null END,\n                        e.embeddingInputHash = row.embeddingInputHash\n                    SET e.kind        = row.kind,\n                        e.name        = row.name,\n                        e.filePath    = row.filePath,\n                        e.language    = row.language,\n                        e.startLine   = row.startLine,\n                        e.endLine     = row.endLine,\n                        e.code        = row.code,\n                        e.docstring   = row.docstring,\n                        e.contentHash = row.contentHash,\n                        e.unresolvedCalls   = row.unresolvedCalls,\n                        e.unresolvedExtends = row.unresolvedExtends,\n                        e.unresolvedTypes   = row.unresolvedTypes,\n                        e.externalCalls     = row.externalCalls,\n                        e.externalTypes     = row.externalTypes,\n                        e.isRecursive       = row.isRecursive,\n                        e.ownerName         = row.ownerName,\n                        // The evidence that lets an ambiguous call name be\n                        // resolved later: what each receiver was declared to\n                        // be, how many arguments each call passed, and this\n                        // definition\'s own signature shape. Written here\n                        // because a pending reference is usually settled on\n                        // a LATER run, by which time the calling file is\n                        // unchanged and will not be parsed again.\n                        e.callOwnerHints    = row.callOwnerHints,\n                        e.callArityHints    = row.callArityHints,\n                        e.paramCount        = CAST(row.paramCount AS INT64),\n                        e.flexibleArity     = row.flexibleArity\n                    WITH e, row\n                    MATCH (f:File {path: row.filePath})\n                    MERGE (f)-[:CONTAINS]->(e)\n                    ', rows=batch)

    def save_embeddings(self, id_to_vector: dict[str, list[float]]) -> None:
        """
        Attach vectors and their input hashes atomically to existing nodes.

        The CLI currently runs one indexer at a time. Concurrent writers need
        scoped snapshots and revision checks before this can be hosted.

        Vectors are fixed-width Ladybug FLOAT arrays. Exact similarity search
        reads committed vectors immediately, with no separate index to refresh.
        """
        if not id_to_vector:
            return
        for vector in id_to_vector.values():
            self._validate_vector(vector)
        rows = [{'id': key, 'embedding': value} for key, value in id_to_vector.items()]
        with self._session() as session:
            for batch in _chunked(rows, 100):
                session.run(f'\n                    UNWIND $rows AS row\n                    MATCH (e:CodeEntity {{id: row.id}})\n                    SET e.embedding = CAST(row.embedding AS FLOAT[{config.EMBEDDING_DIM}]),\n                        e.embeddingHash = e.embeddingInputHash\n                    ', rows=batch)

    def delete_entities(self, entity_ids: list[str]) -> None:
        """Remove specific entities - used when a function is deleted from a file."""
        if not entity_ids:
            return
        with self._session() as session:
            session.run('\n                UNWIND $ids AS id\n                MATCH (e:CodeEntity {id: id})\n                DETACH DELETE e\n                ', ids=entity_ids)

    def get_doc_hashes(self, file_path: str) -> dict[str, str]:
        """{doc chunk id: block hash} for one file - the Merkle check for docs."""
        with self._session() as session:
            result = session.run('\n                MATCH (d:Doc {filePath: $filePath})\n                RETURN d.id AS id, d.contentHash AS hash\n                ', filePath=file_path)
            return {record['id']: record['hash'] for record in result}

    def get_doc_ids_with_embeddings(self, file_path: str) -> set[str]:
        """Which doc chunks in this file already have a vector stored."""
        with self._session() as session:
            result = session.run('\n                MATCH (d:Doc {filePath: $filePath})\n                WHERE d.embedding IS NOT NULL\n                RETURN d.id AS id\n                ', filePath=file_path)
            return {record['id'] for record in result}

    def save_docs(self, chunks: list[DocChunk]) -> None:
        """Create or update Doc nodes and link each to its file."""
        if not chunks:
            return
        rows = []
        for chunk in chunks:
            properties = schema.doc_to_properties(chunk)
            properties.pop('embedding')
            properties['embeddingInputHash'] = embedding_fingerprint(build_doc_embedding_text(chunk))
            rows.append(properties)
        with self._session() as session:
            for batch in _chunked(rows, 200):
                session.run('\n                    UNWIND $rows AS row\n                    MERGE (d:Doc {id: row.id})\n                    // Keep a vector only when it describes this exact input.\n                    SET d.embedding = CASE\n                            WHEN d.embeddingHash = row.embeddingInputHash THEN d.embedding\n                            ELSE null END,\n                        d.embeddingHash = CASE\n                            WHEN d.embeddingHash = row.embeddingInputHash THEN d.embeddingHash\n                            ELSE null END,\n                        d.embeddingInputHash = row.embeddingInputHash\n                    SET d.filePath    = row.filePath,\n                        d.docType     = row.docType,\n                        d.title       = row.title,\n                        d.headingPath = row.headingPath,\n                        d.level       = row.level,\n                        d.startLine   = row.startLine,\n                        d.endLine     = row.endLine,\n                        d.text        = row.text,\n                        d.contentHash = row.contentHash\n                    WITH d, row\n                    MATCH (f:File {path: row.filePath})\n                    MERGE (f)-[:CONTAINS]->(d)\n                    ', rows=batch)

    def save_doc_embeddings(self, id_to_vector: dict[str, list[float]]) -> None:
        """Attach embedding vectors to Doc nodes that already exist."""
        if not id_to_vector:
            return
        for vector in id_to_vector.values():
            self._validate_vector(vector)
        rows = [{'id': key, 'embedding': value} for key, value in id_to_vector.items()]
        with self._session() as session:
            for batch in _chunked(rows, 100):
                session.run(f'\n                    UNWIND $rows AS row\n                    MATCH (d:Doc {{id: row.id}})\n                    SET d.embedding = CAST(row.embedding AS FLOAT[{config.EMBEDDING_DIM}]),\n                        d.embeddingHash = d.embeddingInputHash\n                    ', rows=batch)

    def delete_docs(self, doc_ids: list[str]) -> None:
        """Remove doc chunks - used when a section is deleted from a file."""
        if not doc_ids:
            return
        with self._session() as session:
            session.run('\n                UNWIND $ids AS id\n                MATCH (d:Doc {id: id})\n                DETACH DELETE d\n                ', ids=doc_ids)

    @staticmethod
    def _validate_vector(vector: list[float]) -> None:
        if len(vector) != config.EMBEDDING_DIM:
            raise ValueError(f'Expected {config.EMBEDDING_DIM} embedding dimensions, got {len(vector)}')
        if not all((math.isfinite(v) for v in vector)) or not any(vector):
            raise ValueError('Embedding must be finite and have non-zero magnitude')
_STOP_WORDS = frozenset({'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'do', 'does', 'did', 'how', 'what', 'where', 'when', 'why', 'which', 'who', 'whom', 'this', 'that', 'these', 'those', 'of', 'in', 'on', 'at', 'to', 'for', 'with', 'from', 'by', 'and', 'or', 'not', 'it', 'its', 'i', 'we', 'you', 'can', 'could', 'should', 'would', 'will', 'shall', 'get', 'show', 'me', 'my', 'our', 'please', 'tell', 'about', 'does'})
