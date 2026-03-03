"""
pipeline/
=========

"Run the whole indexing job."

This is the orchestrator - the folder that knows the ORDER things happen in.
It does no parsing, no hashing, no database work and no embedding of its own;
it calls change_detection/, parsing/, embedding/ and storage/ in the right
sequence and reports what happened.

  indexer.py        The full incremental indexing run.
  import_resolver.py  Best-effort "which file does this import line point at?"

If you want to understand the system end to end, read indexer.py - it is the
narrative version of everything else.
"""

from .indexer import IndexReport, resolve_owner, resolve_reference, run_indexing

__all__ = ["IndexReport", "resolve_owner", "resolve_reference", "run_indexing"]
