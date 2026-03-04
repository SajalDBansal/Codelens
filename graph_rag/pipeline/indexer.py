"""
pipeline/indexer.py
===================

THE WHOLE INDEXING RUN, IN ORDER.

If you read one file in this project, read this one. Everything else is a
detail that this file calls into.

THE SEVEN STEPS
---------------
  1. Open the local LadybugDB file and validate its typed schema.
  2. Build the Merkle hash tree of the repository on disk.
  3. Load last run's hashes out of LadybugDB and compare.  <-- the cheap part
  4. Delete anything that disappeared from disk.
  5. Re-parse only the files that changed.
  6. Embed only the code BLOCKS whose own hash changed. <-- the expensive part
  7. Resolve cross-file calls, save the edges, print a summary.

The point of steps 2-3 is to make step 6 as small as possible, because step
6 is the one that costs money and time.
"""

import time
from dataclasses import dataclass, field
from pathlib import Path

from .. import config
from ..change_detection import (
    build_merkle_tree,
    collect_file_hashes,
    collect_folder_hashes,
    diff_against_stored,
)
from ..embedding import Embedder
from ..embedding.embedder import (
    build_doc_embedding_text,
    build_embedding_text,
    embedding_fingerprint,
)
from ..models import CodeEntity, DocChunk, Relationship
from ..parsing import language_family, parse_document, parse_file, prefer_by_scope
from ..storage import GraphStore
from ..storage import schema
from .import_resolver import resolve_import
from .summarizer import Summarizer


@dataclass
class IndexReport:
    """Everything that happened during a run, for the summary at the end."""

    files_changed: int = 0
    failed_files: dict[str, str] = field(default_factory=dict)
    files_deleted: int = 0
    files_unchanged: int = 0
    folders_skipped: int = 0
    entities_written: int = 0
    entities_deleted: int = 0
    blocks_embedded: int = 0
    blocks_reused: int = 0        # unchanged blocks we did NOT pay to re-embed

    # Documentation (.md, .yaml, .json, ...) - counted separately from code
    # so you can see at a glance how much of each the repository holds.
    doc_chunks_written: int = 0
    doc_chunks_embedded: int = 0
    doc_chunks_reused: int = 0
    mentions_linked: int = 0      # doc -> code edges created

    # Go methods and Rust impl blocks joined to the type they belong to,
    # where that type lives in a different file from the member.
    members_linked: int = 0

    # Cross-language coupling: REFERENCES edges into route / env-var nodes,
    # and the tokens dropped afterwards for being too common to mean anything.
    interface_refs_written: int = 0
    interfaces_pruned: int = 0

    # Folder overviews - what makes broad "what is all this?" questions
    # answerable. Only regenerated for folders whose Merkle hash moved.
    summaries_written: int = 0
    summaries_reused: int = 0

    edges_written: int = 0

    # Pending references from EARLIER runs that we managed to link this time.
    # Watch this be non-zero on a run where you only added the callee.
    references_relinked: int = 0

    # Calls we could not turn into an edge, split by WHY - the two mean very
    # different things. "external" is the healthy kind (a call into the
    # standard library or a third-party package, which is not in the graph
    # and should not be). "ambiguous" is the lossy kind: the target IS in
    # the graph, but several entities share the name, so we refused to guess.
    calls_unresolved_external: int = 0
    calls_unresolved_ambiguous: int = 0

    # Self-calls. Neither an edge nor a miss - recorded on the node as
    # `isRecursive` so the fact is not simply lost, and counted here so it
    # never inflates the "real missed edges" number again.
    calls_recursive: int = 0

    # Edges recovered by the receiver-type and arity rules - calls that the
    # strict unique-name test alone would have abandoned as ambiguous.
    #
    # Worth its own line because it is the direct measure of what the extra
    # evidence is earning. If it is zero on a repository with inheritance,
    # the signatures are probably unannotated and the rule has nothing to
    # read.
    calls_recovered_by_evidence: int = 0

    # USES_TYPE edges written across files - a function linked to the type it
    # declares in its signature, or a class to the type of one of its fields.
    #
    # These are the structural dependencies no CALLS edge records. Before
    # they existed, the TypeScript `Order` and `Session` interfaces on the
    # demo repo had degree 1 - their file's CONTAINS edge and nothing else -
    # even though `Order` was the declared parameter type of the function
    # that checks out an order and of the component that renders it.
    type_edges_written: int = 0

    # Pending names this run managed to retire: either they linked, or they
    # were reclassified as library calls that will not be retried again.
    # Watch this drain towards zero as the graph converges.
    references_retired: int = 0

    # Whether the graph is one connected structure or several disconnected
    # ones, from GraphStore.connectivity_stats. Empty when the run failed
    # before it could be measured.
    #
    # This is the only entry here that describes the SHAPE of the result
    # rather than counting work done, and it is the one that catches the
    # failure the others cannot see: every count healthy, every test green,
    # and the graph in five language-shaped islands.
    connectivity: dict = field(default_factory=dict)

    @property
    def calls_unresolved(self) -> int:
        """Total unresolved, for the summary line."""
        return self.calls_unresolved_external + self.calls_unresolved_ambiguous
    seconds: float = 0.0


def _parent_folder(path: str) -> str | None:
    """
    The folder containing `path`.

        "src/auth/user.py" -> "src/auth"
        "README.md"        -> ""           (the repository root)
        ""                 -> None         (the root has no parent)
    """
    if path == "":
        return None
    if "/" not in path:
        return ""
    return path.rsplit("/", 1)[0]


# How much each resolution rule is worth, as the confidence stored on the
# edge it creates.
#
# These are not thresholds - an edge either exists or it does not, and that
# decision is made by the rules below. They are how much RETRIEVAL should
# trust the edge once it exists, so a neighbour reached by a certainty
# outranks one reached by an inference when the context budget is tight.
#
#   same-file      the parser saw both ends in one file. A certainty.
#   unique-name    exactly one definition in the repository answers to the
#                  name, in the caller's own language. Very likely.
#   receiver-type  several answered, and the receiver's DECLARED type picked
#                  one. Likely, but it rests on the annotation being right.
#   arity          settled only by how many arguments were passed. The
#                  weakest evidence in the system, and priced accordingly.
RESOLUTION_CONFIDENCE: dict[str, float] = {
    "same-file": 1.0,
    "linked": 0.95,
    "receiver-type": 0.8,
    "arity": 0.6,
    # A declared type is as certain as a unique name - the only question was
    # ever WHICH definition, never whether the dependency is real.
    "type-reference": 0.9,
}

