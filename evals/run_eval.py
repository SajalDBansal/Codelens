"""
evals/run_eval.py
=================

Measure whether retrieval actually finds the right code.

WHY THIS EXISTS
---------------
Retrieval failures do not raise. They degrade quietly: the run summary stays
clean, every unit test passes, and the model answers fluently from a context
that simply does not contain the answer. That is the single most dangerous
state this system can be in, because nothing downstream can detect it.

This harness is what caught the worst bug in the project. Reciprocal Rank
Fusion was discarding any hit found by only ONE of the two searches, however
confident that search was. Asked "how do I format a price for display?", the
vector search ranked the correct function FIRST at similarity 0.767 - and
fusion placed it sixth, one slot past `top_k`, so it never reached the model.
Twelve hand-written questions with known answers found that in one run.

    before the fix:  seed 67%   overall  92%
    after the fix:   seed 92%   overall 100%

HOW TO USE IT
-------------
    graph-rag-index /path/to/repo          # the graph must exist first
    python evals/run_eval.py --questions evals/questions.demo.json

The question file is a list of {"question", "expect"} objects, where `expect`
is the id of an entity that MUST be retrieved for the question to be
answerable. Ids look like "src/auth/user_service.py::AuthService.login" - the
`graph-rag-ask --no-answer` output prints them.

Write one for your own repository. A dozen questions whose answers you already
know is enough to notice a regression, and it is the only instrument that
sees this class of failure at all.

EXIT CODE
---------
Non-zero if recall falls below `--min-recall`, so this can gate CI.
"""

import argparse
import json
import sys
from pathlib import Path

from graph_rag import config
from graph_rag.cli.projects import inspect_database, load_project, resolve_project
from graph_rag.cli.settings import engine_settings, require_settings
from graph_rag.cli.state import StateError, config_directory, locked
from graph_rag.cli.workflow import select_profile
from graph_rag.retrieval import retrieve
from graph_rag.storage import GraphStore


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Measure retrieval recall against a fixed question set."
    )
    parser.add_argument(
        "--questions",
        default="evals/questions.demo.json",
        help="JSON file of {question, expect} objects",
    )
    parser.add_argument(
        "--min-recall",
        type=float,
        default=0.9,
        help="Fail (exit 1) if OVERALL recall drops below this. Default 0.9",
    )
    parser.add_argument("--config-dir", type=Path)
    parser.add_argument("--project", help="Registered project to evaluate")
    parser.add_argument("--on-model-change", choices=["keep", "error"], default="error")
    parser.add_argument("--top-k", type=int)
    parser.add_argument("--hops", type=int, choices=[1, 2])
    parser.add_argument("--db", help="Path to the local LadybugDB file")
    return parser


def main(argv=None) -> None:
    args = build_parser().parse_args(argv)
    try:
        with config_directory(args.config_dir):
            settings = require_settings()
            project = resolve_project(args.project, db=args.db)
            with locked(Path(project.root) / ".codelens" / "project.lock"):
                project = load_project(Path(project.root))
                if project.status in ("failed", "indexing"):
                    raise StateError(
                        "Finish the graph update with codelens embed before evaluation."
                    )
                meta = inspect_database(project.database)
                if meta is None:
                    raise StateError("The graph does not exist. Run codelens embed first.")
                profile, _ = select_profile(meta, settings, args.on_model_change)
                args.db = str(project.database)
                with engine_settings(settings, profile=profile):
                    args.top_k = config.DEFAULT_TOP_K if args.top_k is None else args.top_k
                    args.hops = config.DEFAULT_HOPS if args.hops is None else args.hops
                    evaluate(args)
    except StateError as error:
        raise SystemExit(str(error)) from None


def evaluate(args) -> None:

    path = Path(args.questions)
    if not path.is_file():
        raise SystemExit(f"No such question file: {path}")

    cases = json.loads(path.read_text(encoding="utf-8"))
    if not cases:
        raise SystemExit(f"{path} contains no questions")

    hit_seed = 0
    hit_any = 0
    misses: list[str] = []

    print(f"Retrieval eval - {len(cases)} questions from {path}\n")

    with GraphStore(args.db, read_only=True) as store:
        counts = store.stats()
        if not counts.get("entities"):
            raise SystemExit(
                "The graph is empty - index a repository first:\n"
                "  graph-rag-index /path/to/your/repo"
            )

        for case in cases:
            question = case["question"]
            expected = case["expect"]

            result = retrieve(question, store, top_k=args.top_k, hops=args.hops)
            seeds = [item.entity.id for item in result.items if item.hop == 0]
            everything = [item.entity.id for item in result.items]

            in_seeds = expected in seeds
            in_any = expected in everything
            hit_seed += in_seeds
            hit_any += in_any

            # SEED means the search found it directly; via-graph means only
            # the traversal rescued it. Both are successes, but the split is
            # what tells you WHICH half of the system is doing the work - and
            # a question drifting from SEED to via-graph is an early warning
            # that ranking is degrading.
            if in_seeds:
                status = "SEED"
            elif in_any:
                status = "via-graph"
            else:
                status = "MISS"
                misses.append(question)

            rank = everything.index(expected) + 1 if in_any else None
            print(f"  {status:10} rank={rank!s:5} of {len(everything):3}  {question}")

    total = len(cases)
    seed_recall = hit_seed / total
    overall_recall = hit_any / total

    print()
    print(
        f"  Recall@seed  {hit_seed}/{total} = {seed_recall:.0%}   (vector + keyword search alone)"
    )
    print(f"  Recall@all   {hit_any}/{total} = {overall_recall:.0%}   (+ graph expansion)")

    if misses:
        print("\n  Not retrieved at all:")
        for question in misses:
            print(f"    - {question}")

    if overall_recall < args.min_recall:
        raise SystemExit(
            f"\nFAIL: overall recall {overall_recall:.0%} is below the "
            f"{args.min_recall:.0%} threshold."
        )

    print("\nPASS")
    sys.exit(0)


if __name__ == "__main__":
    main()
