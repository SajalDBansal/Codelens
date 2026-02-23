"""
change_detection/merkle.py
==========================

THE IDEA (this is the heart of the incremental design)
------------------------------------------------------
We hash the codebase at three levels, where each level's hash is built from
the level below it. That structure is called a *Merkle tree*.

    block hash   = sha256( the text of one function or class )
    file hash    = sha256( the text of the whole file )
    folder hash  = sha256( the names + hashes of everything directly inside )

Because a folder's hash is computed FROM its children's hashes, one rule
falls out for free:

    If a folder's hash is unchanged, then NOTHING anywhere inside it has
    changed - not in its files, not in its sub-folders, not at any depth.

So indexing becomes a cheap descent:

    1. Compare the ROOT folder hash to the one saved last run.
       Same? The entire repository is untouched. Stop. Do nothing.
    2. Different? Look at the root's children. Skip every child whose hash
       matches; descend only into the ones that differ.
    3. Repeat until we reach the actual changed FILES.
    4. Inside a changed file, compare each function's BLOCK hash to find
       which specific functions changed, and re-embed only those.

A one-line edit in a 5000-file repo therefore re-embeds one function.

AN HONEST NOTE ON WHAT THIS SAVES
---------------------------------
To compute the hashes we still have to READ every code file from disk. Disk
reads are fast and free. What the tree saves us is the expensive work:
tree-sitter parsing, OpenAI embedding calls (which cost money), and LadybugDB
writes. Those are what get skipped for unchanged subtrees.
"""

import hashlib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .. import config
from ..parsing.languages import classify, looks_binary


# ---------------------------------------------------------------------------
# The tree we build in memory
# ---------------------------------------------------------------------------

@dataclass
class TreeNode:
    """
    One entry in the Merkle tree: either a folder or a file.

    `path` is always relative to the repository root and uses forward
    slashes, so the same repo produces identical hashes on Windows and Linux.
    The root folder's path is "" (an empty string).
    """

    path: str
    is_dir: bool
    hash: str
    # Only folders have children. Files always have an empty list.
    children: list["TreeNode"] = field(default_factory=list)
    # Only files have these. Folders leave both as None.
    # Exactly one is set: `language` for code, `doc_type` for a document.
    language: Optional[str] = None
    doc_type: Optional[str] = None


def _hash_bytes(data: bytes) -> str:
    """sha256 of raw bytes, as a hex string."""
    return hashlib.sha256(data).hexdigest()


def _hash_children(children: list[TreeNode]) -> str:
    """
    Combine child hashes into one folder hash.

    Two details that matter:

    1. We SORT the entries. The operating system can hand us files in any
       order, and an unsorted list would produce a different hash on
       different machines for identical content.

    2. We include the child's NAME and a "d"/"f" marker, not just its hash.
       Without the name, renaming a file without changing its contents would
       leave the folder hash identical, and we would never notice the rename.
    """
    lines = []
    for child in sorted(children, key=lambda c: c.path):
        marker = "d" if child.is_dir else "f"
        name = child.path.rsplit("/", 1)[-1]
        lines.append(f"{marker}:{name}:{child.hash}")

    joined = "\n".join(lines)
    return _hash_bytes(joined.encode("utf-8"))


# ---------------------------------------------------------------------------
# Building the tree
# ---------------------------------------------------------------------------

def build_merkle_tree(root: Path) -> TreeNode:
    """
    Walk the repository and build the whole hash tree in memory.

    WHICH FILES COUNT
    -----------------
    Files we can index: code (.py, .ts, .go, ...) AND documentation or
    configuration (.md, .yaml, .json, ...). Anything else - images, lock
    files, minified bundles - is invisible to the whole system. It does not
    affect any folder hash, so editing it never triggers re-indexing.

    Folders that end up containing nothing indexable are left out entirely.
    """

    def build_folder(folder: Path, relative: str) -> Optional[TreeNode]:
        children: list[TreeNode] = []

        # `iterdir()` lists the folder's direct contents. We sort by name so
        # the walk order is predictable, which makes logs easier to read.
        try:
            entries = sorted(folder.iterdir(), key=lambda p: p.name)
        except PermissionError:
            # A folder we are not allowed to read is treated as empty rather
            # than crashing the whole indexing run.
            return None

        for entry in entries:
            if entry.is_symlink():
                # Symlinks can point outside the repo, or loop back into it
                # and cause an infinite walk. Skipping them is the safe call.
                continue

            if entry.is_dir():
                if entry.name in config.IGNORED_DIRECTORIES:
                    continue
                if entry.name.endswith(config.IGNORED_DIRECTORY_SUFFIXES):
                    continue
                child_relative = f"{relative}/{entry.name}" if relative else entry.name
                child = build_folder(entry, child_relative)
                # `None` means "this subtree had no code in it" - leave it out.
                if child is not None:
                    children.append(child)

            elif entry.is_file():
                # Work out the repo-relative path FIRST, and test that rather
                # than the absolute path on disk.
                #
                # This matters: the secret check looks at every folder on the
                # path, and an absolute path drags in the whole machine
                # layout. A project living in C:/keys/myproject would have
                # every one of its files judged secret because of a folder
                # name that is not part of the project at all.
                child_relative = f"{relative}/{entry.name}" if relative else entry.name

                language, doc_type = classify(child_relative)
                if language is None and doc_type is None:
                    continue  # secret, binary, or generated - skip silently

                size_limit = (
                    config.MAX_FILE_SIZE_BYTES
                    if language is not None
                    else config.MAX_DOC_FILE_SIZE_BYTES
                )
                try:
                    if entry.stat().st_size > size_limit:
                        continue  # oversized - usually a generated dump
                    data = entry.read_bytes()
                except OSError:
                    continue  # unreadable file - skip rather than crash

                # Last gate, and the one that makes "index unknown extensions
                # by default" safe: check the actual bytes. An unfamiliar
                # extension is accepted optimistically by classify(), so this
                # is where a binary wearing one gets caught.
                if looks_binary(data):
                    continue

                children.append(
                    TreeNode(
                        path=child_relative,
                        is_dir=False,
                        hash=_hash_bytes(data),
                        language=language,
                        doc_type=doc_type,
                    )
                )

        # A folder with nothing indexable in it is left out of the tree, so
        # it never gets a node and never appears in a diff.
        if not children:
            return None

        return TreeNode(
            path=relative,
            is_dir=True,
            hash=_hash_children(children),
            children=children,
        )

    tree = build_folder(root, relative="")

    # A repository with no supported code files at all: return an empty root
    # so callers do not have to handle `None` as a special case.
    if tree is None:
        tree = TreeNode(path="", is_dir=True, hash=_hash_children([]), children=[])

    return tree


