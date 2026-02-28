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
from ..models import CodeEntity, DocChunk, Relationship
from ..parsing import language_family, parse_document, parse_file, prefer_by_scope

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
