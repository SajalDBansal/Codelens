"""
change_detection/
=================

"What actually changed since the last run?"

This folder implements the Merkle hash tree that makes re-indexing cheap.
It is pure computation: it reads files off disk and produces hashes. It
knows nothing about LadybugDB, OpenAI, or tree-sitter.

  merkle.py   Builds the hash tree and compares it against the hashes stored
              from the previous run.
"""

from .merkle import (
    ChangeSet,
    TreeNode,
    build_merkle_tree,
    collect_file_hashes,
    collect_folder_hashes,
    diff_against_stored,
)

__all__ = [
    "ChangeSet",
    "TreeNode",
    "build_merkle_tree",
    "collect_file_hashes",
    "collect_folder_hashes",
    "diff_against_stored",
]
