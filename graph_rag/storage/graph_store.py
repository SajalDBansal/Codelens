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

    def get_pending_references(self) -> list[dict]:
        """
        The AMBIGUOUS references - names that exist in the graph more than
        once, so we refused to guess which was meant.

        Returns [{"id", "language", "names", "kind"}, ...].

        THIS IS WHAT STOPS THE CALL GRAPH FROM DECAYING.
        ------------------------------------------------
        A call can only be linked once BOTH ends are in the graph. The caller
        is parsed when its own file changes, but the callee may not be added
        until a later run - and by then the caller is unchanged, so it is
        never looked at again. Every such pair was a permanently missing edge.
        Retrying the pending names makes the call graph converge instead.

        WHY THIS IS NOW ONLY THE AMBIGUOUS ONES
        ---------------------------------------
        `unresolvedCalls` used to hold two completely different things at
        once: "not resolvable YET" (the target is not indexed yet - worth
        retrying) and "not resolvable AT ALL" (a call into the standard
        library - will never resolve). Both were stored the same way and
        retried the same way, forever. Nothing pruned a name once it DID
        resolve either, because resolution happened in a later step that
        never wrote its results back.

        The effect was that step 7a of the indexer re-resolved every reference
        in the entire repository on every run, including `len` and `print`.
        Scanning this project's own source produced 702 permanently-pending
        names from 8,900 lines; at that ratio a 500k-line repository would
        re-query about 40,000 names on every run, for ever, and the set only
        ever grew. Steps 2-6 scale with what CHANGED; this one scaled with
        repository SIZE, which quietly undid the whole point of the Merkle
        tree.

        Now the two are stored apart. Ambiguous names stay here and are
        retried every run - there are few of them, and they are the ones that
        can genuinely be resolved by a later run. External names go to
        `externalCalls` and are retried only when a matching name actually
        enters the graph. See get_external_references.
        """
        with self._session() as session:
            result = session.run('\n                MATCH (e:CodeEntity)\n                WHERE (e.unresolvedCalls IS NOT NULL AND size(e.unresolvedCalls) > 0)\n                   OR (e.unresolvedExtends IS NOT NULL AND size(e.unresolvedExtends) > 0)\n                RETURN e.id AS id, e.language AS language,\n                       coalesce(e.unresolvedCalls, [])   AS calls,\n                       coalesce(e.unresolvedExtends, []) AS extends\n                ')
            pending: list[dict] = []
            for record in result:
                if record['calls']:
                    pending.append({'id': record['id'], 'language': record['language'], 'names': record['calls'], 'kind': 'CALLS'})
                if record['extends']:
                    pending.append({'id': record['id'], 'language': record['language'], 'names': record['extends'], 'kind': 'EXTENDS'})
            return pending

    def clear_outgoing_edges(self, file_path: str) -> None:
        """
        Delete the edges that START at entities in this file.

        We do this before re-writing a changed file's edges. If we only added
        edges, a call that was DELETED from the code would keep its edge in
        the graph forever.

        We only clear OUTGOING edges because those are the ones this file is
        responsible for. Incoming edges belong to whichever other file makes
        the call, and that file will refresh its own edges when it changes.
        """
        with self._session() as session:
            session.run('\n                MATCH (e:CodeEntity {filePath: $path})-[r:CALLS|CONTAINS|EXTENDS|USES_TYPE]->()\n                DELETE r\n                ', path=file_path)

    def get_external_references(self, new_names: list[str]) -> list[dict]:
        """
        Pending references that were classified "external" and whose name has
        just appeared in the graph for the first time.

        This is the other half of the fix described in
        get_pending_references. A name we could not find anywhere is almost
        always a library call, and retrying it on every run for the life of
        the repository is pure waste. But it is not ALWAYS a library call:
        sometimes the file defining it simply had not been indexed yet, and
        refusing to ever look again would reintroduce exactly the graph decay
        that pending references exist to prevent.

        So we retry an external name on precisely the run where retrying
        could newly succeed: the one where an entity with that name was
        written. `new_names` is what the indexer just saved, so the work here
        is proportional to what CHANGED rather than to repository size.
        """
        if not new_names:
            return []
        with self._session() as session:
            result = session.run('\n                UNWIND $names AS wanted\n                MATCH (e:CodeEntity)\n                WHERE wanted IN e.externalCalls\n                WITH e, collect(DISTINCT wanted) AS names\n                RETURN e.id AS id, e.language AS language, names\n                ', names=list(set(new_names)))
            return [{'id': r['id'], 'language': r['language'], 'names': r['names'], 'kind': 'CALLS'} for r in result]

    def get_unowned_members(self) -> list[dict]:
        """
        Definitions that name an owning type which is not yet linked to them.

        A Go method declares its type in a receiver and a Rust `impl` block
        declares the type it belongs to, so in both languages the owner can
        live in a different file from the member. When it does, the member
        cannot be linked at parse time - the parser only ever sees one file.

        This is the same "both ends must exist" problem that pending call
        references solve, and it converges the same way: the set shrinks to
        empty as the owning types get indexed, so it costs nothing on a
        settled repository.
        """
        with self._session() as session:
            result = session.run('\n                MATCH (e:CodeEntity)\n                WHERE e.ownerName IS NOT NULL\n                  AND NOT EXISTS {\n                        MATCH (o:CodeEntity {name: e.ownerName})-[:CONTAINS]->(e)\n                  }\n                RETURN e.id AS id, e.ownerName AS ownerName,\n                       e.language AS language, e.filePath AS filePath\n                ')
            return [dict(record) for record in result]

    def get_reference_state(self, new_names: list[str]) -> list[dict]:
        """
        Everything resolution needs to reconsider on THIS run, in one query.

        Returns [{"id", "language", "unresolvedCalls", "unresolvedExtends",
        "externalCalls"}, ...] for every entity that either

          - still has an AMBIGUOUS name (those are always worth retrying:
            there are few of them and a later run can genuinely settle them),
            or
          - has an EXTERNAL name that has just appeared in the graph, because
            `new_names` is what this run wrote, and that is the only event
            that can turn "nowhere to be found" into "found".

        The whole external list comes back, not only the matching names, so
        the caller can rewrite the property without losing the ones it did
        not reconsider.

        This is the query that puts step 7a back on the Merkle budget: its
        cost tracks what changed, not how big the repository is.
        """
        with self._session() as session:
            result = session.run("\n                WITH $newNames AS knownNames\n                MATCH (e:CodeEntity)\n                WHERE size(coalesce(e.unresolvedCalls, [])) > 0\n                   OR size(coalesce(e.unresolvedTypes, [])) > 0\n                   OR size(coalesce(e.unresolvedExtends, [])) > 0\n                   OR any(n IN coalesce(e.externalCalls, []) WHERE n IN knownNames)\n                   OR any(n IN coalesce(e.externalTypes, []) WHERE n IN knownNames)\n                RETURN e.id       AS id,\n                       e.language AS language,\n                       // The receiver types this entity's own source\n                       // declared. This is the evidence that settles an\n                       // override collision - see models.CodeEntity.\n                       coalesce(e.callOwnerHints, [])    AS callOwnerHints,\n                       coalesce(e.callArityHints, [])    AS callArityHints,\n                       coalesce(e.unresolvedCalls, [])   AS unresolvedCalls,\n                       coalesce(e.unresolvedTypes, [])   AS unresolvedTypes,\n                       coalesce(e.unresolvedExtends, []) AS unresolvedExtends,\n                       coalesce(e.externalCalls, [])     AS externalCalls,\n                       coalesce(e.externalTypes, [])     AS externalTypes\n                ", newNames=list(set(new_names)))
            return [dict(record) for record in result]

    def save_reference_state(self, rows: list[dict]) -> None:
        """
        Write back what resolution concluded about each entity's pending names.

        Rows are {"id", "unresolvedCalls", "unresolvedExtends",
        "unresolvedTypes", "externalCalls"}.

        WHY WRITING BACK MATTERS
        ------------------------
        Resolution used to be a read-only pass: it worked out which names now
        linked, created the edges, and then threw that knowledge away. The
        node kept claiming those names were unresolved, so the next run did
        the identical work again and reached the identical conclusion. Three
        names in a 52-entity demo graph were still listed as unresolved after
        their CALLS edge had already been created.

        Persisting the verdict is what makes the pending set SHRINK as the
        graph converges, instead of only ever growing.
        """
        if not rows:
            return
        with self._session() as session:
            for batch in _chunked(rows, 500):
                session.run('\n                    UNWIND $rows AS row\n                    MATCH (e:CodeEntity {id: row.id})\n                    SET e.unresolvedCalls   = row.unresolvedCalls,\n                        e.unresolvedExtends = row.unresolvedExtends,\n                        e.unresolvedTypes   = row.unresolvedTypes,\n                        e.externalCalls     = row.externalCalls,\n                        e.externalTypes     = row.externalTypes\n                    ', rows=batch)

    def save_relationships(self, relationships: list[Relationship]) -> None:
        """
        Create edges between entities.

        Cypher cannot take a relationship type as a parameter, so we group
        the edges by type and paste the type into the query - after checking
        it against a fixed whitelist in schema.py, which makes injection
        impossible.
        """
        if not relationships:
            return
        by_kind: dict[str, list[dict]] = {}
        for rel in relationships:
            if rel.kind not in schema.ALLOWED_ENTITY_RELATIONSHIPS:
                continue
            by_kind.setdefault(rel.kind, []).append({'source': rel.source_id, 'target': rel.target_id, 'confidence': rel.confidence, 'resolvedBy': rel.resolved_by})
        with self._session() as session:
            for kind, rows in by_kind.items():
                for batch in _chunked(rows, 500):
                    session.run(f'\n                        UNWIND $rows AS row\n                        MATCH (a:CodeEntity {{id: row.source}})\n                        MATCH (b:CodeEntity {{id: row.target}})\n                        MERGE (a)-[r:{kind}]->(b)\n                        SET r.confidence = row.confidence,\n                            r.resolvedBy  = row.resolvedBy\n                        ', rows=batch)

    def clear_file_imports(self, file_path: str) -> None:
        """
        Drop this file's IMPORTS edges before rewriting them.

        Nothing used to remove them, so deleting an import statement left its
        IMPORTS edge in the graph forever. Every edge here is derived purely
        from the importing file's own text, so replacing them wholesale is
        both correct and simple.
        """
        with self._session() as session:
            session.run('\n                MATCH (f:File {path: $path})-[r:IMPORTS]->()\n                DELETE r\n                ', path=file_path)

    def save_file_imports(self, file_path: str, imported_paths: list[str]) -> None:
        """Create (:File)-[:IMPORTS]->(:File) edges."""
        if not imported_paths:
            return
        with self._session() as session:
            session.run('\n                MATCH (source:File {path: $path})\n                UNWIND $targets AS target\n                MATCH (t:File {path: target})\n                MERGE (source)-[:IMPORTS]->(t)\n                ', path=file_path, targets=imported_paths)

    def clear_interface_refs(self, file_path: str) -> None:
        with self._session() as session:
            for label in ('CodeEntity', 'Doc'):
                session.run(f'MATCH (n:{label})-[r:REFERENCES]->(:Interface) WHERE n.filePath=$path DELETE r', path=file_path)

    def save_interface_refs(self, refs: list[tuple[str, str, str]]) -> None:
        rows = [{'source': source, 'id': schema.interface_id(kind, value), 'kind': kind, 'value': value} for source, kind, value in refs]
        with self._session() as session:
            for batch in _chunked(rows, 500):
                session.run('UNWIND $rows AS row MERGE (i:Interface {id:row.id}) SET i.kind=row.kind, i.value=row.value', rows=batch)
                for label in ('CodeEntity', 'Doc'):
                    session.run(f'UNWIND $rows AS row MATCH (n:{label} {{id:row.source}}) MATCH (i:Interface {{id:row.id}}) MERGE (n)-[:REFERENCES]->(i)', rows=batch)

    def prune_noisy_interfaces(self, max_fanout: int) -> int:
        rows = self.query('MATCH (i:Interface) OPTIONAL MATCH (i)<-[:REFERENCES]-(n) WITH i, count(n) AS fanout WHERE fanout > $cap OR fanout < 2 RETURN i.id AS id', cap=max_fanout)
        if rows:
            self.query('MATCH (i:Interface) WHERE i.id IN $ids DETACH DELETE i', ids=[r['id'] for r in rows])
        return len(rows)

    def entities_sharing_interfaces(self, entity_ids: list[str], limit: int) -> list[dict]:
        if not entity_ids or limit <= 0:
            return []
        found = {}
        for label in ('CodeEntity', 'Doc'):
            language = 'seed.language' if label == 'CodeEntity' else 'NULL'
            rows = self.query(f'MATCH (seed:{label})-[:REFERENCES]->(i:Interface) MATCH (e:CodeEntity)-[:REFERENCES]->(i) WHERE seed.id IN $ids AND NOT e.id IN $ids AND ({language} IS NULL OR e.language <> {language} OR e.filePath <> seed.filePath) RETURN {schema.ENTITY_RETURN_FIELDS}, i.kind AS tokenKind, i.value AS tokenValue ORDER BY e.id, tokenKind, tokenValue', ids=entity_ids)
            for r in rows:
                found.setdefault(r['id'], {'entity': schema.record_to_entity(r), 'reason': f"shares the {r['tokenKind']} `{r['tokenValue']}` with a match"})
        return list(found.values())[:limit]

    def resolve_names(self, names: list[str]) -> dict[str, dict]:
        if not names:
            return {}
        rows = self.query('MATCH (e:CodeEntity) WHERE e.name IN $names RETURN e.name AS name, e.id AS id, e.language AS language, e.filePath AS filePath, e.kind AS kind, e.ownerName AS ownerName, e.paramCount AS paramCount, e.flexibleArity AS flexibleArity ORDER BY e.id', names=sorted(set(names)))
        result = {}
        for row in rows:
            entry = result.setdefault(row.pop('name'), {'candidates': [], 'total': 0})
            entry['total'] += 1
            if len(entry['candidates']) < config.MAX_NAME_CANDIDATES:
                entry['candidates'].append(row)
        return result

    def get_import_map(self) -> dict[str, set[str]]:
        """
        {file path: the set of file paths it imports}.

        Used to disambiguate call targets. The parser reduces a call to a bare
        NAME, so `save` may match a dozen entities across the repo; but a file
        can only call what it can see, and what it can see is overwhelmingly
        what it imports. Narrowing the candidates to imported files turns most
        "ambiguous, create no edge" verdicts into correct links.
        """
        with self._session() as session:
            result = session.run('\n                MATCH (a:File)-[:IMPORTS]->(b:File)\n                RETURN a.path AS source, collect(b.path) AS targets\n                ')
            return {record['source']: set(record['targets']) for record in result}

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

    def clear_doc_outgoing_edges(self, file_path: str) -> None:
        """
        Drop the edges starting at this file's doc chunks, before rewriting.

        Same reasoning as clear_outgoing_edges for code: if a README stops
        mentioning `save_entities`, the MENTIONS edge has to go away too, and
        only a delete-then-recreate guarantees that.
        """
        with self._session() as session:
            session.run('\n                MATCH (d:Doc {filePath: $path})-[r:CONTAINS|MENTIONS]->()\n                DELETE r\n                ', path=file_path)

    def save_doc_relationships(self, relationships: list[Relationship]) -> None:
        """
        Create Doc -> Doc (CONTAINS) and Doc -> CodeEntity (MENTIONS) edges.

        The target label differs by edge type, so the two are handled
        separately rather than with one clever query.
        """
        if not relationships:
            return
        contains = [{'source': r.source_id, 'target': r.target_id} for r in relationships if r.kind == 'CONTAINS']
        mentions = [{'source': r.source_id, 'target': r.target_id} for r in relationships if r.kind == 'MENTIONS']
        with self._session() as session:
            for batch in _chunked(contains, 500):
                session.run('\n                    UNWIND $rows AS row\n                    MATCH (a:Doc {id: row.source})\n                    MATCH (b:Doc {id: row.target})\n                    MERGE (a)-[:CONTAINS]->(b)\n                    ', rows=batch)
            for batch in _chunked(mentions, 500):
                session.run('\n                    UNWIND $rows AS row\n                    MATCH (a:Doc {id: row.source})\n                    MATCH (b:CodeEntity {id: row.target})\n                    MERGE (a)-[:MENTIONS]->(b)\n                    ', rows=batch)

    @staticmethod
    def _validate_vector(vector: list[float]) -> None:
        if len(vector) != config.EMBEDDING_DIM:
            raise ValueError(f'Expected {config.EMBEDDING_DIM} embedding dimensions, got {len(vector)}')
        if not all((math.isfinite(v) for v in vector)) or not any(vector):
            raise ValueError('Embedding must be finite and have non-zero magnitude')

    def docs_mentioning(self, entity_ids: list[str], limit: int) -> list[tuple[DocChunk, str]]:
        """
        Find the documentation that talks about these code entities.

        This is the code -> docs direction of the MENTIONS edge: you asked a
        question, vector search found a function, and this pulls in the
        README section that documents it. Plain RAG cannot do this - it would
        only find the docs if the docs happened to match the question text.
        """
        if not entity_ids:
            return []
        with self._session() as session:
            result = session.run(f'\n                MATCH (d:Doc)-[:MENTIONS]->(e:CodeEntity)\n                WHERE e.id IN $entityIds\n                WITH d, collect(DISTINCT e.name) AS names\n                RETURN {schema.DOC_RETURN_FIELDS}, names\n                LIMIT $limit\n                ', entityIds=entity_ids, limit=limit)
            return [(schema.record_to_doc(record), 'documents ' + ', '.join(sorted(record['names'])[:3])) for record in result]

    def entities_mentioned_by(self, doc_ids: list[str], limit: int) -> list[tuple[CodeEntity, str]]:
        """
        Find the code that these documentation sections talk about.

        The docs -> code direction. If the question matched a README section,
        this hands back the actual implementation it refers to, so the answer
        can quote real code instead of only repeating the documentation.
        """
        if not doc_ids:
            return []
        with self._session() as session:
            result = session.run(f'\n                MATCH (d:Doc)-[:MENTIONS]->(e:CodeEntity)\n                WHERE d.id IN $docIds\n                WITH e, collect(DISTINCT d.title)[1] AS docTitle\n                RETURN {schema.ENTITY_RETURN_FIELDS}, docTitle\n                LIMIT $limit\n                ', docIds=doc_ids, limit=limit)
            return [(schema.record_to_entity(record), f'''mentioned in "{record['docTitle']}"''') for record in result]
_STOP_WORDS = frozenset({'the', 'a', 'an', 'is', 'are', 'was', 'were', 'be', 'been', 'being', 'do', 'does', 'did', 'how', 'what', 'where', 'when', 'why', 'which', 'who', 'whom', 'this', 'that', 'these', 'those', 'of', 'in', 'on', 'at', 'to', 'for', 'with', 'from', 'by', 'and', 'or', 'not', 'it', 'its', 'i', 'we', 'you', 'can', 'could', 'should', 'would', 'will', 'shall', 'get', 'show', 'me', 'my', 'our', 'please', 'tell', 'about', 'does'})