# The reasons that mean an edge was actually created.
LINKED_REASONS = frozenset({"linked", "receiver-type", "arity"})


def should_reuse_block(
    stored_hash: str | None, content_hash: str, force: bool
) -> bool:
    """
    Can we skip paying to embed this block?

    `stored_hash` is what GraphStore.get_entity_hashes_for_files returned:
    the embedding-input hash if the node has a valid vector, and None if
    the node is new OR exists without one. Those two cases collapse into one
    here on purpose - both mean "there is no vector to reuse".

    WHY THIS IS A FUNCTION AND NOT FOUR LINES INLINE
    -----------------------------------------------
    It is the decision that spends money. Getting it wrong in the safe
    direction re-embeds unchanged code on every run; getting it wrong in the
    unsafe direction leaves a stale vector pointing at code that has changed,
    so retrieval quietly matches text that is no longer there.

    Neither failure raises, and neither shows up in any other number - the
    run summary reports what it decided, not whether the decision was right.
    Inline, it could only be exercised by running a real indexing pass
    against a real database, which is exactly the kind of check that does not
    get written. As a plain function it is four unit tests. Same reasoning as
    resolve_reference.
    """
    if force:
        # --force means "distrust everything", which is what makes it the
        # repair for a changed parser or a changed embedding model.
        return False
    if stored_hash is None:
        return False
    return stored_hash == content_hash


def resolve_reference(
    candidates: list[dict],
    source_id: str,
    source_language: str | None,
    visible_paths: set[str] | None = None,
    owner_hint: str | None = None,
    call_arity: int | None = None,
    allowed_kinds: frozenset[str] | None = None,
) -> tuple[str | None, str]:
    """
    Decide which entity a call by NAME actually refers to.

    `candidates` is what storage.resolve_names returned for that name:
    [{"id", "language", "filePath", "kind", "ownerName", "paramCount",
      "flexibleArity"}, ...]

    `owner_hint` is the type the CALL'S RECEIVER was declared to be, read off
    the calling function's own signature - "AuthService" for a call written
    `auth.login()` in a function taking `auth: AuthService`. `call_arity` is
    how many arguments that call passed. Both are optional: without them the
    rule behaves exactly as it did before, which is what keeps a graph
    indexed by an older version working.

    Returns (target_id_or_None, reason) where reason is one of
    "linked", "receiver-type", "arity", "external", "recursive" or
    "ambiguous". The first three all mean an edge was made, and differ only
    in how much evidence backed it - see RESOLUTION_CONFIDENCE.

    Pulled out as a plain function with no database in it so the rule can be
    tested directly - it is the single most consequential piece of judgement
    in the whole indexer, and getting it wrong either loses real edges or
    invents false ones.

    THE RULE, in order:

      1. No candidate at all -> "external". The name is nowhere in the graph,
         so this is a call into the standard library or a third-party
         package. Expected, and not a problem.

      2. Narrow to the caller's own language FAMILY. A Python `login` and a
         TypeScript `login` are unrelated functions that share a word.
         Family, not exact language, because .ts/.tsx/.js are three grammars
         but one module system.

      3. If several survive, narrow again to what the calling file can
         actually SEE - its own contents plus the files it imports.
         `visible_paths` is that set. A file cannot call something it never
         imported, so a candidate outside it is almost always the wrong one.
         This step only ever runs when the answer would otherwise be
         "ambiguous", so it can add edges but never change an existing one.

      4. Still several? Ask what the RECEIVER was declared to be. A call
         written `auth.login()` inside a function taking `auth: AuthService`
         means the AuthService one, whatever else shares the name. This is
         the step that keeps a subclass overriding a parent method from
         making both of them unlinkable - the commonest name collision there
         is in object-oriented code, and the one that cost this project an
         entire auth module.

      5. Still several? Compare how many arguments the call passed against
         what each candidate declares, ignoring any candidate whose
         signature is flexible enough that the count proves nothing.

      6. Exactly one survivor -> link it. Anything else -> "ambiguous", and
         we create NO edge. If three classes each have a `save` method we do
         not know which is meant, and a wrong edge is worse than a missing
         one: it silently sends retrieval to unrelated code.

    Every step from 2 onwards only ever NARROWS the pool, so the new evidence
    can recover an edge that was previously abandoned but can never invent
    one that was not already a candidate, nor overturn a link an earlier and
    stronger step already made.
    """
    if not candidates:
        return (None, "external")

    # A call can only land on something callable.
    #
    # Once module-level constants became entities, common method names
    # started colliding with them: `disk_folder_hashes.items()` reduces to the
    # bare name `items`, which happily matched a dataclass field called
    # `items` and produced a CALLS edge to a variable. A constant is never the
    # target of a call, and neither is a whole-file `module` node, so both are
    # ruled out before we consider anything else. Classes stay in - calling a
    # class is how most languages spell "construct one".
    # An `impl` block is also ruled out: it is named after the type it
    # implements so that its methods are filed correctly, but the type itself
    # is the thing a call means. Leaving both in made every reference to that
    # type ambiguous.
    candidates = [
        c for c in candidates if c.get("kind") not in ("constant", "module", "impl")
    ]

    # A TYPE reference is stricter still: it may only ever land on a class.
    #
    # Calls keep classes in their pool because constructing one is spelled as
    # a call in most languages. A parameter annotated `Order` is not that -
    # it means the type, and linking it to a FUNCTION that happens to be
    # called `Order` would be plainly wrong. So the caller passes the kinds
    # it will accept rather than the two rules sharing one candidate set.
    if allowed_kinds is not None:
        candidates = [c for c in candidates if c.get("kind") in allowed_kinds]

    if not candidates:
        return (None, "external")

    source_family = language_family(source_language)
    if source_family is not None:
        pool = [
            c for c in candidates
            if language_family(c.get("language")) == source_family
        ]
    else:
        pool = candidates

    if not pool:
        # Candidates exist, but none in the caller's language family. A Python
        # file calling `login` when the only `login` in the graph is
        # TypeScript is calling something OUTSIDE the graph, not something
        # ambiguous. Reporting it as ambiguous inflated the "real missed
        # edges" number with cases that were nothing of the kind.
        return (None, "external")

    if len(pool) == 1 and pool[0]["id"] != source_id:
        return (pool[0]["id"], "linked")

    # Still ambiguous by language alone. Ask LEXICAL SCOPE next, before
    # imports, because scope is the stronger signal AND the cheaper one: a
    # candidate the caller physically contains is not a guess about which
    # definition was meant, it is an observation. See parsing.code_parser.
    # prefer_by_scope for the measured cost of leaving it out - 8 of the 21
    # references this project lost to ambiguity were nested helpers whose
    # caller contained the very definition it was calling.
    if len(pool) > 1:
        chosen = prefer_by_scope([c["id"] for c in pool], source_id)
        if chosen == source_id:
            # The only sensible reading is that the entity calls itself.
            # That is a fact about the code, not a missing edge, so it must
            # not be counted as one or retried for ever.
            return (None, "recursive")
        if chosen is not None:
            return (chosen, "linked")

    # Narrow to what this file can see:
    # itself, plus everything it imports. The IMPORTS edges were already
    # being built and stored; they were simply never consulted here.
    if len(pool) > 1 and visible_paths:
        source_path = source_id.split("::")[0]
        visible = [
            c for c in pool
            if c.get("filePath") == source_path
            or c.get("filePath") in visible_paths
        ]
        if len(visible) == 1 and visible[0]["id"] != source_id:
            return (visible[0]["id"], "linked")
        if len(visible) > 1:
            # Not decisive on its own, but a strictly better pool to apply
            # the remaining evidence to.
            pool = visible

    # THE RECEIVER'S DECLARED TYPE.
    #
    # This is the step that recovers the edges an override collision used to
    # destroy. `handle_login` calls `auth.login(...)` and declares its
    # parameter `auth: AuthService`, so the call is not ambiguous at all -
    # only the bare NAME was. Both `AuthService.login` and
    # `AdminAuthService.login` answer to `login`; exactly one of them is
    # owned by the type the signature names.
    #
    # Measured on a five-language demo repo: without this the entire auth
    # module - nine entities - sat as an isolated island that no traversal
    # could reach from the API that calls into it.
    #
    # If the hint matches NOTHING it is discarded rather than allowed to rule
    # every candidate out. A capitalised receiver can be a namespace rather
    # than a type (`Math.max`), and evidence that fits no candidate is
    # evidence about something outside the graph.
    if len(pool) > 1 and owner_hint:
        by_owner = [c for c in pool if c.get("ownerName") == owner_hint]
        if not by_owner:
            # The owner may be expressed by the id rather than by a stored
            # ownerName - a nested Python class writes its methods as
            # "file.py::AuthService.login".
            by_owner = [
                c for c in pool
                if f"::{owner_hint}." in c["id"] or c["id"].endswith(f"::{owner_hint}")
            ]
        if len(by_owner) == 1 and by_owner[0]["id"] != source_id:
            return (by_owner[0]["id"], "receiver-type")
        if by_owner:
            pool = by_owner

    # ARITY, the last resort and the weakest signal.
    #
    # Deliberately last, and deliberately narrow. A declared parameter count
    # is only the number of arguments a legal call carries when the signature
    # has no defaults, varargs or optional parameters - so any candidate
    # whose arity is flexible, or whose count was never determined, is left
    # in the pool rather than ruled out. Getting this wrong is the one
    # failure mode worse than a missing edge: eliminating the RIGHT candidate
    # can leave exactly one WRONG one and link it confidently.
    if len(pool) > 1 and call_arity is not None:
        plausible = [
            c for c in pool
            if c.get("flexibleArity")
            or c.get("paramCount") is None
            or c.get("paramCount") == call_arity
        ]
        if len(plausible) == 1 and plausible[0]["id"] != source_id:
            return (plausible[0]["id"], "arity")
        if plausible:
            pool = plausible

    return (None, "ambiguous")


