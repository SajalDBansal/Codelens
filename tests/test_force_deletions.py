"""
tests/test_force_deletions.py
=============================

`--force` must still notice that a file is gone.

THE BUG THIS PINS
-----------------
`--force` was implemented by handing the diff two EMPTY stored-hash dicts.
That does make every file look changed - and it also makes the repository
look as though it had never been indexed at all. Deletions are computed as
`stored - seen`, and `{} - seen` is always empty, so a forced run could not
delete anything, ever.

The damage is quiet and it lasts. Point the indexer at a different repository
with `--force` and the previous one's nodes simply stay: measured on this
project, 74 entities from an unrelated demo repo survived a forced re-index,
stayed searchable, and were reported as extra disconnected pieces in the
graph-shape summary. The same thing happens to one repository over time as
files are deleted from it - the graph keeps serving code that no longer
exists, and an answer citing a deleted function is indistinguishable from a
correct one.

"Distrust the stored hashes" and "forget which files existed" are two
different instructions. `--force` only ever meant the first.
"""

from graph_rag.change_detection import diff_against_stored
from graph_rag.change_detection.merkle import TreeNode


def _file(path: str, digest: str) -> TreeNode:
    return TreeNode(path=path, is_dir=False, hash=digest)


def _tree(*children: TreeNode) -> TreeNode:
    return TreeNode(path="", is_dir=True, hash="root-hash", children=list(children))


def test_force_still_reports_a_deleted_file():
    """THE REGRESSION. `gone.py` is in the graph and not on disk."""
    tree = _tree(_file("kept.py", "h1"))
    stored_files = {"kept.py": "h1", "gone.py": "h2"}

    changes = diff_against_stored(tree, {"": "root-hash"}, stored_files, force=True)

    assert changes.deleted_files == ["gone.py"]


def test_force_treats_every_file_as_changed():
    """The half of --force that already worked must keep working."""
    tree = _tree(_file("a.py", "h1"), _file("b.py", "h2"))
    stored_files = {"a.py": "h1", "b.py": "h2"}

    changes = diff_against_stored(tree, {"": "root-hash"}, stored_files, force=True)

    assert sorted(changes.changed_files) == ["a.py", "b.py"]
    assert changes.unchanged_file_count == 0


def test_force_does_not_skip_a_folder_whose_hash_matches():
    """
    The folder-skip is the optimisation --force exists to defeat. If a
    matching folder hash could still short-circuit the walk, `--force` would
    silently do nothing at all on an unchanged repository.
    """
    tree = _tree(_file("a.py", "h1"))
    changes = diff_against_stored(
        tree, {"": "root-hash"}, {"a.py": "h1"}, force=True
    )
    assert changes.skipped_folder_count == 0
    assert changes.changed_files == ["a.py"]


def test_without_force_an_unchanged_repository_is_still_skipped():
    """
    And the optimisation itself must survive the fix - a no-op run has to
    stay a no-op, or every run pays full price.
    """
    tree = _tree(_file("a.py", "h1"))
    changes = diff_against_stored(tree, {"": "root-hash"}, {"a.py": "h1"})

    assert changes.changed_files == []
    assert changes.deleted_files == []
    assert changes.skipped_folder_count == 1


def test_deletions_are_found_on_an_ordinary_run_too():
    """The non-forced path, which always worked, is pinned alongside it."""
    tree = _tree(_file("kept.py", "h1"))
    changes = diff_against_stored(
        tree, {"": "different-hash"}, {"kept.py": "h1", "gone.py": "h2"}
    )
    assert changes.deleted_files == ["gone.py"]
