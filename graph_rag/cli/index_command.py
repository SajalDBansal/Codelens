"""
cli/index_command.py
====================

ENTRY POINT 1 of 2:  `graph-rag-index`

Reads a codebase, stores its structure as a graph in SQLite, and creates the
embeddings. Run it once to build the graph, then again any time the code
changes - the second run only touches what actually changed.

USAGE
-----
    graph-rag-index                     index the current folder
    graph-rag-index ../some/project     index another folder
    graph-rag-index --force             re-index everything from scratch
    graph-rag-index --quiet             only print the final summary

You can also run it without installing the package:

    python -m graph_rag.cli.index_command
"""

import argparse
import sys
from pathlib import Path

from .. import config
from ._console import configure_console
from ..parsing.languages import supported_doc_types, supported_languages
from ..pipeline import run_indexing


def build_parser() -> argparse.ArgumentParser:
    """
    Describe the command line options.

    `argparse` is Python's built-in library for this. It reads the options
    below and automatically gives us `--help` output for free.
    """
    parser = argparse.ArgumentParser(
        prog="graph-rag-index",
        description=(
            "Index a codebase into a SQLite graph with embeddings.\n"
            "Code languages: " + ", ".join(supported_languages()) + "\n"
            "Document types: " + ", ".join(supported_doc_types())
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "path",
        nargs="?",          # "?" means the argument is optional
        default=".",        # "." means "the folder I am standing in"
        help="Folder to index (default: the current folder)",
    )
    parser.add_argument(
        "--force",
        action="store_true",   # a flag: present = True, absent = False
        help=(
            "Ignore all stored hashes and re-parse and re-embed everything. "
            "Use after changing the parser. For a new embedding model, use a new --db file."
        ),
    )
    parser.add_argument(
        "--quiet",
        action="store_true",
        help="Only print the final summary",
    )
    parser.add_argument("--db", help="Local SQLite index file (default: .codelens/graph.sqlite in the repository)")
    return parser


def main(argv=None, *, _managed=False):
    """The function that runs when you type `graph-rag-index`."""
    if not _managed:
        from .app import main as run_cli
        return run_cli(["embed", *(sys.argv[1:] if argv is None else argv)])
    # Indexing prints file paths, and a path can hold any character at all.
    configure_console()

    args = build_parser().parse_args(argv)

    # Fail immediately if the managed settings are incomplete,
    # instead of failing later inside the OpenAI or SQLite library.
    config.validate()

    repo_path = Path(args.path)
    if not repo_path.is_dir():
        raise SystemExit(f"Not a folder: {repo_path}")

    report = run_indexing(repo_path, force=args.force, verbose=not args.quiet, db_path=args.db)

    # ---- the summary -------------------------------------------------
    print("")
    print("=" * 52)
    print(f"Indexed {repo_path.resolve()} in {report.seconds:.1f}s")
    print("=" * 52)
    print(f"  files changed        {report.files_changed}")
    print(f"  files failed         {len(report.failed_files)}")
    print(f"  files deleted        {report.files_deleted}")
    print(f"  files unchanged      {report.files_unchanged}")
    print(f"  folders skipped      {report.folders_skipped}   <- Merkle tree savings")
    print(f"  entities written     {report.entities_written}")
    print(f"  entities deleted     {report.entities_deleted}")
    print(f"  blocks embedded      {report.blocks_embedded}   <- the part that costs money")
    print(f"  blocks reused        {report.blocks_reused}   <- unchanged, not re-embedded")
    print(f"  doc sections written {report.doc_chunks_written}")
    print(f"  doc sections embed. {report.doc_chunks_embedded}")
    print(f"  doc sections reused  {report.doc_chunks_reused}")
    print(f"  doc -> code links    {report.mentions_linked}   <- MENTIONS edges")

    if report.members_linked:
        print(f"  members -> types     {report.members_linked}   <- Go receivers, Rust impls")
    if report.interface_refs_written:
        print(
            f"  interface links      {report.interface_refs_written}"
            f"   <- routes/env vars, the CROSS-LANGUAGE edges"
        )
    if report.interfaces_pruned:
        print(f"  interfaces pruned    {report.interfaces_pruned}   (too common to mean anything)")
    if report.summaries_written or report.summaries_reused:
        print(f"  folder overviews     {report.summaries_written} written, "
              f"{report.summaries_reused} reused   <- answers broad questions")

    print(f"  edges written        {report.edges_written}")

    if report.calls_unresolved:
        # Reported on purpose rather than hidden, and split by cause, because
        # the two mean very different things:
        #   external  - a call into the stdlib or a library. Expected. Fine.
        #   ambiguous - the target IS in the graph but several entities share
        #               the name, so we refused to guess. THIS is real signal:
        #               if it is large, the call graph has holes in it.
        print(f"  calls -> library     {report.calls_unresolved_external}   (expected, not in graph)")
        print(f"  calls ambiguous      {report.calls_unresolved_ambiguous}   <- real missed edges")

    if report.type_edges_written:
        # Structural dependencies that no call records: a function linked to
        # the type in its signature, a class to the type of its fields.
        print(
            f"  type edges           {report.type_edges_written}"
            f"   <- USES_TYPE, signatures and fields"
        )

    if report.calls_recovered_by_evidence:
        # Calls the strict unique-name rule would have abandoned, linked
        # because the caller's own signature said which definition it meant.
        # These are exactly the edges lost to a subclass overriding a
        # parent's method.
        print(
            f"  calls by evidence    {report.calls_recovered_by_evidence}"
            f"   <- receiver type / arity settled an ambiguous name"
        )

    if report.calls_recursive:
        print(f"  recursive calls      {report.calls_recursive}   (a fact, not a missed edge)")
    if report.references_retired:
        # The number that says the pending set is DRAINING rather than
        # growing. Before references were written back, every name stayed
        # pending for ever and this step re-did the same work on every run.
        print(
            f"  pending retired      {report.references_retired}"
            f"   <- names that will not be retried again"
        )

    _print_connectivity(report.connectivity)

    print("")
    if report.failed_files:
        print("Indexing is incomplete. These files will be retried on the next run:")
        for path, reason in sorted(report.failed_files.items()):
            print(f"  {path}: {reason}")
        raise SystemExit(1)

    database_path = Path(
        args.db or repo_path.resolve() / ".codelens" / "graph.sqlite"
    ).expanduser().resolve()
    print(f"  database             {database_path}")
    return report


def _print_connectivity(shape: dict) -> None:
    """
    Report whether the result is ONE graph or several.

    Every other line of the summary counts work that was done, and all of
    them can read healthy while the graph is broken in the way that matters
    most to a multi-language repository. This block is the counterweight: it
    describes the shape of what came out, not the effort that went in.

    It earns its place by having caught a real failure. A five-language demo
    repo indexed with 100% edge precision, a clean summary and a green test
    suite - and a code graph in five disconnected islands split exactly along
    language lines, with not one edge crossing between them. Answers to "how
    do these services fit together?" were fluent and invented. Nothing in the
    old summary hinted at it; `components 5` would have.
    """
    if not shape:
        return

    print("")
    print("  --- graph shape ---")
    print(f"  connected pieces     {shape['components']}   <- 1 is ideal; more means islands")
    print(f"  largest piece        {shape['largest_component']} of {shape['entities']} entities")
    print(f"  isolated entities    {shape['isolated_entities']}   (nothing links to them at all)")

    if shape["languages"] > 1:
        print(
            f"  cross-language edges {shape['cross_language_edges']}"
            f"   ({shape['language_pairs_linked']} language pairs joined, "
            f"{shape['languages']} languages present)"
        )

    # The number that says whether the structure is real or whether prose is
    # holding it together. A big gap means a hand-written architecture
    # document is doing the work the code analysis failed to do - and that
    # document will go stale, which is the exact failure this system exists
    # to prevent.
    if shape["components_with_docs"] < shape["components"]:
        print(
            f"  with docs counted    {shape['components_with_docs']}"
            f"   <- documentation is bridging "
            f"{shape['components'] - shape['components_with_docs']} of those gaps"
        )

    if shape["languages"] > 1 and shape["cross_language_edges"] == 0:
        print("")
        print(
            f"  ! {shape['languages']} languages are indexed and NOTHING connects them.\n"
            "    Calls are resolved within a language family on purpose, so the only\n"
            "    thing that can join two languages is a shared literal - a route, a\n"
            "    queue topic, a table name, an env var, a gRPC method. None matched.\n"
            "    Questions spanning services will be answered from documentation if\n"
            "    you have any, and guessed if you do not."
        )


# This lets you run the file directly with
#     python -m graph_rag.cli.index_command
# `__name__` is "__main__" only when the file is run as a program, not when
# it is imported by something else.
if __name__ == "__main__":
    main()