def resolve_owner(
    candidates: list[dict], member_id: str, member_language: str | None
) -> str | None:
    """
    Which TYPE does a Go method or a Rust `impl` belong to?

    Go writes `func (e *Engine) Apply(...)` and Rust writes `impl LineItem`,
    so in both languages the owning type is named rather than nested - and it
    may sit in a different file from the member. This is the cross-file half
    of that link; the parser already made the easy same-file one.

    The rules are deliberately tighter than for a call:

      - the candidate must be a real type (`class`). In Rust `struct
        LineItem` and `impl LineItem` share a name, so without this the impl
        block links to itself and the struct stays as disconnected as it was.
      - it must be in the same language family, for the usual reason that a
        Go `Engine` and a TypeScript `Engine` are unrelated words.
      - the same file wins over the same folder, which wins over anything
        else. Go requires a method's type to be in the same package, so
        "nearest in the tree" is not a heuristic here, it is the language
        rule.

    Returns None rather than guessing when nothing stands out - a wrong
    ownership edge would file a method under the wrong type, which is worse
    than leaving it parented to its file.
    """
    member_path = member_id.split("::")[0]
    member_folder = _parent_folder(member_path)
    family = language_family(member_language)

    pool = [
        c for c in candidates
        if c.get("kind") == "class"
        and c["id"] != member_id
        and (family is None or language_family(c.get("language")) == family)
    ]
    if not pool:
        return None

    same_file = [c for c in pool if c.get("filePath") == member_path]
    if len(same_file) == 1:
        return same_file[0]["id"]

    same_folder = [
        c for c in pool if _parent_folder(c.get("filePath") or "") == member_folder
    ]
    if len(same_folder) == 1:
        return same_folder[0]["id"]

    if len(pool) == 1:
        return pool[0]["id"]
    return None


def _path_proximity(doc_path: str, code_path: str) -> int:
    """How many leading directory segments these two paths share."""
    doc_parts = doc_path.split("/")[:-1]
    code_parts = code_path.split("/")[:-1]
    shared = 0
    for left, right in zip(doc_parts, code_parts):
        if left != right:
            break
        shared += 1
    return shared