# ---------------------------------------------------------------------------
# Flattening the tree (used when saving hashes back to LadybugDB)
# ---------------------------------------------------------------------------

def collect_folder_hashes(node: TreeNode) -> dict[str, str]:
    """Every folder in the tree, as {relative_path: hash}."""
    result: dict[str, str] = {}

    def walk(current: TreeNode) -> None:
        if current.is_dir:
            result[current.path] = current.hash
            for child in current.children:
                walk(child)

    walk(node)
    return result


def collect_file_hashes(node: TreeNode) -> dict[str, str]:
    """Every file in the tree, as {relative_path: hash}."""
    result: dict[str, str] = {}

    def walk(current: TreeNode) -> None:
        if current.is_dir:
            for child in current.children:
                walk(child)
        else:
            result[current.path] = current.hash

    walk(node)
    return result


# ---------------------------------------------------------------------------
# Comparing against last run
# ---------------------------------------------------------------------------

@dataclass
class ChangeSet:
    """The answer to 'what do we need to do this run?'"""

    # Files that are new, or whose contents changed. These get re-parsed.
    changed_files: list[str] = field(default_factory=list)

    # Files that were in the graph last run but are gone from disk now.
    # Their nodes must be deleted, or the graph slowly fills with ghosts.
    deleted_files: list[str] = field(default_factory=list)

    # Files we confirmed are unchanged and therefore skipped entirely.
    unchanged_file_count: int = 0

    # How many whole folders we skipped without looking inside.
    # This number is the payoff of the Merkle design - watch it be large on
    # the second run.
    skipped_folder_count: int = 0

    @property
    def is_empty(self) -> bool:
        """True when there is genuinely nothing to do."""
        return not self.changed_files and not self.deleted_files


def diff_against_stored(
    tree: TreeNode,
    stored_folder_hashes: dict[str, str],
    stored_file_hashes: dict[str, str],
    force: bool = False,
) -> ChangeSet:
    """
    Compare the freshly built tree against the hashes saved last run.

    `stored_folder_hashes` / `stored_file_hashes` come out of LadybugDB - see
    storage/graph_store.py.

    This is where the "skip whole folders" optimisation actually happens.

    `force` makes every file on disk count as changed, without touching how
    DELETIONS are worked out.

    WHY FORCE IS A FLAG HERE AND NOT AN EMPTY DICT
    ----------------------------------------------
    `--force` used to be implemented by passing two empty dicts, which does
    make everything look changed - and also makes the repository look as
    though it had never been indexed. Deletions are computed as
    `stored - seen`, and `{} - seen` is empty, so a forced run could not
    delete anything.

    The damage that caused is quiet and lasting. Point the indexer at a
    different repository with `--force` and the previous one's nodes simply
    stay: measured here, 74 entities from another project survived, stayed
    searchable, and were counted as 12 disconnected pieces in the graph-shape
    report. The same happens on a normal run of one repository after files
    are deleted from it - the graph keeps serving code that no longer exists,
    and an answer citing a deleted function looks exactly like a correct one.

    "Distrust the stored hashes" and "forget which files existed" are two
    different instructions. Only the first is what --force means.
    """
    changes = ChangeSet()

    # Every file path we saw on disk this run. Anything in the stored hashes
    # that is NOT in here has been deleted from the repository.
    seen_files: set[str] = set()

    def mark_subtree_seen(node: TreeNode) -> None:
        """
        Record every file under an unchanged folder as 'still exists'.

        We must do this even though we are skipping the folder. Otherwise
        those files would look deleted, and we would wipe them from the graph.
        This walk is over the in-memory tree only - no disk access.
        """
        if node.is_dir:
            for child in node.children:
                mark_subtree_seen(child)
        else:
            seen_files.add(node.path)
            changes.unchanged_file_count += 1

    def walk(node: TreeNode) -> None:
        if node.is_dir:
            # ---- THE KEY CHECK -------------------------------------------
            # If this folder's hash matches what we stored, nothing inside it
            # changed at any depth. Do not open it, do not read it, move on.
            if not force and stored_folder_hashes.get(node.path) == node.hash:
                changes.skipped_folder_count += 1
                mark_subtree_seen(node)
                return
            # --------------------------------------------------------------

            # Hash differs, so SOMETHING inside changed. Check each child to
            # narrow down where. Most children usually still match and get
            # skipped by the same check one level down.
            for child in node.children:
                walk(child)
        else:
            seen_files.add(node.path)
            if not force and stored_file_hashes.get(node.path) == node.hash:
                changes.unchanged_file_count += 1
            else:
                # New file, or its contents changed. Re-parse it.
                changes.changed_files.append(node.path)

    walk(tree)

    # Anything we stored last time but did not see on disk is gone.
    changes.deleted_files = sorted(set(stored_file_hashes) - seen_files)

    return changes
