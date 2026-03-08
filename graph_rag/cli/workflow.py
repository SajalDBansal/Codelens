"""Project/model checks around the unchanged parsing and retrieval engine."""

import sys
import uuid
from pathlib import Path

from ..storage import GraphStore
from . import ask_command, index_command
from .projects import (
    Project,
    database_reference,
    ensure_gitignore,
    inspect_database,
    load_project,
    remember,
    same_path,
    save_project,
)
from .settings import Settings, engine_settings
from .state import StateError, locked, now


def select_profile(meta: dict | None, settings: Settings, policy: str | None):
    selected = (settings.embedding_model, settings.embedding_dimensions)
    if meta is None or selected == (meta["model"], meta["dimensions"]):
        return selected, False
    old = (meta["model"], meta["dimensions"])
    print(
        f"Embedding settings changed: graph uses {old[0]} / {old[1]} dimensions; "
        f"configuration selects {selected[0]} / {selected[1]}."
    )
    print("Vectors from different models or dimensions cannot be mixed.")
    if policy is None and sys.stdin.isatty():
        print("1. Keep the graph's embedding model for this command (default)")
        print("2. Rebuild all embeddings with the selected model (OpenAI usage charges apply)")
        print("3. Cancel")
        answer = input("Choose [1]: ").strip() or "1"
        policy = {"1": "keep", "2": "rebuild"}.get(answer, "error")
    if policy == "keep":
        print("Using the graph's existing embedding model; global defaults are unchanged.")
        return old, False
    if policy == "rebuild":
        return selected, True
    raise StateError(
        "Choose --on-model-change keep or --on-model-change rebuild; no model call was made."
    )


def index_project(project: Project, *, rebuild: bool, force: bool, quiet: bool):
    """Publish replacement graphs only after indexing and validation succeed."""
    from .. import config

    root = Path(project.root)
    if not root.is_dir():
        raise StateError(f"Source folder is unavailable: {root}")
    ensure_gitignore(root)
    target = project.database
    if rebuild:
        if (
            project.pending_database
            and project.pending_model == config.EMBEDDING_MODEL
            and project.pending_dimensions == config.EMBEDDING_DIM
        ):
            target = (root / project.pending_database).resolve()
            print("Resuming the pending replacement graph.")
        else:
            target = root / ".codelens" / "graphs" / uuid.uuid4().hex / "graph.lbug"
            project.pending_database = database_reference(root, target)
            project.pending_model = config.EMBEDDING_MODEL
            project.pending_dimensions = config.EMBEDDING_DIM
        print("Building a replacement graph. The previous graph stays active until success.")
    else:
        project.status = "indexing"
    save_project(project)
    try:
        arguments = [str(root), "--db", str(target)]
        if force:
            arguments.append("--force")
        if quiet:
            arguments.append("--quiet")
        # Also lock the exact database path for legacy external --db usage.
        with locked(Path(str(target) + ".command.lock")):
            report = index_command.main(arguments, _managed=True)
            with GraphStore(target, read_only=True) as store:
                meta = store.index_metadata()
                counts = store.stats()
            if (meta["model"], meta["dimensions"]) != (
                config.EMBEDDING_MODEL,
                config.EMBEDDING_DIM,
            ):
                raise StateError("The completed graph has unexpected embedding settings.")
    except BaseException:
        if not rebuild:
            project.status = "failed"
        save_project(project)
        raise
    if rebuild:
        project.previous_databases.append(project.active_database)
        project.active_database = database_reference(root, target)
        project.pending_database = project.pending_model = project.pending_dimensions = None
    project.status = "ready"
    project.embedding_model, project.embedding_dimensions = meta["model"], meta["dimensions"]
    project.statistics = counts
    project.last_indexed_at = now()
    if report.summaries_written:
        project.last_summary_model = config.CHAT_MODEL
    save_project(project)
    return report


def run(project: Project, settings: Settings, args):
    root = Path(project.root)
    if not root.is_dir():
        raise StateError(f"Project folder is unavailable: {root}")
    with locked(root / ".codelens" / "project.lock"):
        project = load_project(root)  # Re-read after locking, before touching the graph.
        meta = inspect_database(project.database)
        if meta and meta["root"] and not same_path(meta["root"], root):
            raise StateError(f"This graph belongs to {meta['root']}, not {root}.")
        if args.command != "embed" and meta is None:
            raise StateError(
                "This project has no graph yet. Run codelens embed --project " + project.id
            )
        profile, rebuild = select_profile(meta, settings, args.on_model_change)
        no_update = getattr(args, "no_update", False)
        if no_update and rebuild:
            raise StateError("--no-update cannot be combined with a model rebuild.")
        if no_update and project.status in ("failed", "indexing"):
            raise StateError(
                "The last update did not complete. Omit --no-update to recover it first."
            )
        with engine_settings(settings, profile=profile):
            if args.command == "embed":
                index_project(project, rebuild=rebuild, force=args.force, quiet=args.quiet)
            else:
                if not no_update:
                    print("Checking the source files and updating the graph before retrieval ...")
                    index_project(project, rebuild=rebuild, force=False, quiet=False)
                else:
                    print("Using the stored graph; source changes are not checked (--no-update).")
                arguments = [
                    args.question,
                    "--db",
                    str(project.database),
                    "--top-k",
                    str(args.top_k),
                    "--hops",
                    str(args.hops),
                    "--doc-top-k",
                    str(args.doc_top_k),
                ]
                retrieval_only = args.command == "retrieve" or args.no_answer
                if retrieval_only:
                    arguments.append("--no-answer")
                if args.show_context or args.command == "retrieve":
                    arguments.append("--show-context")
                print("Retrieving code and documentation with graph expansion ...")
                with locked(Path(str(project.database) + ".command.lock")):
                    ask_command.main(arguments, _managed=True)
                if not retrieval_only:
                    project.last_reasoning_model = settings.reasoning_model
            project.last_used_at = now()
            save_project(project)
            remember(project)
