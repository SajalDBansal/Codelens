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
from ..embedding.embedder import (
    build_doc_embedding_text,
    build_embedding_text,
    build_summary_embedding_text,
    embedding_fingerprint,
)
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
        yield items[start : start + size]


def _component_sizes(adjacency: dict[str, set[str]]) -> list[int]:
    """
    The size of every connected group in an undirected adjacency map.

    Walking adjacency lists in Python keeps this calculation linear in
    nodes plus edges. It needs no database extension and avoids enumerating
    potentially numerous paths through the same connected component.

    Iterative rather than recursive: a chain of a few thousand functions that
    call each other is perfectly ordinary, and it would blow Python's
    recursion limit.
    """
    seen: set[str] = set()
    sizes: list[int] = []

    for start in adjacency:
        if start in seen:
            continue
        size = 0
        stack = [start]
        while stack:
            node = stack.pop()
            if node in seen:
                continue
            seen.add(node)
            size += 1
            stack.extend(adjacency.get(node, ()))
        sizes.append(size)

    return sizes


class GraphStore:
    """
    The local LadybugDB connection, plus one method per thing we need to do.

    Use it with `with`, so the connection is always closed properly:

        with GraphStore.from_config() as store:
            store.ensure_schema()
    """

    def __init__(self, path: str | Path, *, repo_path: Path | None = None, read_only=False):
        self.path = Path(path).expanduser().resolve()
        self.repo_path = repo_path.resolve() if repo_path is not None else None
        if read_only:
            if not self.path.is_file():
                raise FileNotFoundError(self.path)
        else:
            self.path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._keyword_cache = {}
        self._db = ladybug.Database(
            str(self.path),
            buffer_pool_size=config.LADYBUG_BUFFER_MB * 1024 * 1024,
            max_num_threads=config.LADYBUG_THREADS,
            read_only=read_only,
        )
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
    def from_config(cls, repo_path: Path | None = None, db_path: str | Path | None = None):
        root = (repo_path or Path.cwd()).resolve()
        path = db_path or root / ".codelens" / "graph.lbug"
        return cls(path, repo_path=repo_path)

    # --- context manager plumbing (what makes `with` work) -----------------

    def __enter__(self) -> "GraphStore":
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
        self.query("RETURN 1 AS ok")

    def index_metadata(self) -> dict:
        """Inspect an existing index without creating schema or changing its model."""
        rows = self.query(
            "MATCH (m:IndexMeta {id: 'embedding'}) RETURN m.model AS model, "
            "m.dimensions AS dimensions, m.schemaVersion AS version, m.repoRoot AS root"
        )
        if not rows:
            raise ValueError("This database has no embedding metadata. Run codelens embed first.")
        return rows[0]

    # -----------------------------------------------------------------
    # One-time setup
    # -----------------------------------------------------------------

    def ensure_schema(self) -> None:
        # Check metadata before typed vector columns are used with another model.
        with self._session() as session:
            for statement in schema.table_statements(config.EMBEDDING_DIM):
                session.run(statement)
        self._check_embedding_model()

    def _check_embedding_model(self) -> None:
        record = self.query(
            "MERGE (m:IndexMeta {id: 'embedding'}) "
            "ON CREATE SET m.model=$model, m.dimensions=$dim, m.schemaVersion=$version, "
            "m.repoRoot=$root RETURN m.model AS model, m.dimensions AS dimensions, "
            "m.schemaVersion AS version, m.repoRoot AS root",
            model=config.EMBEDDING_MODEL,
            dim=config.EMBEDDING_DIM,
            version=schema.SCHEMA_VERSION,
            root=str(self.repo_path) if self.repo_path else None,
        )[0]
        if (record["model"], record["dimensions"], record["version"]) != (
            config.EMBEDDING_MODEL,
            config.EMBEDDING_DIM,
            schema.SCHEMA_VERSION,
        ):
            raise ValueError(
                f"Database {self.path} uses {record['model']} / {record['dimensions']} dimensions "
                f"and schema {record['version']}. Restore the original settings or use a NEW "
                "--db path to rebuild. --force does not change the database format/model."
            )
        if self.repo_path is not None:
            if record["root"] and os.path.normcase(record["root"]) != os.path.normcase(
                str(self.repo_path)
            ):
                raise ValueError(
                    f"Database belongs to {record['root']}, not {self.repo_path}. "
                    "Choose a separate --db path for this repository."
                )
            if not record["root"]:
                self.query(
                    "MATCH (m:IndexMeta {id:'embedding'}) SET m.repoRoot=$root",
                    root=str(self.repo_path),
                )

    # -----------------------------------------------------------------
    # Reading the hashes stored from the previous run
    # -----------------------------------------------------------------

    def get_folder_hashes(self) -> dict[str, str]:
        """{folder path: Merkle hash} for every folder we stored last run."""
        with self._session() as session:
            result = session.run("MATCH (d:Folder) RETURN d.path AS path, d.hash AS hash")
            return {record["path"]: record["hash"] for record in result}

    def get_file_hashes(self) -> dict[str, str]:
        """{file path: sha256 of the file} for every file we stored last run."""
        with self._session() as session:
            result = session.run("MATCH (f:File) RETURN f.path AS path, f.hash AS hash")
            return {record["path"]: record["hash"] for record in result}

    def get_entity_hashes(self, file_path: str) -> dict[str, str]:
        """
        {entity id: block hash} for one file.

        This is the third and finest level of the Merkle comparison: even
        inside a file we know changed, we only re-embed the functions whose
        own hash moved.
        """
        with self._session() as session:
            result = session.run(
                """
                MATCH (e:CodeEntity {filePath: $filePath})
                RETURN e.id AS id, e.contentHash AS hash
                """,
                filePath=file_path,
            )
            return {record["id"]: record["hash"] for record in result}

    def get_entity_hashes_for_files(
        self, file_paths: list[str]
    ) -> dict[str, dict[str, str | None]]:
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
                records = session.run(
                    """
                    MATCH (e:CodeEntity)
                    WHERE e.filePath IN $paths
                    RETURN e.filePath AS filePath, e.id AS id,
                           e.embeddingHash AS hash,
                           e.embedding IS NOT NULL AS embedded
                    """,
                    paths=batch,
                )
                for record in records:
                    result[record["filePath"]][record["id"]] = (
                        record["hash"] if record["embedded"] else None
                    )
        return result

    def get_doc_hashes_for_files(self, file_paths: list[str]) -> dict[str, dict[str, str | None]]:
        """The document twin of get_entity_hashes_for_files."""
        if not file_paths:
            return {}

        result: dict[str, dict[str, str | None]] = {path: {} for path in file_paths}
        with self._session() as session:
            for batch in _chunked(file_paths, 1000):
                records = session.run(
                    """
                    MATCH (d:Doc)
                    WHERE d.filePath IN $paths
                    RETURN d.filePath AS filePath, d.id AS id,
                           d.embeddingHash AS hash,
                           d.embedding IS NOT NULL AS embedded
                    """,
                    paths=batch,
                )
                for record in records:
                    result[record["filePath"]][record["id"]] = (
                        record["hash"] if record["embedded"] else None
                    )
        return result

    def get_entity_ids_with_embeddings(self, file_path: str) -> set[str]:
        """
        Which entities in this file already have an embedding stored.

        Needed for the case where a previous run was interrupted after
        writing a node but before embedding it: the hash matches, but the
        vector is missing, so it must still be embedded.
        """
        with self._session() as session:
            result = session.run(
                """
                MATCH (e:CodeEntity {filePath: $filePath})
                WHERE e.embedding IS NOT NULL
                RETURN e.id AS id
                """,
                filePath=file_path,
            )
            return {record["id"] for record in result}

    # -----------------------------------------------------------------
    # Writing the folder / file skeleton
    # -----------------------------------------------------------------

    def invalidate_hashes(self, file_paths: list[str], folder_paths: list[str]) -> None:
        """Remove success markers before work, including an unchanged --force run."""

        def invalidate(tx):
            tx.run(
                "MATCH (f:Folder) WHERE f.path IN $paths SET f.hash = NULL",
                paths=folder_paths,
            ).consume()
            tx.run(
                "MATCH (f:File) WHERE f.path IN $paths SET f.hash = NULL",
                paths=file_paths,
            ).consume()

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
        self._save_hashes(folders, "Folder")

    def save_file_hashes(self, files: list[dict]) -> None:
        """Write file hashes, and nothing else. See save_folder_hashes."""
        self._save_hashes(files, "File")

    def _save_hashes(self, rows: list[dict], label: str) -> None:
        if not rows:
            return
        payload = [{"path": row["path"], "hash": row["hash"]} for row in rows]
        with self._session() as session:
            for batch in _chunked(payload, 1000):
                session.run(
                    f"""
                    UNWIND $rows AS row
                    MATCH (n:{label} {{path: row.path}})
                    SET n.hash = row.hash
                    """,
                    rows=batch,
                )

    def save_folders(self, rows: list[dict], with_hash: bool = True) -> None:
        if not rows:
            return

        def write(tx):
            for batch in _chunked(rows, 500):
                assignments = []
                if with_hash:
                    assignments.append("n.hash=row.hash")

                setting = " SET " + ", ".join(assignments) if assignments else ""
                tx.run("UNWIND $rows AS row MERGE (n:Folder {path:row.path})" + setting, rows=batch)
                parents = [row for row in batch if row.get("parent") is not None]
                if parents:
                    tx.run(
                        "UNWIND $rows AS row MATCH (n:Folder {path:row.path}) "
                        "MERGE (parent:Folder {path:row.parent}) "
                        "MERGE (parent)-[:CONTAINS]->(n)",
                        rows=parents,
                    )

        with self._session() as session:
            session.execute_write(write)

    def save_files(self, rows: list[dict], with_hash: bool = True) -> None:
        if not rows:
            return

        def write(tx):
            for batch in _chunked(rows, 500):
                assignments = []
                if with_hash:
                    assignments.append("n.hash=row.hash")
                assignments.append("n.language=row.language")
                setting = " SET " + ", ".join(assignments) if assignments else ""
                tx.run("UNWIND $rows AS row MERGE (n:File {path:row.path})" + setting, rows=batch)
                parents = [row for row in batch if row.get("parent") is not None]
                if parents:
                    tx.run(
                        "UNWIND $rows AS row MATCH (n:File {path:row.path}) "
                        "MERGE (parent:Folder {path:row.parent}) "
                        "MERGE (parent)-[:CONTAINS]->(n)",
                        rows=parents,
                    )

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
            result = session.run(
                """
                MATCH (d:Folder)
                WHERE NOT d.path IN $paths
                DETACH DELETE d
                RETURN count(*) AS removed
                """,
                paths=existing_paths,
            )
            return result.single()["removed"]

    def delete_file(self, file_path: str) -> None:
        """
        Remove a file and everything defined in it.

        Called when a file is deleted from the repo. Without this the graph
        would slowly fill with "ghost" functions that no longer exist, and
        the LLM would confidently answer questions using deleted code.
        """
        # Three separate statements rather than one.
        #
        # The single-statement version started `MATCH (f:File {path: $path})`,
        # so if the File node was missing - an earlier run interrupted between
        # writing entities and writing the file, say - the whole match failed
        # and its orphaned entities and doc chunks were never cleaned up.
        # It also built a cartesian product of entities x docs, which does
        # pointless work on any file with a lot of both.
        with self._session() as session:
            session.run(
                "MATCH (e:CodeEntity {filePath: $path}) DETACH DELETE e",
                path=file_path,
            )
            session.run(
                "MATCH (d:Doc {filePath: $path}) DETACH DELETE d",
                path=file_path,
            )
            session.run(
                "MATCH (f:File {path: $path}) DETACH DELETE f",
                path=file_path,
            )

    # -----------------------------------------------------------------
    # Writing code entities
    # -----------------------------------------------------------------

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
            properties.pop("embedding")  # handled by save_embeddings
            properties["embeddingInputHash"] = embedding_fingerprint(build_embedding_text(entity))
            rows.append(properties)

        with self._session() as session:
            for batch in _chunked(rows, 200):
                session.run(
                    """
                    UNWIND $rows AS row
                    // MERGE on id = "update it if it exists, create it if not".
                    MERGE (e:CodeEntity {id: row.id})
                    // Keep a vector only when it describes this exact input.
                    SET e.embedding = CASE
                            WHEN e.embeddingHash = row.embeddingInputHash THEN e.embedding
                            ELSE null END,
                        e.embeddingHash = CASE
                            WHEN e.embeddingHash = row.embeddingInputHash THEN e.embeddingHash
                            ELSE null END,
                        e.embeddingInputHash = row.embeddingInputHash
                    SET e.kind        = row.kind,
                        e.name        = row.name,
                        e.filePath    = row.filePath,
                        e.language    = row.language,
                        e.startLine   = row.startLine,
                        e.endLine     = row.endLine,
                        e.code        = row.code,
                        e.docstring   = row.docstring,
                        e.contentHash = row.contentHash,
                        e.unresolvedCalls   = row.unresolvedCalls,
                        e.unresolvedExtends = row.unresolvedExtends,
                        e.unresolvedTypes   = row.unresolvedTypes,
                        e.externalCalls     = row.externalCalls,
                        e.externalTypes     = row.externalTypes,
                        e.isRecursive       = row.isRecursive,
                        e.ownerName         = row.ownerName,
                        // The evidence that lets an ambiguous call name be
                        // resolved later: what each receiver was declared to
                        // be, how many arguments each call passed, and this
                        // definition's own signature shape. Written here
                        // because a pending reference is usually settled on
                        // a LATER run, by which time the calling file is
                        // unchanged and will not be parsed again.
                        e.callOwnerHints    = row.callOwnerHints,
                        e.callArityHints    = row.callArityHints,
                        e.paramCount        = CAST(row.paramCount AS INT64),
                        e.flexibleArity     = row.flexibleArity
                    WITH e, row
                    MATCH (f:File {path: row.filePath})
                    MERGE (f)-[:CONTAINS]->(e)
                    """,
                    rows=batch,
                )

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
        rows = [{"id": key, "embedding": value} for key, value in id_to_vector.items()]

        with self._session() as session:
            # Smaller chunks here: each row carries 1536 floats, so these
            # payloads are far bigger than ordinary property writes.
            for batch in _chunked(rows, 100):
                session.run(
                    f"""
                    UNWIND $rows AS row
                    MATCH (e:CodeEntity {{id: row.id}})
                    SET e.embedding = CAST(row.embedding AS FLOAT[{config.EMBEDDING_DIM}]),
                        e.embeddingHash = e.embeddingInputHash
                    """,
                    rows=batch,
                )

    def delete_entities(self, entity_ids: list[str]) -> None:
        """Remove specific entities - used when a function is deleted from a file."""
        if not entity_ids:
            return

        with self._session() as session:
            session.run(
                """
                UNWIND $ids AS id
                MATCH (e:CodeEntity {id: id})
                DETACH DELETE e
                """,
                ids=entity_ids,
            )

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
            result = session.run(
                """
                MATCH (e:CodeEntity)
                WHERE (e.unresolvedCalls IS NOT NULL AND size(e.unresolvedCalls) > 0)
                   OR (e.unresolvedExtends IS NOT NULL AND size(e.unresolvedExtends) > 0)
                RETURN e.id AS id, e.language AS language,
                       coalesce(e.unresolvedCalls, [])   AS calls,
                       coalesce(e.unresolvedExtends, []) AS extends
                """
            )
            pending: list[dict] = []
            for record in result:
                if record["calls"]:
                    pending.append(
                        {
                            "id": record["id"],
                            "language": record["language"],
                            "names": record["calls"],
                            "kind": "CALLS",
                        }
                    )
                if record["extends"]:
                    pending.append(
                        {
                            "id": record["id"],
                            "language": record["language"],
                            "names": record["extends"],
                            "kind": "EXTENDS",
                        }
                    )
            return pending

    # -----------------------------------------------------------------
    # Writing relationships (edges)
    # -----------------------------------------------------------------

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
            session.run(
                """
                MATCH (e:CodeEntity {filePath: $path})-[r:CALLS|CONTAINS|EXTENDS|USES_TYPE]->()
                DELETE r
                """,
                path=file_path,
            )

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
            result = session.run(
                """
                UNWIND $names AS wanted
                MATCH (e:CodeEntity)
                WHERE wanted IN e.externalCalls
                WITH e, collect(DISTINCT wanted) AS names
                RETURN e.id AS id, e.language AS language, names
                """,
                names=list(set(new_names)),
            )
            return [
                {"id": r["id"], "language": r["language"], "names": r["names"], "kind": "CALLS"}
                for r in result
            ]

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
            result = session.run(
                """
                MATCH (e:CodeEntity)
                WHERE e.ownerName IS NOT NULL
                  AND NOT EXISTS {
                        MATCH (o:CodeEntity {name: e.ownerName})-[:CONTAINS]->(e)
                  }
                RETURN e.id AS id, e.ownerName AS ownerName,
                       e.language AS language, e.filePath AS filePath
                """
            )
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
            result = session.run(
                """
                WITH $newNames AS knownNames
                MATCH (e:CodeEntity)
                WHERE size(coalesce(e.unresolvedCalls, [])) > 0
                   OR size(coalesce(e.unresolvedTypes, [])) > 0
                   OR size(coalesce(e.unresolvedExtends, [])) > 0
                   OR any(n IN coalesce(e.externalCalls, []) WHERE n IN knownNames)
                   OR any(n IN coalesce(e.externalTypes, []) WHERE n IN knownNames)
                RETURN e.id       AS id,
                       e.language AS language,
                       // The receiver types this entity's own source
                       // declared. This is the evidence that settles an
                       // override collision - see models.CodeEntity.
                       coalesce(e.callOwnerHints, [])    AS callOwnerHints,
                       coalesce(e.callArityHints, [])    AS callArityHints,
                       coalesce(e.unresolvedCalls, [])   AS unresolvedCalls,
                       coalesce(e.unresolvedTypes, [])   AS unresolvedTypes,
                       coalesce(e.unresolvedExtends, []) AS unresolvedExtends,
                       coalesce(e.externalCalls, [])     AS externalCalls,
                       coalesce(e.externalTypes, [])     AS externalTypes
                """,
                newNames=list(set(new_names)),
            )
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
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (e:CodeEntity {id: row.id})
                    SET e.unresolvedCalls   = row.unresolvedCalls,
                        e.unresolvedExtends = row.unresolvedExtends,
                        e.unresolvedTypes   = row.unresolvedTypes,
                        e.externalCalls     = row.externalCalls,
                        e.externalTypes     = row.externalTypes
                    """,
                    rows=batch,
                )

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

        # Group by kind: {"CALLS": [...], "CONTAINS": [...]}
        by_kind: dict[str, list[dict]] = {}
        for rel in relationships:
            if rel.kind not in schema.ALLOWED_ENTITY_RELATIONSHIPS:
                continue  # unknown edge type - ignore rather than trust it
            by_kind.setdefault(rel.kind, []).append(
                {
                    "source": rel.source_id,
                    "target": rel.target_id,
                    # How much the edge is worth, and which rule made it.
                    # Retrieval reads the first to rank neighbours; the
                    # second is there so a surprising link can be explained
                    # by querying the graph rather than re-running the
                    # indexer with a debugger attached.
                    "confidence": rel.confidence,
                    "resolvedBy": rel.resolved_by,
                }
            )

        with self._session() as session:
            for kind, rows in by_kind.items():
                for batch in _chunked(rows, 500):
                    session.run(
                        f"""
                        UNWIND $rows AS row
                        MATCH (a:CodeEntity {{id: row.source}})
                        MATCH (b:CodeEntity {{id: row.target}})
                        MERGE (a)-[r:{kind}]->(b)
                        SET r.confidence = row.confidence,
                            r.resolvedBy  = row.resolvedBy
                        """,
                        rows=batch,
                    )

    def clear_file_imports(self, file_path: str) -> None:
        """
        Drop this file's IMPORTS edges before rewriting them.

        Nothing used to remove them, so deleting an import statement left its
        IMPORTS edge in the graph forever. Every edge here is derived purely
        from the importing file's own text, so replacing them wholesale is
        both correct and simple.
        """
        with self._session() as session:
            session.run(
                """
                MATCH (f:File {path: $path})-[r:IMPORTS]->()
                DELETE r
                """,
                path=file_path,
            )

    def save_file_imports(self, file_path: str, imported_paths: list[str]) -> None:
        """Create (:File)-[:IMPORTS]->(:File) edges."""
        if not imported_paths:
            return

        with self._session() as session:
            session.run(
                """
                MATCH (source:File {path: $path})
                UNWIND $targets AS target
                MATCH (t:File {path: target})
                MERGE (source)-[:IMPORTS]->(t)
                """,
                path=file_path,
                targets=imported_paths,
            )

    # -----------------------------------------------------------------
    # Interface tokens: the cross-language join
    # -----------------------------------------------------------------

    def clear_interface_refs(self, file_path: str) -> None:
        with self._session() as session:
            for label in ("CodeEntity", "Doc"):
                session.run(
                    f"MATCH (n:{label})-[r:REFERENCES]->(:Interface) "
                    "WHERE n.filePath=$path DELETE r",
                    path=file_path,
                )

    def save_interface_refs(self, refs: list[tuple[str, str, str]]) -> None:
        rows = [
            {"source": source, "id": schema.interface_id(kind, value), "kind": kind, "value": value}
            for source, kind, value in refs
        ]
        with self._session() as session:
            for batch in _chunked(rows, 500):
                session.run(
                    "UNWIND $rows AS row MERGE (i:Interface {id:row.id}) "
                    "SET i.kind=row.kind, i.value=row.value",
                    rows=batch,
                )
                for label in ("CodeEntity", "Doc"):
                    session.run(
                        f"UNWIND $rows AS row MATCH (n:{label} {{id:row.source}}) "
                        "MATCH (i:Interface {id:row.id}) MERGE (n)-[:REFERENCES]->(i)",
                        rows=batch,
                    )

    def prune_noisy_interfaces(self, max_fanout: int) -> int:
        rows = self.query(
            "MATCH (i:Interface) OPTIONAL MATCH (i)<-[:REFERENCES]-(n) "
            "WITH i, count(n) AS fanout WHERE fanout > $cap OR fanout < 2 "
            "RETURN i.id AS id",
            cap=max_fanout,
        )
        if rows:
            self.query(
                "MATCH (i:Interface) WHERE i.id IN $ids DETACH DELETE i",
                ids=[r["id"] for r in rows],
            )
        return len(rows)

    def entities_sharing_interfaces(self, entity_ids: list[str], limit: int) -> list[dict]:
        if not entity_ids or limit <= 0:
            return []
        found = {}
        for label in ("CodeEntity", "Doc"):
            language = "seed.language" if label == "CodeEntity" else "NULL"
            rows = self.query(
                f"MATCH (seed:{label})-[:REFERENCES]->(i:Interface) "
                "MATCH (e:CodeEntity)-[:REFERENCES]->(i) "
                f"WHERE seed.id IN $ids AND NOT e.id IN $ids AND ({language} IS NULL "
                f"OR e.language <> {language} OR e.filePath <> seed.filePath) "
                f"RETURN {schema.ENTITY_RETURN_FIELDS}, i.kind AS tokenKind, i.value AS tokenValue "
                "ORDER BY e.id, tokenKind, tokenValue",
                ids=entity_ids,
            )
            for r in rows:
                found.setdefault(
                    r["id"],
                    {
                        "entity": schema.record_to_entity(r),
                        "reason": f"shares the {r['tokenKind']} `{r['tokenValue']}` with a match",
                    },
                )
        return list(found.values())[:limit]

    def resolve_names(self, names: list[str]) -> dict[str, dict]:
        if not names:
            return {}
        rows = self.query(
            "MATCH (e:CodeEntity) WHERE e.name IN $names RETURN e.name AS name, "
            "e.id AS id, e.language AS language, e.filePath AS filePath, e.kind AS kind, "
            "e.ownerName AS ownerName, e.paramCount AS paramCount, e.flexibleArity AS flexibleArity "
            "ORDER BY e.id",
            names=sorted(set(names)),
        )
        result = {}
        for row in rows:
            entry = result.setdefault(row.pop("name"), {"candidates": [], "total": 0})
            entry["total"] += 1
            if len(entry["candidates"]) < config.MAX_NAME_CANDIDATES:
                entry["candidates"].append(row)
        return result

    # -----------------------------------------------------------------
    # Documentation chunks
    # -----------------------------------------------------------------
    # These mirror the CodeEntity methods above almost exactly. Docs get
    # their own label and their own vector index so that prose and code can
    # be retrieved in controlled proportions rather than competing in one
    # ranking - these remain separate ranked searches.

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
            result = session.run(
                """
                MATCH (a:File)-[:IMPORTS]->(b:File)
                RETURN a.path AS source, collect(b.path) AS targets
                """
            )
            return {record["source"]: set(record["targets"]) for record in result}

    def get_doc_hashes(self, file_path: str) -> dict[str, str]:
        """{doc chunk id: block hash} for one file - the Merkle check for docs."""
        with self._session() as session:
            result = session.run(
                """
                MATCH (d:Doc {filePath: $filePath})
                RETURN d.id AS id, d.contentHash AS hash
                """,
                filePath=file_path,
            )
            return {record["id"]: record["hash"] for record in result}

    def get_doc_ids_with_embeddings(self, file_path: str) -> set[str]:
        """Which doc chunks in this file already have a vector stored."""
        with self._session() as session:
            result = session.run(
                """
                MATCH (d:Doc {filePath: $filePath})
                WHERE d.embedding IS NOT NULL
                RETURN d.id AS id
                """,
                filePath=file_path,
            )
            return {record["id"] for record in result}

    def save_docs(self, chunks: list[DocChunk]) -> None:
        """Create or update Doc nodes and link each to its file."""
        if not chunks:
            return

        rows = []
        for chunk in chunks:
            properties = schema.doc_to_properties(chunk)
            properties.pop("embedding")  # set separately, like entities
            properties["embeddingInputHash"] = embedding_fingerprint(
                build_doc_embedding_text(chunk)
            )
            rows.append(properties)

        with self._session() as session:
            for batch in _chunked(rows, 200):
                session.run(
                    """
                    UNWIND $rows AS row
                    MERGE (d:Doc {id: row.id})
                    // Keep a vector only when it describes this exact input.
                    SET d.embedding = CASE
                            WHEN d.embeddingHash = row.embeddingInputHash THEN d.embedding
                            ELSE null END,
                        d.embeddingHash = CASE
                            WHEN d.embeddingHash = row.embeddingInputHash THEN d.embeddingHash
                            ELSE null END,
                        d.embeddingInputHash = row.embeddingInputHash
                    SET d.filePath    = row.filePath,
                        d.docType     = row.docType,
                        d.title       = row.title,
                        d.headingPath = row.headingPath,
                        d.level       = row.level,
                        d.startLine   = row.startLine,
                        d.endLine     = row.endLine,
                        d.text        = row.text,
                        d.contentHash = row.contentHash
                    WITH d, row
                    MATCH (f:File {path: row.filePath})
                    MERGE (f)-[:CONTAINS]->(d)
                    """,
                    rows=batch,
                )

    def save_doc_embeddings(self, id_to_vector: dict[str, list[float]]) -> None:
        """Attach embedding vectors to Doc nodes that already exist."""
        if not id_to_vector:
            return

        for vector in id_to_vector.values():
            self._validate_vector(vector)
        rows = [{"id": key, "embedding": value} for key, value in id_to_vector.items()]

        with self._session() as session:
            for batch in _chunked(rows, 100):
                session.run(
                    f"""
                    UNWIND $rows AS row
                    MATCH (d:Doc {{id: row.id}})
                    SET d.embedding = CAST(row.embedding AS FLOAT[{config.EMBEDDING_DIM}]),
                        d.embeddingHash = d.embeddingInputHash
                    """,
                    rows=batch,
                )

    def delete_docs(self, doc_ids: list[str]) -> None:
        """Remove doc chunks - used when a section is deleted from a file."""
        if not doc_ids:
            return

        with self._session() as session:
            session.run(
                """
                UNWIND $ids AS id
                MATCH (d:Doc {id: id})
                DETACH DELETE d
                """,
                ids=doc_ids,
            )

    def clear_doc_outgoing_edges(self, file_path: str) -> None:
        """
        Drop the edges starting at this file's doc chunks, before rewriting.

        Same reasoning as clear_outgoing_edges for code: if a README stops
        mentioning `save_entities`, the MENTIONS edge has to go away too, and
        only a delete-then-recreate guarantees that.
        """
        with self._session() as session:
            session.run(
                """
                MATCH (d:Doc {filePath: $path})-[r:CONTAINS|MENTIONS]->()
                DELETE r
                """,
                path=file_path,
            )

    def save_doc_relationships(self, relationships: list[Relationship]) -> None:
        """
        Create Doc -> Doc (CONTAINS) and Doc -> CodeEntity (MENTIONS) edges.

        The target label differs by edge type, so the two are handled
        separately rather than with one clever query.
        """
        if not relationships:
            return

        contains = [
            {"source": r.source_id, "target": r.target_id}
            for r in relationships
            if r.kind == "CONTAINS"
        ]
        mentions = [
            {"source": r.source_id, "target": r.target_id}
            for r in relationships
            if r.kind == "MENTIONS"
        ]

        with self._session() as session:
            for batch in _chunked(contains, 500):
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (a:Doc {id: row.source})
                    MATCH (b:Doc {id: row.target})
                    MERGE (a)-[:CONTAINS]->(b)
                    """,
                    rows=batch,
                )
            for batch in _chunked(mentions, 500):
                session.run(
                    """
                    UNWIND $rows AS row
                    MATCH (a:Doc {id: row.source})
                    MATCH (b:CodeEntity {id: row.target})
                    MERGE (a)-[:MENTIONS]->(b)
                    """,
                    rows=batch,
                )

    # -----------------------------------------------------------------
    # Retrieval (used by the "ask" command)
    # -----------------------------------------------------------------

    def vector_search(self, query_embedding: list[float], top_k: int) -> list:
        if top_k <= 0:
            return []
        self._validate_vector(query_embedding)
        rows = self.query(
            f"MATCH (e:CodeEntity) WHERE e.embedding IS NOT NULL "
            f"WITH e, (1.0 + array_cosine_similarity(e.embedding, "
            f"CAST($embedding AS FLOAT[{config.EMBEDDING_DIM}]))) / 2.0 AS score "
            f"RETURN {schema.ENTITY_RETURN_FIELDS}, score ORDER BY score DESC, e.id LIMIT $topK",
            embedding=query_embedding,
            topK=top_k,
        )
        return [(schema.record_to_entity(r), min(1.0, max(0.0, r["score"]))) for r in rows]

    def fulltext_search(self, question: str, top_k: int) -> list:
        return self._keyword_search("CodeEntity", question, top_k)

    @staticmethod
    def _validate_vector(vector: list[float]) -> None:
        if len(vector) != config.EMBEDDING_DIM:
            raise ValueError(
                f"Expected {config.EMBEDDING_DIM} embedding dimensions, got {len(vector)}"
            )
        if not all(math.isfinite(v) for v in vector) or not any(vector):
            raise ValueError("Embedding must be finite and have non-zero magnitude")

    def _keyword_search(self, label: str, question: str, top_k: int) -> list:
        """BM25 over stored text, with exact identifier boosts; no native extensions.

        The corpus is cached for this connection and invalidated on writes. Full
        vectors remain inside Ladybug; this index contains only tokens and ids.
        """
        terms = [t for t in _search_tokens(question) if t not in _STOP_WORDS and len(t) > 1]
        if not terms or top_k <= 0:
            return []
        with self._lock:
            if label not in self._keyword_cache:
                fields = "e.id AS id, e.name AS name, e.filePath AS path, e.code AS text, e.docstring AS doc"
                if label == "Doc":
                    fields = "e.id AS id, e.title AS name, e.filePath AS path, e.text AS text, e.headingPath AS doc"
                records = self.query(f"MATCH (e:{label}) RETURN {fields}")
                corpus, postings, lengths = {}, {}, {}
                for row in records:
                    tokens = _search_tokens(
                        " ".join(str(row[k] or "") for k in ("name", "path", "text", "doc"))
                    )
                    counts = Counter(tokens)
                    corpus[row["id"]] = row["name"].lower()
                    lengths[row["id"]] = len(tokens)
                    for token, count in counts.items():
                        postings.setdefault(token, {})[row["id"]] = count
                self._keyword_cache[label] = (corpus, postings, lengths)
            corpus, postings, lengths = self._keyword_cache[label]
            if not corpus:
                return []
            average = max(1.0, sum(lengths.values()) / len(corpus))
            scores = {}
            for term in set(terms):
                matches = postings.get(term, {})
                idf = math.log(1.0 + (len(corpus) - len(matches) + 0.5) / (len(matches) + 0.5))
                for identifier, frequency in matches.items():
                    score = (
                        idf
                        * frequency
                        * 2.2
                        / (frequency + 1.2 * (0.25 + 0.75 * lengths[identifier] / average))
                    )
                    if corpus[identifier] == term:
                        score *= 3.0
                    scores[identifier] = scores.get(identifier, 0.0) + score
            ids = sorted(scores, key=lambda key: (-scores[key], key))[:top_k]
            if not ids:
                return []
            fields = (
                schema.ENTITY_RETURN_FIELDS if label == "CodeEntity" else schema.DOC_RETURN_FIELDS
            )
            alias = "e" if label == "CodeEntity" else "d"
            mapper = schema.record_to_entity if label == "CodeEntity" else schema.record_to_doc
            rows = self.query(
                f"MATCH ({alias}:{label}) WHERE {alias}.id IN $ids RETURN {fields}", ids=ids
            )
            objects = {r["id"]: mapper(r) for r in rows}
            return [(objects[key], scores[key]) for key in ids]

    def doc_fulltext_search(self, question: str, top_k: int) -> list:
        return self._keyword_search("Doc", question, top_k)

    def expand_neighbours(
        self, seed_ids: list[str], hops: int, limit: int, query_embedding: list[float] | None = None
    ) -> list[dict]:
        if not seed_ids or limit <= 0:
            return []
        if query_embedding is not None:
            self._validate_vector(query_embedding)
        # Explicit 1/2-hop patterns keep intermediate nodes restricted to code.
        # Bounded patterns also make the permitted intermediate node types explicit.
        best = {}
        types = "CALLS|CONTAINS|EXTENDS|USES_TYPE"
        for hop in range(1, max(1, min(int(hops), 2)) + 1):
            middle = "(e:CodeEntity)" if hop == 1 else f"(m:CodeEntity)-[:{types}]-(e:CodeEntity)"
            score = "0.0"
            if query_embedding is not None:
                score = (
                    f"CASE WHEN e.embedding IS NULL THEN 0.0 ELSE "
                    f"(1.0 + array_cosine_similarity(e.embedding, CAST($vector AS FLOAT[{config.EMBEDDING_DIM}]))) / 2.0 END"
                )
            for outgoing in (True, False):
                first = f"-[r:{types}]->" if outgoing else f"<-[r:{types}]-"
                parameters = {"ids": seed_ids}
                if query_embedding is not None:
                    parameters["vector"] = query_embedding
                rows = self.query(
                    f"MATCH (seed:CodeEntity){first}{middle} "
                    "WHERE seed.id IN $ids AND NOT e.id IN $ids "
                    f"RETURN {schema.ENTITY_RETURN_FIELDS}, seed.id AS seedId, seed.kind AS seedKind, "
                    f"label(r) AS relType, coalesce(r.confidence, 1.0) AS confidence, {score} AS relevance "
                    "ORDER BY seedId, relType, e.id",
                    **parameters,
                )
                for row in rows:
                    best.setdefault(row["id"], dict(row, hop=hop, outgoing=outgoing))
        kept, groups = [], Counter()
        for row in sorted(best.values(), key=lambda r: (-r["relevance"], r["id"])):
            key = (row["seedId"], row["relType"])
            if row["seedKind"] == "module" and row["relType"] == "CONTAINS":
                if groups[key] >= config.MAX_MODULE_EXPANSION:
                    continue
                groups[key] += 1
            kept.append(row)
        return [
            {
                "entity": schema.record_to_entity(r),
                "hop": r["hop"],
                "confidence": r["confidence"],
                "relevance": r["relevance"],
                "rel_type": r["relType"],
                "reason": _describe_connection(r["relType"], r["outgoing"], r["seedId"]),
            }
            for r in sorted(kept, key=lambda r: (r["hop"], -r["relevance"], r["id"]))[:limit]
        ]

    def doc_vector_search(self, query_embedding: list[float], top_k: int) -> list:
        if top_k <= 0:
            return []
        self._validate_vector(query_embedding)
        rows = self.query(
            f"MATCH (d:Doc) WHERE d.embedding IS NOT NULL "
            f"WITH d, (1.0 + array_cosine_similarity(d.embedding, "
            f"CAST($embedding AS FLOAT[{config.EMBEDDING_DIM}]))) / 2.0 AS score "
            f"RETURN {schema.DOC_RETURN_FIELDS}, score ORDER BY score DESC, d.id LIMIT $topK",
            embedding=query_embedding,
            topK=top_k,
        )
        return [(schema.record_to_doc(r), min(1.0, max(0.0, r["score"]))) for r in rows]

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
            result = session.run(
                f"""
                MATCH (d:Doc)-[:MENTIONS]->(e:CodeEntity)
                WHERE e.id IN $entityIds
                WITH d, collect(DISTINCT e.name) AS names
                RETURN {schema.DOC_RETURN_FIELDS}, names
                LIMIT $limit
                """,
                entityIds=entity_ids,
                limit=limit,
            )
            return [
                (
                    schema.record_to_doc(record),
                    "documents " + ", ".join(sorted(record["names"])[:3]),
                )
                for record in result
            ]

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
            result = session.run(
                f"""
                MATCH (d:Doc)-[:MENTIONS]->(e:CodeEntity)
                WHERE d.id IN $docIds
                WITH e, collect(DISTINCT d.title)[1] AS docTitle
                RETURN {schema.ENTITY_RETURN_FIELDS}, docTitle
                LIMIT $limit
                """,
                docIds=doc_ids,
                limit=limit,
            )
            return [
                (schema.record_to_entity(record), f'mentioned in "{record["docTitle"]}"')
                for record in result
            ]

    # -----------------------------------------------------------------
    # Reporting
    # -----------------------------------------------------------------

    # -----------------------------------------------------------------
    # Folder summaries
    # -----------------------------------------------------------------

    def get_entities_for_folders(self, folder_paths: list[str]) -> dict[str, list[CodeEntity]]:
        """Read complete current folder contents, including unchanged files."""
        result = {path: [] for path in folder_paths}
        with self._session() as session:
            for batch in _chunked(folder_paths, 1000):
                rows = session.run(
                    f"""
                    UNWIND $paths AS path
                    MATCH (:Folder {{path: path}})-[:CONTAINS]->(:File)-[:CONTAINS]->(e:CodeEntity)
                    RETURN path AS folder, {schema.ENTITY_RETURN_FIELDS}
                    ORDER BY e.filePath, e.startLine, e.id
                    """,
                    paths=batch,
                )
                for row in rows:
                    result[row["folder"]].append(schema.record_to_entity(row))
        return result

    def delete_summaries(self, folder_paths: list[str]) -> None:
        """Remove outdated overviews, including folders with no code left."""
        if folder_paths:
            with self._session() as session:
                session.run(
                    "MATCH (s:Summary) WHERE s.path IN $paths DETACH DELETE s",
                    paths=folder_paths,
                ).consume()

    def get_summary_hashes(self) -> dict[str, str | None]:
        """{folder path: the Merkle hash the stored summary was written from}."""
        with self._session() as session:
            result = session.run(
                """
                MATCH (s:Summary)
                RETURN s.path AS path, CASE
                    WHEN s.embedding IS NOT NULL AND s.embeddingHash = s.embeddingInputHash
                    THEN s.contentHash ELSE null END AS hash
                """
            )
            return {record["path"]: record["hash"] for record in result}

    def save_summaries(self, summaries: list) -> None:
        """Create or update Summary nodes and hang each off its Folder."""
        if not summaries:
            return

        rows = []
        for summary in summaries:
            properties = schema.summary_to_properties(summary)
            properties.pop("embedding")
            properties["embeddingInputHash"] = embedding_fingerprint(
                build_summary_embedding_text(summary)
            )
            rows.append(properties)

        with self._session() as session:
            session.run(
                """
                UNWIND $rows AS row
                MERGE (s:Summary {id: row.id})
                // Keep a vector only when it describes this exact input.
                SET s.embedding = CASE
                        WHEN s.embeddingHash = row.embeddingInputHash THEN s.embedding
                        ELSE null END,
                    s.embeddingHash = CASE
                        WHEN s.embeddingHash = row.embeddingInputHash THEN s.embeddingHash
                        ELSE null END,
                    s.embeddingInputHash = row.embeddingInputHash
                SET s.path        = row.path,
                    s.text        = row.text,
                    s.entityCount = row.entityCount,
                    s.contentHash = row.contentHash
                WITH s, row
                MATCH (f:Folder {path: row.path})
                MERGE (f)-[:CONTAINS]->(s)
                """,
                rows=rows,
            )

    def save_summary_embeddings(self, id_to_vector: dict) -> None:
        """Attach vectors to Summary nodes."""
        if not id_to_vector:
            return

        for vector in id_to_vector.values():
            self._validate_vector(vector)
        rows = [{"id": key, "embedding": value} for key, value in id_to_vector.items()]
        with self._session() as session:
            for batch in _chunked(rows, 100):
                session.run(
                    f"""
                    UNWIND $rows AS row
                    MATCH (s:Summary {{id: row.id}})
                    SET s.embedding = CAST(row.embedding AS FLOAT[{config.EMBEDDING_DIM}]),
                        s.embeddingHash = s.embeddingInputHash
                    """,
                    rows=batch,
                )

    def delete_missing_summaries(self, existing_paths: list[str]) -> int:
        """Remove summaries for folders that are no longer in the repository."""
        with self._session() as session:
            result = session.run(
                """
                MATCH (s:Summary)
                WHERE NOT s.path IN $paths
                DETACH DELETE s
                RETURN count(s) AS removed
                """,
                paths=existing_paths,
            )
            record = result.single()
            return record["removed"] if record else 0

    def summary_vector_search(self, query_embedding: list[float], top_k: int) -> list:
        if top_k <= 0:
            return []
        self._validate_vector(query_embedding)
        rows = self.query(
            f"MATCH (s:Summary) WHERE s.embedding IS NOT NULL "
            f"WITH s, (1.0 + array_cosine_similarity(s.embedding, "
            f"CAST($embedding AS FLOAT[{config.EMBEDDING_DIM}]))) / 2.0 AS score "
            f"RETURN {schema.SUMMARY_RETURN_FIELDS}, score ORDER BY score DESC, s.id LIMIT $topK",
            embedding=query_embedding,
            topK=top_k,
        )
        return [(schema.record_to_summary(r), min(1.0, max(0.0, r["score"]))) for r in rows]

    def stats(self) -> dict[str, int]:
        """
        Counts for the summary printed at the end of an indexing run.

        Deliberately written as several small queries instead of one clever
        one: each line counts one table or relationship type and is easy to inspect.
        """
        counts = {
            "folders": "MATCH (d:Folder)     RETURN count(d) AS n",
            "files": "MATCH (f:File)       RETURN count(f) AS n",
            "entities": "MATCH (e:CodeEntity) RETURN count(e) AS n",
            "embedded": "MATCH (e:CodeEntity) WHERE e.embedding IS NOT NULL RETURN count(e) AS n",
            "doc chunks": "MATCH (d:Doc) RETURN count(d) AS n",
            "docs embedded": "MATCH (d:Doc) WHERE d.embedding IS NOT NULL RETURN count(d) AS n",
            "CALLS edges": "MATCH ()-[r:CALLS]->()    RETURN count(r) AS n",
            "CONTAINS edges": "MATCH ()-[r:CONTAINS]->() RETURN count(r) AS n",
            "EXTENDS edges": "MATCH ()-[r:EXTENDS]->()  RETURN count(r) AS n",
            "USES_TYPE edges": "MATCH ()-[r:USES_TYPE]->() RETURN count(r) AS n",
            "IMPORTS edges": "MATCH ()-[r:IMPORTS]->()  RETURN count(r) AS n",
            "MENTIONS edges": "MATCH ()-[r:MENTIONS]->() RETURN count(r) AS n",
            "interfaces": "MATCH (i:Interface) RETURN count(i) AS n",
            "folder summaries": "MATCH (s:Summary) RETURN count(s) AS n",
            "REFERENCES edges": "MATCH ()-[r:REFERENCES]->() RETURN count(r) AS n",
        }

        result: dict[str, int] = {}
        with self._session() as session:
            for label, query in counts.items():
                result[label] = session.run(query).single()["n"]
        return result

    def connectivity_stats(self) -> dict[str, int]:
        """
        Is this actually ONE graph, or several graphs in one database?

        WHY THIS EXISTS - the failure it is built to expose
        --------------------------------------------------
        Every other number in the run summary counts things that were
        CREATED, and every one of them can look healthy while the graph is
        broken in the way that matters most. Measured on a five-language demo
        repository: 74 entities, 21 CALLS edges, 100% edge precision, a clean
        summary, all tests passing - and the code graph in FIVE disconnected
        islands split exactly along language lines, with zero edges crossing
        a language boundary.

        Nothing reported that. The cross-language mechanism had silently
        produced one node and no useful edges, and the only symptom was that
        answers to "how do these services fit together?" were invented. A
        single line saying `components 5 | cross-language edges 0` would have
        made it obvious on the very first run, which is what this method is
        for.

        WHAT IS COUNTED AS A CONNECTION, AND WHY DOCS ARE COUNTED SEPARATELY
        -------------------------------------------------------------------
        Two entities are connected if a CALLS / CONTAINS / EXTENDS edge joins
        them, or if they both reference the same Interface token (a shared
        route, topic, table, env var or gRPC method) - that bridge is two
        hops through the Interface node, so it has to be walked explicitly.

        Documentation is deliberately NOT part of that count, and is reported
        as a second number instead. A MENTIONS edge from a hand-written
        architecture document will happily join every service in the
        repository, so folding docs into the main figure hides exactly the
        problem this exists to reveal: on that same demo repo the code graph
        was in five pieces, and one README pulled it into one. Seeing both
        numbers says whether the structure is real or whether prose is
        carrying it - and prose goes stale, which is the failure this whole
        system exists to prevent.
        """
        adjacency: dict[str, set[str]] = {}
        language_of: dict[str, str] = {}

        def link(a: str, b: str) -> None:
            adjacency.setdefault(a, set()).add(b)
            adjacency.setdefault(b, set()).add(a)

        with self._session() as session:
            # Every entity, so ones with no edges at all still get counted.
            for record in session.run(
                "MATCH (e:CodeEntity) RETURN e.id AS id, e.language AS language"
            ):
                adjacency.setdefault(record["id"], set())
                language_of[record["id"]] = record["language"] or "unknown"

            # Direct structural edges between two code entities. The endpoint
            # labels are checked because CONTAINS also runs File -> entity and
            # Folder -> Folder, which are not statements about code structure.
            direct_pairs = [
                (record["a"], record["b"])
                for record in session.run(
                    """
                    MATCH (a:CodeEntity)-[:CALLS|CONTAINS|EXTENDS|USES_TYPE]->(b:CodeEntity)
                    RETURN a.id AS a, b.id AS b
                    """
                )
            ]

            # The Interface bridge: a -> (:Interface) <- b.
            bridge_pairs = [
                (record["a"], record["b"])
                for record in session.run(
                    """
                    MATCH (a:CodeEntity)-[:REFERENCES]->(:Interface)<-[:REFERENCES]-(b:CodeEntity)
                    WHERE a.id < b.id
                    RETURN DISTINCT a.id AS a, b.id AS b
                    """
                )
            ]

            # The same question again, but allowing documentation to join
            # things up. Kept apart for the reason in the docstring.
            doc_pairs = [
                (record["a"], record["b"])
                for record in session.run(
                    """
                    MATCH (a:CodeEntity)<-[:MENTIONS]-(:Doc)-[:MENTIONS]->(b:CodeEntity)
                    WHERE a.id < b.id
                    RETURN DISTINCT a.id AS a, b.id AS b
                    """
                )
            ]

        for a, b in direct_pairs + bridge_pairs:
            link(a, b)

        # Compared by FAMILY, not by exact language.
        #
        # `.ts` and `.tsx` are two grammars and one module system, so an edge
        # between them is an ordinary same-project import - not the boundary
        # this number exists to measure. Counting them inflated the figure
        # with edges that were never in doubt, which is the worst thing a
        # diagnostic can do: report progress that has not happened.
        cross_language = 0
        language_pairs: set[tuple[str, str]] = set()
        for a, b in direct_pairs + bridge_pairs:
            first = language_family(language_of.get(a)) or "unknown"
            second = language_family(language_of.get(b)) or "unknown"
            if first != second:
                cross_language += 1
                language_pairs.add(tuple(sorted((first, second))))

        # Measured BEFORE documentation is folded in, so every figure below
        # describes the code structure alone.
        sizes = _component_sizes(adjacency)
        components = len(sizes)
        isolated = sum(1 for neighbours in adjacency.values() if not neighbours)

        # Now fold documentation in and ask the same question again.
        for a, b in doc_pairs:
            link(a, b)
        components_with_docs = len(_component_sizes(adjacency))

        return {
            "entities": len(adjacency),
            "languages": len({lang for lang in language_of.values()}),
            "components": components,
            "components_with_docs": components_with_docs,
            "largest_component": max(sizes, default=0),
            "isolated_entities": isolated,
            "cross_language_edges": cross_language,
            "language_pairs_linked": len(language_pairs),
        }


