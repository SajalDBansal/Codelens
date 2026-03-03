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
from ..change_detection import build_merkle_tree, collect_file_hashes, collect_folder_hashes, diff_against_stored
from ..embedding import Embedder
from ..embedding.embedder import build_doc_embedding_text, build_embedding_text, embedding_fingerprint
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
    blocks_reused: int = 0
    doc_chunks_written: int = 0
    doc_chunks_embedded: int = 0
    doc_chunks_reused: int = 0
    mentions_linked: int = 0
    members_linked: int = 0
    interface_refs_written: int = 0
    interfaces_pruned: int = 0
    summaries_written: int = 0
    summaries_reused: int = 0
    edges_written: int = 0
    references_relinked: int = 0
    calls_unresolved_external: int = 0
    calls_unresolved_ambiguous: int = 0
    calls_recursive: int = 0
    calls_recovered_by_evidence: int = 0
    type_edges_written: int = 0
    references_retired: int = 0
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
    if path == '':
        return None
    if '/' not in path:
        return ''
    return path.rsplit('/', 1)[0]
RESOLUTION_CONFIDENCE: dict[str, float] = {'same-file': 1.0, 'linked': 0.95, 'receiver-type': 0.8, 'arity': 0.6, 'type-reference': 0.9}
LINKED_REASONS = frozenset({'linked', 'receiver-type', 'arity'})

def should_reuse_block(stored_hash: str | None, content_hash: str, force: bool) -> bool:
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
        return False
    if stored_hash is None:
        return False
    return stored_hash == content_hash

def resolve_reference(candidates: list[dict], source_id: str, source_language: str | None, visible_paths: set[str] | None=None, owner_hint: str | None=None, call_arity: int | None=None, allowed_kinds: frozenset[str] | None=None) -> tuple[str | None, str]:
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
        return (None, 'external')
    candidates = [c for c in candidates if c.get('kind') not in ('constant', 'module', 'impl')]
    if allowed_kinds is not None:
        candidates = [c for c in candidates if c.get('kind') in allowed_kinds]
    if not candidates:
        return (None, 'external')
    source_family = language_family(source_language)
    if source_family is not None:
        pool = [c for c in candidates if language_family(c.get('language')) == source_family]
    else:
        pool = candidates
    if not pool:
        return (None, 'external')
    if len(pool) == 1 and pool[0]['id'] != source_id:
        return (pool[0]['id'], 'linked')
    if len(pool) > 1:
        chosen = prefer_by_scope([c['id'] for c in pool], source_id)
        if chosen == source_id:
            return (None, 'recursive')
        if chosen is not None:
            return (chosen, 'linked')
    if len(pool) > 1 and visible_paths:
        source_path = source_id.split('::')[0]
        visible = [c for c in pool if c.get('filePath') == source_path or c.get('filePath') in visible_paths]
        if len(visible) == 1 and visible[0]['id'] != source_id:
            return (visible[0]['id'], 'linked')
        if len(visible) > 1:
            pool = visible
    if len(pool) > 1 and owner_hint:
        by_owner = [c for c in pool if c.get('ownerName') == owner_hint]
        if not by_owner:
            by_owner = [c for c in pool if f'::{owner_hint}.' in c['id'] or c['id'].endswith(f'::{owner_hint}')]
        if len(by_owner) == 1 and by_owner[0]['id'] != source_id:
            return (by_owner[0]['id'], 'receiver-type')
        if by_owner:
            pool = by_owner
    if len(pool) > 1 and call_arity is not None:
        plausible = [c for c in pool if c.get('flexibleArity') or c.get('paramCount') is None or c.get('paramCount') == call_arity]
        if len(plausible) == 1 and plausible[0]['id'] != source_id:
            return (plausible[0]['id'], 'arity')
        if plausible:
            pool = plausible
    return (None, 'ambiguous')

def resolve_owner(candidates: list[dict], member_id: str, member_language: str | None) -> str | None:
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
    member_path = member_id.split('::')[0]
    member_folder = _parent_folder(member_path)
    family = language_family(member_language)
    pool = [c for c in candidates if c.get('kind') == 'class' and c['id'] != member_id and (family is None or language_family(c.get('language')) == family)]
    if not pool:
        return None
    same_file = [c for c in pool if c.get('filePath') == member_path]
    if len(same_file) == 1:
        return same_file[0]['id']
    same_folder = [c for c in pool if _parent_folder(c.get('filePath') or '') == member_folder]
    if len(same_folder) == 1:
        return same_folder[0]['id']
    if len(pool) == 1:
        return pool[0]['id']
    return None

def _path_proximity(doc_path: str, code_path: str) -> int:
    """How many leading directory segments these two paths share."""
    doc_parts = doc_path.split('/')[:-1]
    code_parts = code_path.split('/')[:-1]
    shared = 0
    for left, right in zip(doc_parts, code_parts):
        if left != right:
            break
        shared += 1
    return shared