def resolve_mention(
    candidates: list[dict], doc_path: str, max_targets: int
) -> list[str]:
    """
    Decide which code entities a `backtick` name in a document refers to.

    Returns the ids to link, which may be several - and that is the point.

    WHY THIS IS NOT THE SAME RULE AS resolve_reference
    ---------------------------------------------------
    Calls use a strict unique-match rule because a wrong CALLS edge is a lie
    about what the program does. A MENTIONS edge claims something much
    weaker - "this prose talks about this code" - so the cost of an extra one
    is a little noise in retrieval, not a false statement.

    Applying the strict rule here was a disaster in practice. On a repository
    with more than one language, `AuthService` matched 13 entities, `login`
    matched 12, and `verify` matched 14, so the unique-match test failed every
    single time and the whole feature produced ZERO edges. Documentation was
    left exactly as disconnected as it would have been without a graph at all.

    So instead:
      1. Drop `module` nodes. A doc that writes `login` means the function,
         not the whole file that happens to contain it.
      2. Prefer the candidates living nearest the document in the directory
         tree - docs/auth/README.md talking about `login` almost certainly
         means the `login` in src/auth, not the one in the Lua build script.
      3. Link up to `max_targets` of them, best first.
    """
    usable = [c for c in candidates if c.get("kind") != "module"]
    if not usable:
        usable = candidates
    if not usable:
        return []

    ranked = sorted(
        usable,
        key=lambda c: (-_path_proximity(doc_path, c.get("filePath") or ""), c["id"]),
    )
    return [c["id"] for c in ranked[:max_targets]]