# Words that carry no signal in a code search and only dilute the ranking.
_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "how",
        "what",
        "where",
        "when",
        "why",
        "which",
        "who",
        "whom",
        "this",
        "that",
        "these",
        "those",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "from",
        "by",
        "and",
        "or",
        "not",
        "it",
        "its",
        "i",
        "we",
        "you",
        "can",
        "could",
        "should",
        "would",
        "will",
        "shall",
        "get",
        "show",
        "me",
        "my",
        "our",
        "please",
        "tell",
        "about",
        "does",
    }
)


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    k: int | None = None,
    weights: list[float] | None = None,
) -> dict[str, float]:
    """
    Merge several ranked lists of ids into one score per id.

    Reciprocal Rank Fusion is the standard way to combine a vector search
    with a keyword search, and it is used here because the two produce
    scores that are NOT comparable: cosine similarity lives in 0..1 while a
    BM25 score is unbounded and depends on corpus statistics. Normalising
    them against each other would be guesswork.

    RRF sidesteps that entirely by throwing the scores away and keeping only
    the RANKS: an item scores 1/(k + rank) in each list it appears in, and
    the scores are added. So an item both searches rank highly beats one that
    only a single search liked, whatever the raw numbers said.

    `k` damps the difference between the top few positions so that rank 1
    does not overwhelm rank 3. The original paper's value of 60 was tuned for
    TREC runs a THOUSAND results deep; on the ~15-item lists fused here it
    flattens rank 1 and rank 15 to within 19% of each other, while merely
    appearing in both lists is worth about 100%. That is how a rank-1 hit
    gets outvoted by weaker agreement, so the default now comes from
    config.RRF_K, which is much smaller.

    `weights` scales each list's contribution, so a project whose questions
    usually name identifiers can lean on the keyword ranking without
    abandoning the vector one. Defaults to equal weight.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    if k is None:
        k = config.RRF_K

    scores: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights):
        for position, identifier in enumerate(ranking):
            scores[identifier] = scores.get(identifier, 0.0) + weight / (k + position + 1)
    return scores


def _describe_connection(rel_type: str, outgoing: bool, seed_id: str) -> str:
    """
    Turn an edge into a short English phrase for the prompt.

    Telling the LLM "this function is CALLED BY the one you matched" rather
    than just dumping code helps it reason about direction - e.g. that a
    change here would affect the caller, not the other way round.
    """
    seed_name = seed_id.split("::")[-1]

    if rel_type == "CALLS":
        return f"calls {seed_name}" if not outgoing else f"called by {seed_name}"
    if rel_type == "CONTAINS":
        return f"defined inside {seed_name}" if outgoing else f"contains {seed_name}"
    if rel_type == "EXTENDS":
        return f"parent class of {seed_name}" if outgoing else f"extends {seed_name}"
    if rel_type == "USES_TYPE":
        # Direction matters more here than for any other edge. "the type
        # `Order` used by checkout" and "checkout, which works on this type"
        # send the model looking in opposite directions, and getting it
        # backwards is how an answer ends up describing the wrong end of a
        # dependency.
        return (
            f"the type used by {seed_name}"
            if outgoing
            else f"declares {seed_name} in its signature"
        )
    return f"connected to {seed_name}"


def _search_tokens(text: str) -> list[str]:
    """Preserve complete identifiers and add camel/snake components."""
    tokens = []
    for word in re.findall(r"[^\W]+", text, re.UNICODE):
        tokens.append(word.lower())
        parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", word).replace("_", " ").split()
        if len(parts) > 1:
            tokens.extend(p.lower() for p in parts)
    return tokens