def resolve_mention(candidates: list[dict], doc_path: str, max_targets: int) -> list[str]:
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
    usable = [c for c in candidates if c.get('kind') != 'module']
    if not usable:
        usable = candidates
    if not usable:
        return []
    ranked = sorted(usable, key=lambda c: (-_path_proximity(doc_path, c.get('filePath') or ''), c['id']))
    return [c['id'] for c in ranked[:max_targets]]

def run_indexing(repo_path: Path, force: bool=False, verbose: bool=True, db_path: str | Path | None=None) -> IndexReport:
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
        raise ValueError(f'Repository directory is unavailable: {repo_path}')
    with GraphStore.from_config(repo_path=repo_path, db_path=db_path) as store:
        store.verify_connection()
        store.ensure_schema()
        log(f'Scanning {repo_path} ...')
        tree = build_merkle_tree(repo_path)
        disk_folder_hashes = collect_folder_hashes(tree)
        disk_file_hashes = collect_file_hashes(tree)
        file_kinds = _build_file_kind_map(tree)
        log(f'  found {len(disk_file_hashes)} indexable files in {len(disk_folder_hashes)} folders')
        stored_folder_hashes = store.get_folder_hashes()
        stored_file_hashes = store.get_file_hashes()
        if force:
            log('  --force given: re-indexing everything')
        changes = diff_against_stored(tree, stored_folder_hashes, stored_file_hashes, force=force)
        report.files_changed = len(changes.changed_files)
        report.files_deleted = len(changes.deleted_files)
        report.files_unchanged = changes.unchanged_file_count
        report.folders_skipped = changes.skipped_folder_count
        log(f'  {len(changes.changed_files)} changed, {len(changes.deleted_files)} deleted, {changes.unchanged_file_count} unchanged ({changes.skipped_folder_count} folders skipped without opening)')
        if changes.is_empty:
            log('Nothing changed. Graph is already up to date.')
            log('(graph shape not recomputed - nothing changed. Use --force to remeasure.)')
            report.seconds = time.time() - started_at
            return report
        for file_path in changes.deleted_files:
            log(f'  deleting {file_path}')
            store.delete_file(file_path)
        changed_folders = [{'path': path, 'hash': hash_value, 'parent': _parent_folder(path)} for path, hash_value in disk_folder_hashes.items() if force or stored_folder_hashes.get(path) != hash_value]
        store.save_folders(changed_folders, with_hash=False)
        changed_file_rows = []
        for file_path in changes.changed_files:
            language, doc_type = file_kinds.get(file_path, (None, None))
            changed_file_rows.append({'path': file_path, 'hash': disk_file_hashes[file_path], 'language': language or doc_type or 'unknown', 'parent': _parent_folder(file_path)})
        store.save_files(changed_file_rows, with_hash=False)
        store.invalidate_hashes(changes.changed_files, [row['path'] for row in changed_folders])
        stored_blocks_by_file = store.get_entity_hashes_for_files(changes.changed_files)
        stored_chunks_by_file = store.get_doc_hashes_for_files(changes.changed_files)
        entities_to_embed: list[CodeEntity] = []
        docs_to_embed: list[DocChunk] = []
        entity_language: dict[str, str] = {}
        entity_names_written: set[str] = set()
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
            except Exception as error:
                report.failed_files[file_path] = str(error)
                log(f'  ! could not parse {file_path}: {error}')
                continue
            if parsed is None:
                report.failed_files[file_path] = 'Parser returned no result'
                log(f'  ! could not parse {file_path}: parser returned no result')
                continue
            if language is not None:
                stored_block_hashes = stored_blocks_by_file.get(file_path, {})
                changed_blocks: list[CodeEntity] = []
                for entity in parsed.entities:
                    if should_reuse_block(stored_block_hashes.get(entity.id), embedding_fingerprint(build_embedding_text(entity)), force):
                        report.blocks_reused += 1
                    else:
                        changed_blocks.append(entity)
                current_ids = {entity.id for entity in parsed.entities}
                removed_ids = [entity_id for entity_id in stored_block_hashes if entity_id not in current_ids]
                if removed_ids:
                    store.delete_entities(removed_ids)
                    report.entities_deleted += len(removed_ids)
                store.save_entities(parsed.entities)
                report.entities_written += len(parsed.entities)
                for entity in parsed.entities:
                    entity_language[entity.id] = entity.language
                    entity_names_written.add(entity.name)
                entities_to_embed.extend(changed_blocks)
                store.clear_outgoing_edges(file_path)
                store.save_relationships(parsed.relationships)
                report.edges_written += len(parsed.relationships)
                all_unresolved.extend(parsed.unresolved_references)
                parsed_imports[file_path] = parsed.imports
                store.clear_interface_refs(file_path)
                all_interface_refs.extend(parsed.interface_refs)
                if verbose:
                    log(f'  {file_path}: {len(parsed.entities)} entities, {len(changed_blocks)} to embed')
            else:
                stored_chunk_hashes = stored_chunks_by_file.get(file_path, {})
                changed_chunks: list[DocChunk] = []
                for chunk in parsed.chunks:
                    stored = stored_chunk_hashes.get(chunk.id)
                    hash_matches = not force and stored is not None and (stored == embedding_fingerprint(build_doc_embedding_text(chunk)))
                    has_vector = stored is not None
                    if hash_matches and has_vector:
                        report.doc_chunks_reused += 1
                    else:
                        changed_chunks.append(chunk)
                current_ids = {chunk.id for chunk in parsed.chunks}
                removed_ids = [chunk_id for chunk_id in stored_chunk_hashes if chunk_id not in current_ids]
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
                    log(f'  {file_path}: {len(parsed.chunks)} doc sections, {len(changed_chunks)} to embed')
        if entities_to_embed or docs_to_embed:
            embedder = Embedder()
            if entities_to_embed:
                log(f'Embedding {len(entities_to_embed)} code blocks with {config.EMBEDDING_MODEL} ...')
                vectors = embedder.embed_entities(entities_to_embed)
                store.save_embeddings(vectors)
                report.blocks_embedded = len(vectors)
            if docs_to_embed:
                log(f'Embedding {len(docs_to_embed)} doc sections ...')
                doc_vectors = embedder.embed_docs(docs_to_embed)
                store.save_doc_embeddings(doc_vectors)
                report.doc_chunks_embedded = len(doc_vectors)
        else:
            log('Nothing needed re-embedding.')
        known_files = set(disk_file_hashes)
        for file_path, import_lines in parsed_imports.items():
            language = file_kinds.get(file_path, (None, None))[0] or 'unknown'
            targets = []
            for line in import_lines:
                resolved = resolve_import(line, file_path, known_files, language)
                if resolved and resolved != file_path:
                    targets.append(resolved)
            store.clear_file_imports(file_path)
            if targets:
                store.save_file_imports(file_path, sorted(set(targets)))
        names_written = sorted({name for name in entity_names_written})
        pending_rows = store.get_reference_state(names_written)
        if pending_rows:
            wanted_names = sorted({name for row in pending_rows for name in row['unresolvedCalls'] + row['unresolvedExtends'] + row['unresolvedTypes'] + row['externalCalls'] + row['externalTypes']})
            matches = store.resolve_names(wanted_names)
            import_map = store.get_import_map()
            cross_file_edges: list[Relationship] = []
            state_rows: list[dict] = []
            retired = 0
            for row in pending_rows:
                entity_id = row['id']
                source_path = entity_id.split('::')[0]
                visible = import_map.get(source_path)
                still_ambiguous_calls: list[str] = []
                still_ambiguous_extends: list[str] = []
                still_external: list[str] = []
                owner_hints = schema.parse_hint_list(row.get('callOwnerHints'))
                arity_hints = schema.parse_hint_list(row.get('callArityHints'), int)

                def classify(name: str, kind: str) -> str:
                    match = matches.get(name) or {'candidates': [], 'total': 0}
                    if match['total'] > config.MAX_NAME_CANDIDATES:
                        return 'ambiguous'
                    target_id, reason = resolve_reference(match['candidates'], entity_id, row['language'], visible, allowed_kinds=frozenset({'class'}) if kind == 'USES_TYPE' else None, owner_hint=owner_hints.get(name) if kind == 'CALLS' else None, call_arity=arity_hints.get(name) if kind == 'CALLS' else None)
                    if reason in LINKED_REASONS:
                        if kind == 'USES_TYPE':
                            rule = 'type-reference'
                            report.type_edges_written += 1
                        elif reason == 'linked':
                            rule = 'unique-name'
                        else:
                            rule = reason
                            report.calls_recovered_by_evidence += 1
                        cross_file_edges.append(Relationship(source_id=entity_id, target_id=target_id, kind=kind, confidence=RESOLUTION_CONFIDENCE.get(rule, 1.0), resolved_by=rule))
                    return reason
                for name in row['unresolvedCalls']:
                    reason = classify(name, 'CALLS')
                    if reason == 'ambiguous':
                        still_ambiguous_calls.append(name)
                        report.calls_unresolved_ambiguous += 1
                    elif reason == 'external':
                        still_external.append(name)
                        report.calls_unresolved_external += 1
                    elif reason == 'recursive':
                        report.calls_recursive += 1
                for name in row['unresolvedExtends']:
                    reason = classify(name, 'EXTENDS')
                    if reason in ('ambiguous', 'external'):
                        still_ambiguous_extends.append(name)
                        if reason == 'external':
                            report.calls_unresolved_external += 1
                        else:
                            report.calls_unresolved_ambiguous += 1
                still_unresolved_types: list[str] = []
                still_external_types: list[str] = []
                for name in row['unresolvedTypes']:
                    reason = classify(name, 'USES_TYPE')
                    if reason == 'ambiguous':
                        still_unresolved_types.append(name)
                    elif reason not in LINKED_REASONS:
                        still_external_types.append(name)
                retryable = set(names_written)
                for name in row['externalCalls']:
                    if name not in retryable:
                        still_external.append(name)
                        continue
                    reason = classify(name, 'CALLS')
                    if reason == 'external':
                        still_external.append(name)
                        report.calls_unresolved_external += 1
                    elif reason == 'ambiguous':
                        still_ambiguous_calls.append(name)
                        report.calls_unresolved_ambiguous += 1
                    elif reason == 'recursive':
                        report.calls_recursive += 1
                for name in row['externalTypes']:
                    if name not in retryable:
                        still_external_types.append(name)
                        continue
                    reason = classify(name, 'USES_TYPE')
                    if reason == 'ambiguous':
                        still_unresolved_types.append(name)
                    elif reason not in LINKED_REASONS:
                        still_external_types.append(name)
                before = len(row['unresolvedCalls']) + len(row['unresolvedExtends']) + len(row['unresolvedTypes']) + len(row['externalCalls']) + len(row['externalTypes'])
                after = len(set(still_ambiguous_calls)) + len(set(still_ambiguous_extends)) + len(set(still_unresolved_types)) + len(set(still_external_types)) + len(set(still_external))
                retired += max(0, before - after)
                state_rows.append({'id': entity_id, 'unresolvedCalls': sorted(set(still_ambiguous_calls)), 'unresolvedExtends': sorted(set(still_ambiguous_extends)), 'unresolvedTypes': sorted(set(still_unresolved_types)), 'externalCalls': sorted(set(still_external)), 'externalTypes': sorted(set(still_external_types))})
            store.save_relationships(cross_file_edges)
            store.save_reference_state(state_rows)
            report.edges_written += len(cross_file_edges)
            report.references_relinked = len(cross_file_edges)
            report.references_retired = retired
        unowned = store.get_unowned_members()
        if unowned:
            owner_names = sorted({row['ownerName'] for row in unowned})
            owner_matches = store.resolve_names(owner_names)
            owner_edges: list[Relationship] = []
            for row in unowned:
                owner_id = resolve_owner((owner_matches.get(row['ownerName']) or {}).get('candidates', []), row['id'], row['language'])
                if owner_id:
                    owner_edges.append(Relationship(source_id=owner_id, target_id=row['id'], kind='CONTAINS'))
            if owner_edges:
                store.save_relationships(owner_edges)
                report.edges_written += len(owner_edges)
                report.members_linked = len(owner_edges)
        if all_mentions:
            wanted = [name for _, name in all_mentions]
            matches = store.resolve_names(wanted)
            mention_edges: list[Relationship] = []
            for doc_id, name in all_mentions:
                candidates = (matches.get(name) or {}).get('candidates', [])
                doc_path = doc_id.split('::')[0]
                for target_id in resolve_mention(candidates, doc_path, config.MAX_MENTION_TARGETS):
                    mention_edges.append(Relationship(source_id=doc_id, target_id=target_id, kind='MENTIONS'))
            store.save_doc_relationships(mention_edges)
            report.mentions_linked = len(mention_edges)
            report.edges_written += len(mention_edges)
        if config.EXTRACT_INTERFACES and all_interface_refs:
            store.save_interface_refs(all_interface_refs)
            report.interface_refs_written = len(all_interface_refs)
        if changes.deleted_files or (config.EXTRACT_INTERFACES and all_interface_refs):
            report.interfaces_pruned = store.prune_noisy_interfaces(config.MAX_INTERFACE_FANOUT)
        failed_folders: set[str] = set()
        for file_path in report.failed_files:
            folder = _parent_folder(file_path)
            while folder is not None:
                failed_folders.add(folder)
                folder = _parent_folder(folder)
        store.delete_missing_summaries(list(disk_folder_hashes))
        store.delete_missing_folders(list(disk_folder_hashes))
        store.save_file_hashes([row for row in changed_file_rows if row['path'] not in report.failed_files])
        store.save_folder_hashes([row for row in changed_folders if row['path'] not in failed_folders])
        report.connectivity = store.connectivity_stats()
        report.seconds = time.time() - started_at
        if verbose:
            log('')
            log('Graph now contains:')
            for label, count in store.stats().items():
                log(f'  {label:16} {count}')
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