def run_indexing(repo_path: Path, force: bool = False, verbose: bool = True,
                 db_path: str | Path | None = None) -> IndexReport:
    """
    Index a repository into the graph.

    `force=True` ignores every stored hash and re-parses and re-embeds
    everything. Use it after changing the parser or the embedding model,
    since in those cases the stored data is stale even though the code on
    disk did not change.
    """
    started_at = time.time()
    report = IndexReport()

    def log(message: str) -> None:
        """Print progress, unless the caller asked us to stay quiet."""
        if verbose:
            print(message)

    repo_path = repo_path.resolve()
    if not repo_path.is_dir():
        raise ValueError(f"Repository directory is unavailable: {repo_path}")

    with GraphStore.from_config(repo_path=repo_path, db_path=db_path) as store:
        # -------------------------------------------------------------
        # STEP 1: make sure the database is reachable and set up
        # -------------------------------------------------------------
        store.verify_connection()
        store.ensure_schema()

        # -------------------------------------------------------------
        # STEP 2: build the Merkle tree of what is on disk right now
        # -------------------------------------------------------------
        log(f"Scanning {repo_path} ...")
        tree = build_merkle_tree(repo_path)
        disk_folder_hashes = collect_folder_hashes(tree)
        disk_file_hashes = collect_file_hashes(tree)

        # {path: (language, doc_type)} - exactly one of the pair is set.
        # Built once here from the tree we already walked, so nothing has to
        # re-derive a file's type from its extension later on.
        file_kinds = _build_file_kind_map(tree)
        log(f"  found {len(disk_file_hashes)} indexable files in {len(disk_folder_hashes)} folders")

        # -------------------------------------------------------------
        # STEP 3: compare against last run
        # -------------------------------------------------------------
        # The stored hashes are ALWAYS loaded, even with --force.
        #
        # --force means "distrust these hashes", not "forget which files
        # existed" - and those are different instructions. Blanking the dicts
        # did both, and the second half meant a forced run could never notice
        # a deletion: `deleted` is `stored - seen`, and `{} - seen` is empty.
        # A forced run therefore left behind every node for every file that
        # had gone, which stayed searchable and answerable. See
        # change_detection.diff_against_stored.
        stored_folder_hashes = store.get_folder_hashes()
        stored_file_hashes = store.get_file_hashes()
        if force:
            log("  --force given: re-indexing everything")

        changes = diff_against_stored(
            tree, stored_folder_hashes, stored_file_hashes, force=force
        )

        report.files_changed = len(changes.changed_files)
        report.files_deleted = len(changes.deleted_files)
        report.files_unchanged = changes.unchanged_file_count
        report.folders_skipped = changes.skipped_folder_count

        log(
            f"  {len(changes.changed_files)} changed, "
            f"{len(changes.deleted_files)} deleted, "
            f"{changes.unchanged_file_count} unchanged "
            f"({changes.skipped_folder_count} folders skipped without opening)"
        )

        # THE EARLY EXIT. If the root folder hash matched, `walk` returned
        # immediately and there is genuinely nothing to do. On a large repo
        # this makes a no-op run take under a second.
        if changes.is_empty:
            log("Nothing changed. Graph is already up to date.")
            # The graph-shape block is deliberately NOT printed on this path.
            # Measuring it means reading every edge in the graph, which would
            # turn a sub-second no-op run into a slow one - and nothing can
            # have changed shape when nothing changed. Said out loud so its
            # absence reads as a decision rather than as a missing feature.
            log("(graph shape not recomputed - nothing changed. Use --force to remeasure.)")
            report.seconds = time.time() - started_at
            return report

        # -------------------------------------------------------------
        # STEP 4: remove what no longer exists
        # -------------------------------------------------------------
        for file_path in changes.deleted_files:
            log(f"  deleting {file_path}")
            store.delete_file(file_path)

        # ---- THE HASH-WRITING RULE ------------------------------------
        #
        # A stored hash is a PROMISE that everything derived from that file is
        # already in the graph. So it must not be written until that is true.
        #
        # This used to be written here, before any embedding had happened. If
        # the run then failed - an API outage, a rate limit, a Ctrl-C - the
        # hash was already committed, so the next run saw the file as
        # unchanged, never re-parsed it, and never embedded it. The entity sat
        # in the graph permanently invisible to vector search, and the CLI
        # reported a clean run. Nothing ever repaired it except --force.
        #
        # So we write the NODES now (save_entities needs the File node to
        # attach to) and the HASHES at the very end, once the expensive work
        # has actually succeeded.
        changed_folders = [
            {"path": path, "hash": hash_value, "parent": _parent_folder(path)}
            for path, hash_value in disk_folder_hashes.items()
            if force or stored_folder_hashes.get(path) != hash_value
        ]
        store.save_folders(changed_folders, with_hash=False)

        # Same for files. `language` on a File node holds the code language
        # for source files and the doc type ("markdown", "yaml") for
        # documents, so one property answers "what kind of file is this?"
        changed_file_rows = []
        for file_path in changes.changed_files:
            language, doc_type = file_kinds.get(file_path, (None, None))
            changed_file_rows.append(
                {
                    "path": file_path,
                    "hash": disk_file_hashes[file_path],
                    "language": language or doc_type or "unknown",
                    "parent": _parent_folder(file_path),
                }
            )
        store.save_files(changed_file_rows, with_hash=False)
        store.invalidate_hashes(
            changes.changed_files, [row["path"] for row in changed_folders]
        )

        # -------------------------------------------------------------
        # STEP 5: parse the changed files
        # -------------------------------------------------------------
        # Each changed file goes down ONE of two routes, decided purely by
        # its extension:
        #   code (.py, .ts, .go)   -> parse_file     -> CodeEntity nodes
        #   docs (.md, .yaml, ...) -> parse_document -> Doc nodes
        # Everything else never got into the Merkle tree in the first place.
        #
        # Both routes collect their work here and embed in big batches at the
        # end, so we make one API request per 64 items rather than per file.
        # One query for every changed file's stored block hashes, instead of
        # two round trips per file inside the loop below.
        # Read the stored hashes even under --force.
        #
        # These carry TWO pieces of information, and only one of them should
        # be ignored when forcing: whether a block still needs embedding (yes,
        # ignore that - forcing means re-embed everything) and which entities
        # the graph currently holds for this file (never ignore that - it is
        # the only way to notice one has disappeared).
        #
        # Skipping the query entirely conflated the two, so a --force run left
        # every renamed or deleted entity behind as an orphan node. That is
        # exactly the run people reach for when the parser has changed and ids
        # have moved, which is precisely when stale nodes are created - so the
        # repair step was missing at the only moment it mattered.
        stored_blocks_by_file = store.get_entity_hashes_for_files(changes.changed_files)
        stored_chunks_by_file = store.get_doc_hashes_for_files(changes.changed_files)

        entities_to_embed: list[CodeEntity] = []
        docs_to_embed: list[DocChunk] = []
        entity_language: dict[str, str] = {}
        # Every entity NAME written this run. This is the trigger set for
        # retrying references previously written off as external.
        entity_names_written: set[str] = set()
        # (source_node_id, kind, value) for every route and env-var key seen.
        all_interface_refs: list[tuple[str, str, str]] = []
        all_unresolved: list[tuple[str, str, str]] = []
        all_mentions: list[tuple[str, str]] = []
        parsed_imports: dict[str, list[str]] = {}

        for file_path in changes.changed_files:
            absolute = repo_path / file_path
            language, doc_type = file_kinds.get(file_path, (None, None))

            try:
                if language is not None:
                    parsed = parse_file(absolute, file_path)
                else:
                    parsed = parse_document(absolute, file_path)
            except Exception as error:  # noqa: BLE001
                # One unparseable file must not kill the whole run. Report it
                # and carry on with the rest of the repository.
                report.failed_files[file_path] = str(error)
                log(f"  ! could not parse {file_path}: {error}")
                continue

            if parsed is None:
                report.failed_files[file_path] = "Parser returned no result"
                log(f"  ! could not parse {file_path}: parser returned no result")
                continue

            # ==== ROUTE A: a code file ===================================
            if language is not None:
                # --- work out which BLOCKS actually changed ---------------
                # The third level of the Merkle comparison. Even in a file we
                # know changed, most functions usually did not.
                # {entity id: embedding-input hash or None} from the batch above.
                # A None value means the node exists but has no vector, which
                # has to count as "needs embedding" even when the text matches.
                stored_block_hashes = stored_blocks_by_file.get(file_path, {})

                changed_blocks: list[CodeEntity] = []
                for entity in parsed.entities:
                    if should_reuse_block(
                        stored_block_hashes.get(entity.id),
                        embedding_fingerprint(build_embedding_text(entity)), force,
                    ):
                        report.blocks_reused += 1
                    else:
                        changed_blocks.append(entity)

                # Functions that were in this file last run but are gone now.
                current_ids = {entity.id for entity in parsed.entities}
                removed_ids = [
                    entity_id for entity_id in stored_block_hashes if entity_id not in current_ids
                ]
                if removed_ids:
                    store.delete_entities(removed_ids)
                    report.entities_deleted += len(removed_ids)

                # We write ALL entities, not only the changed ones, because
                # line numbers shift when code above them is edited even if
                # their own text is identical. Property writes are cheap;
                # embeddings are not.
                store.save_entities(parsed.entities)
                report.entities_written += len(parsed.entities)

                # Remember what language each entity is written in, so the
                # cross-file resolution below can prefer same-language
                # candidates instead of tripping over name collisions.
                for entity in parsed.entities:
                    entity_language[entity.id] = entity.language
                    entity_names_written.add(entity.name)

                entities_to_embed.extend(changed_blocks)

                # Replace this file's edges wholesale: clear the old ones
                # first so a call DELETED from the code does not linger.
                store.clear_outgoing_edges(file_path)
                store.save_relationships(parsed.relationships)
                report.edges_written += len(parsed.relationships)

                all_unresolved.extend(parsed.unresolved_references)
                parsed_imports[file_path] = parsed.imports

                # The literal values this file shares with the rest of the
                # system - the only signal that crosses a language boundary.
                store.clear_interface_refs(file_path)
                all_interface_refs.extend(parsed.interface_refs)

                if verbose:
                    log(
                        f"  {file_path}: {len(parsed.entities)} entities, "
                        f"{len(changed_blocks)} to embed"
                    )

            # ==== ROUTE B: a documentation or config file ================
            else:
                # Exactly the same three-level Merkle logic as code, just
                # over doc sections instead of functions.
                stored_chunk_hashes = stored_chunks_by_file.get(file_path, {})

                changed_chunks: list[DocChunk] = []
                for chunk in parsed.chunks:
                    stored = stored_chunk_hashes.get(chunk.id)
                    hash_matches = (
                        not force and stored is not None
                        and stored == embedding_fingerprint(build_doc_embedding_text(chunk))
                    )
                    has_vector = stored is not None
                    if hash_matches and has_vector:
                        report.doc_chunks_reused += 1
                    else:
                        changed_chunks.append(chunk)

                # Sections deleted from the document since last run. This
                # matters more for docs than code: renaming a heading changes
                # the chunk id, so the old section must be cleaned up or it
                # lingers in the graph forever.
                current_ids = {chunk.id for chunk in parsed.chunks}
                removed_ids = [
                    chunk_id for chunk_id in stored_chunk_hashes if chunk_id not in current_ids
                ]
                if removed_ids:
                    store.delete_docs(removed_ids)

                store.save_docs(parsed.chunks)
                report.doc_chunks_written += len(parsed.chunks)

                docs_to_embed.extend(changed_chunks)

                store.clear_doc_outgoing_edges(file_path)
                store.save_doc_relationships(parsed.relationships)
                report.edges_written += len(parsed.relationships)

                all_mentions.extend(parsed.mentions)

                store.clear_interface_refs(file_path)
                all_interface_refs.extend(parsed.interface_refs)

                if verbose:
                    log(
                        f"  {file_path}: {len(parsed.chunks)} doc sections, "
                        f"{len(changed_chunks)} to embed"
                    )

        # -------------------------------------------------------------
        # STEP 6: embed the changed blocks and sections (the expensive step)
        # -------------------------------------------------------------
        # Everything above was cheap. THIS is the part the Merkle tree exists
        # to keep small, because this is what costs money and time.
        if entities_to_embed or docs_to_embed:
            embedder = Embedder()

            if entities_to_embed:
                log(
                    f"Embedding {len(entities_to_embed)} code blocks "
                    f"with {config.EMBEDDING_MODEL} ..."
                )
                vectors = embedder.embed_entities(entities_to_embed)
                store.save_embeddings(vectors)
                report.blocks_embedded = len(vectors)

            if docs_to_embed:
                log(f"Embedding {len(docs_to_embed)} doc sections ...")
                doc_vectors = embedder.embed_docs(docs_to_embed)
                store.save_doc_embeddings(doc_vectors)
                report.doc_chunks_embedded = len(doc_vectors)
        else:
            log("Nothing needed re-embedding.")

        # -------------------------------------------------------------
        # STEP 7a-0: file-level IMPORTS edges
        # -------------------------------------------------------------
        # These are written BEFORE call resolution, not after, because
        # resolution now uses them: knowing which files a caller imports is
        # what separates a genuinely ambiguous name from one that only looks
        # ambiguous across the whole repository. Writing them afterwards meant
        # every run resolved calls against the PREVIOUS run's import graph.
        known_files = set(disk_file_hashes)
        for file_path, import_lines in parsed_imports.items():
            language = file_kinds.get(file_path, (None, None))[0] or "unknown"
            targets = []
            for line in import_lines:
                resolved = resolve_import(line, file_path, known_files, language)
                # Never link a file to itself.
                if resolved and resolved != file_path:
                    targets.append(resolved)
            # Clear first, unconditionally: an import REMOVED from the file has
            # to lose its edge, and only a delete-then-recreate guarantees it.
            # Nothing used to delete these, so a stale IMPORTS edge survived
            # forever once created.
            store.clear_file_imports(file_path)
            if targets:
                store.save_file_imports(file_path, sorted(set(targets)))

        # -------------------------------------------------------------
        # STEP 7a: resolve cross-file calls and inheritance
        # -------------------------------------------------------------
        # The parser could only see one file at a time, so it left these as
        # names. Now that every changed file is saved, we can match them
        # against the WHOLE graph.
        # We retry EVERY pending reference in the graph, not just the ones
        # from files that changed this run. See GraphStore.get_pending_
        # references for why: a call can only be linked once both ends exist,
        # and the caller is usually not the file that changed second.
        # WHICH references get reconsidered, and why it is not "all of them".
        #
        # Ambiguous names are always retried: there are few of them, and a
        # later run really can settle them. External names - calls into the
        # standard library or a third-party package - are retried only on a
        # run that wrote an entity with that name, because that is the only
        # event that can change the answer. `names_written` is exactly that
        # set, so this step now costs what the run changed rather than what
        # the repository contains. See GraphStore.get_reference_state.
        names_written = sorted({name for name in entity_names_written})
        pending_rows = store.get_reference_state(names_written)

        if pending_rows:
            wanted_names = sorted({
                name
                for row in pending_rows
                for name in (row["unresolvedCalls"] + row["unresolvedExtends"]
                             + row["unresolvedTypes"] + row["externalCalls"]
                             + row["externalTypes"])
            })
            matches = store.resolve_names(wanted_names)
            # Read back the import graph we just wrote, so unchanged files
            # get the same disambiguation as the ones parsed this run.
            import_map = store.get_import_map()

            cross_file_edges: list[Relationship] = []
            state_rows: list[dict] = []
            retired = 0

            for row in pending_rows:
                entity_id = row["id"]
                source_path = entity_id.split("::")[0]
                visible = import_map.get(source_path)
                still_ambiguous_calls: list[str] = []
                still_ambiguous_extends: list[str] = []
                still_external: list[str] = []

                # What this entity's own source said about the calls it makes:
                # the declared type of each receiver, and how many arguments
                # each call passed. Both were read at parse time and stored on
                # the node, so they are available even for a file that has not
                # been re-parsed this run - which is exactly when a pending
                # reference finally becomes resolvable.
                owner_hints = schema.parse_hint_list(row.get("callOwnerHints"))
                arity_hints = schema.parse_hint_list(row.get("callArityHints"), int)

                def classify(name: str, kind: str) -> str:
                    match = matches.get(name) or {"candidates": [], "total": 0}
                    # More definitions share this name than we were willing to
                    # fetch, so the sample we have is an arbitrary slice and
                    # narrowing it could produce a confident wrong answer.
                    if match["total"] > config.MAX_NAME_CANDIDATES:
                        return "ambiguous"
                    target_id, reason = resolve_reference(
                        match["candidates"],
                        entity_id,
                        row["language"],
                        visible,
                        # A type reference may only ever land on a class.
                        allowed_kinds=(
                            frozenset({"class"}) if kind == "USES_TYPE" else None
                        ),
                        # Inheritance is a claim about a TYPE, not a call, so
                        # a receiver type and an argument count say nothing
                        # about it and are not offered.
                        owner_hint=owner_hints.get(name) if kind == "CALLS" else None,
                        call_arity=arity_hints.get(name) if kind == "CALLS" else None,
                    )
                    if reason in LINKED_REASONS:
                        if kind == "USES_TYPE":
                            # However it was narrowed, the fact recorded is
                            # the same: a declared dependency on a type.
                            rule = "type-reference"
                            report.type_edges_written += 1
                        elif reason == "linked":
                            rule = "unique-name"
                        else:
                            rule = reason
                            report.calls_recovered_by_evidence += 1
                        cross_file_edges.append(
                            Relationship(
                                source_id=entity_id,
                                target_id=target_id,
                                kind=kind,
                                confidence=RESOLUTION_CONFIDENCE.get(rule, 1.0),
                                resolved_by=rule,
                            )
                        )
                    return reason

                # Names we previously judged AMBIGUOUS - always retried.
                for name in row["unresolvedCalls"]:
                    reason = classify(name, "CALLS")
                    if reason == "ambiguous":
                        still_ambiguous_calls.append(name)
                        report.calls_unresolved_ambiguous += 1
                    elif reason == "external":
                        still_external.append(name)
                        report.calls_unresolved_external += 1
                    elif reason == "recursive":
                        report.calls_recursive += 1

                for name in row["unresolvedExtends"]:
                    reason = classify(name, "EXTENDS")
                    if reason in ("ambiguous", "external"):
                        # Inheritance from outside the repository is rare
                        # enough that it is not worth a separate never-retry
                        # list of its own; the volume cannot run away.
                        still_ambiguous_extends.append(name)
                        if reason == "external":
                            report.calls_unresolved_external += 1
                        else:
                            report.calls_unresolved_ambiguous += 1

                # Types named in a signature or a field whose definition is
                # in another file. The commonest case by far, since a type is
                # usually declared once and imported everywhere - so unlike
                # calls, most of these are expected to resolve.
                # Types named in a signature or a field whose definition is
                # elsewhere. Split by verdict for exactly the reason calls
                # are: "ambiguous" is worth retrying on every run, while
                # `Promise` and `HttpServletRequest` will never resolve and
                # retrying them for ever is what makes this step scale with
                # the repository instead of with what changed.
                still_unresolved_types: list[str] = []
                still_external_types: list[str] = []
                for name in row["unresolvedTypes"]:
                    reason = classify(name, "USES_TYPE")
                    if reason == "ambiguous":
                        still_unresolved_types.append(name)
                    elif reason not in LINKED_REASONS:
                        still_external_types.append(name)

                # Names previously judged EXTERNAL. Only those whose name was
                # written this run can have changed; the rest are carried
                # over untouched so we never lose them.
                retryable = set(names_written)
                for name in row["externalCalls"]:
                    if name not in retryable:
                        still_external.append(name)
                        continue
                    reason = classify(name, "CALLS")
                    if reason == "external":
                        still_external.append(name)
                        report.calls_unresolved_external += 1
                    elif reason == "ambiguous":
                        still_ambiguous_calls.append(name)
                        report.calls_unresolved_ambiguous += 1
                    elif reason == "recursive":
                        report.calls_recursive += 1

                # Type names previously judged external. Same gate as
                # externalCalls: only a run that WROTE that name can change
                # the answer, so the rest are carried over untouched.
                for name in row["externalTypes"]:
                    if name not in retryable:
                        still_external_types.append(name)
                        continue
                    reason = classify(name, "USES_TYPE")
                    if reason == "ambiguous":
                        still_unresolved_types.append(name)
                    elif reason not in LINKED_REASONS:
                        still_external_types.append(name)

                before = (len(row["unresolvedCalls"]) + len(row["unresolvedExtends"])
                          + len(row["unresolvedTypes"]) + len(row["externalCalls"])
                          + len(row["externalTypes"]))
                after = (len(set(still_ambiguous_calls))
                         + len(set(still_ambiguous_extends))
                         + len(set(still_unresolved_types))
                         + len(set(still_external_types))
                         + len(set(still_external)))
                retired += max(0, before - after)

                state_rows.append({
                    "id": entity_id,
                    "unresolvedCalls": sorted(set(still_ambiguous_calls)),
                    "unresolvedExtends": sorted(set(still_ambiguous_extends)),
                    "unresolvedTypes": sorted(set(still_unresolved_types)),
                    "externalCalls": sorted(set(still_external)),
                    "externalTypes": sorted(set(still_external_types)),
                })

            store.save_relationships(cross_file_edges)
            # Persist what we just worked out. Without this the next run
            # repeats every one of these lookups and reaches the identical
            # answer - which is what made the pending set grow for ever
            # instead of draining as the graph converged.
            store.save_reference_state(state_rows)
            report.edges_written += len(cross_file_edges)
            report.references_relinked = len(cross_file_edges)
            report.references_retired = retired

        # -------------------------------------------------------------
        # STEP 7a-1: link members to the type they belong to
        # -------------------------------------------------------------
        # Go and Rust name their owning type instead of nesting inside it, so
        # a method whose type lives in another file cannot be linked at parse
        # time. Same shape as pending calls, and it converges the same way:
        # the set drains to empty as the owning types get indexed, so a
        # settled repository pays nothing for this.
        unowned = store.get_unowned_members()
        if unowned:
            owner_names = sorted({row["ownerName"] for row in unowned})
            owner_matches = store.resolve_names(owner_names)

            owner_edges: list[Relationship] = []
            for row in unowned:
                owner_id = resolve_owner(
                    (owner_matches.get(row["ownerName"]) or {}).get("candidates", []),
                    row["id"],
                    row["language"],
                )
                if owner_id:
                    owner_edges.append(
                        Relationship(
                            source_id=owner_id, target_id=row["id"], kind="CONTAINS"
                        )
                    )

            if owner_edges:
                store.save_relationships(owner_edges)
                report.edges_written += len(owner_edges)
                report.members_linked = len(owner_edges)

        # -------------------------------------------------------------
        # STEP 7a-2: link documentation to the code it describes
        # -------------------------------------------------------------
        # THIS is what stops documentation from being a second, unrelated
        # pile of text sitting next to the code. A README that writes
        # `run_indexing` in backticks gets a MENTIONS edge to that function,
        # so retrieving either one can pull in the other.
        #
        # Same unique-match rule as calls: if three entities share the name,
        # we create no edge at all rather than guess wrong.
        if all_mentions:
            wanted = [name for _, name in all_mentions]
            matches = store.resolve_names(wanted)

            mention_edges: list[Relationship] = []
            for doc_id, name in all_mentions:
                candidates = (matches.get(name) or {}).get("candidates", [])
                # No language filter: a README describes code in every language
                # of the project, so there is no "caller language" to narrow
                # by. We rank by directory proximity instead and allow a few
                # targets - see resolve_mention for why the unique-match rule
                # used here before produced no edges at all.
                doc_path = doc_id.split("::")[0]
                for target_id in resolve_mention(
                    candidates, doc_path, config.MAX_MENTION_TARGETS
                ):
                    mention_edges.append(
                        Relationship(
                            source_id=doc_id, target_id=target_id, kind="MENTIONS"
                        )
                    )

            store.save_doc_relationships(mention_edges)
            report.mentions_linked = len(mention_edges)
            report.edges_written += len(mention_edges)

        # -------------------------------------------------------------
        # STEP 7b: link code that shares an interface token
        # -------------------------------------------------------------
        # THIS IS THE STEP THAT MAKES A MULTI-LANGUAGE REPOSITORY ONE GRAPH.
        #
        # Every other edge in the system stops at a language boundary, and
        # deliberately: matching a Python `login` to a TypeScript `login`
        # because they spell the same would invent relationships. So a repo
        # with a Python backend and a TypeScript client came out as two
        # unconnected graphs, and any question about how they fit together
        # could only be answered by the model guessing.
        #
        # Shared literal values are the honest alternative. Both ends really
        # do name "/api/cart"; that is an observation, not an inference.
        if config.EXTRACT_INTERFACES and all_interface_refs:
            store.save_interface_refs(all_interface_refs)
            report.interface_refs_written = len(all_interface_refs)
        if changes.deleted_files or (config.EXTRACT_INTERFACES and all_interface_refs):
            # Deletion-only updates can leave interfaces with no remaining
            # references, including when a folder becomes excluded.
            report.interfaces_pruned = store.prune_noisy_interfaces(
                config.MAX_INTERFACE_FANOUT
            )

        # -------------------------------------------------------------
        # STEP 7b-2: write one overview per folder
        # -------------------------------------------------------------
        # THIS IS WHAT MAKES BROAD QUESTIONS ANSWERABLE.
        #
        # Every other node in the graph is local - a function, a constant, a
        # section. So "where is the token created?" works and "what are the
        # services here and how do they fit together?" cannot: there is
        # nothing in the graph that MEANS a subsystem, so the question
        # retrieves a few arbitrary local hits and the model invents the
        # synthesis. That is precisely what the demo repository did.
        #
        # The cost is kept small by the Merkle tree that is already running:
        # a folder whose hash did not move cannot have changed inside, so its
        # summary is still true and is not rewritten. A one-file edit costs
        # one or two summary calls, not a tree walk.
        # A failed file also makes every ancestor incomplete. Keeping those
        # hashes unset lets an ordinary retry reach the failed file again.
        failed_folders: set[str] = set()
        for file_path in report.failed_files:
            folder = _parent_folder(file_path)
            while folder is not None:
                failed_folders.add(folder)
                folder = _parent_folder(folder)

        if config.GENERATE_SUMMARIES:
            stored_summary_hashes = {} if force else store.get_summary_hashes()
            stale_folders = [
                folder for folder in disk_folder_hashes
                if folder not in failed_folders
                and stored_summary_hashes.get(folder) != disk_folder_hashes[folder]
            ]
            # Read the entire folder from the updated graph, not only what
            # was parsed this run. This also covers deletion-only updates.
            entities_by_folder = store.get_entities_for_folders(stale_folders)
            store.delete_summaries(stale_folders)
            # Summaries left untouched because their folder's Merkle hash
            # did not move - the saving this step exists to deliver.
            report.summaries_reused = max(
                0, len(stored_summary_hashes) - len(stale_folders)
            )

            if stale_folders:
                log(f"Summarising {len(stale_folders)} changed folders ...")
                summarizer = Summarizer()
                summaries = []
                for folder in sorted(stale_folders):
                    if not entities_by_folder[folder]:
                        continue
                    summary = summarizer.summarise(
                        folder,
                        entities_by_folder[folder],
                        disk_folder_hashes.get(folder, ""),
                    )
                    if summary is not None:
                        summaries.append(summary)

                if summaries:
                    store.save_summaries(summaries)
                    vectors = Embedder().embed_summaries(summaries)
                    store.save_summary_embeddings(vectors)
                    report.summaries_written = len(summaries)

        else:
            # Disabling generation must not keep old searchable descriptions
            # of changed or newly excluded content (including ancestor folders).
            store.delete_summaries([row["path"] for row in changed_folders])

        store.delete_missing_summaries(list(disk_folder_hashes))

        # -------------------------------------------------------------
        # STEP 7c: tidy up folders that vanished
        # -------------------------------------------------------------
        store.delete_missing_folders(list(disk_folder_hashes))

        # -------------------------------------------------------------
        # STEP 7d: commit the hashes - the LAST thing we do
        # -------------------------------------------------------------
        # Everything derived from these files is now in the graph, so it is
        # finally safe to promise that. If any step above raised, we never get
        # here, the old hashes stand, and the next run redoes the work rather
        # than skipping it forever.
        store.save_file_hashes([
            row for row in changed_file_rows if row["path"] not in report.failed_files
        ])
        store.save_folder_hashes([
            row for row in changed_folders if row["path"] not in failed_folders
        ])

        # Whether all of that adds up to ONE graph or several. Measured after
        # every edge has been written, and stored on the report so the
        # summary can print it whether or not the run was verbose - it is the
        # one number that says if the graph is structurally sound, and it
        # stays silent about problems if you hide it behind --quiet.
        report.connectivity = store.connectivity_stats()

        report.seconds = time.time() - started_at

        if verbose:
            log("")
            log("Graph now contains:")
            for label, count in store.stats().items():
                log(f"  {label:16} {count}")

    return report


def _build_file_kind_map(tree) -> dict[str, tuple[str | None, str | None]]:
    """
    {file path: (language, doc_type)} for every file in the Merkle tree.

    Exactly one of the pair is set: `language` for a source file,
    `doc_type` for a document. Both were already worked out while hashing,
    so this just collects them rather than re-deriving anything.
    """
    mapping: dict[str, tuple[str | None, str | None]] = {}

    def walk(node) -> None:
        if node.is_dir:
            for child in node.children:
                walk(child)
        else:
            mapping[node.path] = (node.language, node.doc_type)

    walk(tree)
    return mapping
