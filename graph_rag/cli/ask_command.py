"""
cli/ask_command.py
==================

ENTRY POINT 2 of 2:  `graph-rag-ask`

Asks a question about the indexed codebase. Retrieves the relevant code from
the graph, sends it to the LLM, and prints a grounded, cited answer.

USAGE
-----
    graph-rag-ask "how does the merkle hashing work?"
    graph-rag-ask "where is the embedding model called?" --show-context
    graph-rag-ask "what calls save_entities?" --top-k 8 --hops 2
    graph-rag-ask "what does the README say about setup?" --doc-top-k 5
    graph-rag-ask "where is the retry logic?" --doc-top-k 0     (code only)

You can also run it without installing the package:

    python -m graph_rag.cli.ask_command "your question"
"""
import argparse
import sys
from .. import config
from ._console import configure_console
from ..generation import answer_question
from ..retrieval import retrieve
from ..storage import GraphStore

def build_parser() -> argparse.ArgumentParser:
    """Describe the command line options."""
    parser = argparse.ArgumentParser(prog='graph-rag-ask', description='Ask a question about the indexed codebase.')
    parser.add_argument('question', help='Your question, in quotes. e.g. "how does login work?"')
    parser.add_argument('--top-k', type=int, default=config.DEFAULT_TOP_K, help=f'How many code blocks the vector search returns as starting points (default: {config.DEFAULT_TOP_K}). Raise it for broad questions, lower it for pinpoint ones.')
    parser.add_argument('--hops', type=int, default=config.DEFAULT_HOPS, choices=[1, 2], help=f'How far to walk out in the graph (default: {config.DEFAULT_HOPS}). 1 = direct callers and callees. 2 = their neighbours too. Capped at 2 because beyond that the results become noise.')
    parser.add_argument('--doc-top-k', type=int, default=config.DEFAULT_DOC_TOP_K, help=f'How many documentation sections (.md, .yaml, ...) to retrieve alongside the code (default: {config.DEFAULT_DOC_TOP_K}). Set to 0 to search code only.')
    parser.add_argument('--show-context', action='store_true', help='Print the exact code that was sent to the LLM, before the answer')
    parser.add_argument('--no-answer', action='store_true', help='Retrieve and show the code but do NOT call the LLM. Useful for checking retrieval quality without paying for a completion.')
    parser.add_argument('--db', help='Local LadybugDB file (default: .codelens/graph.lbug in the repository)')
    return parser

def main(argv=None, *, _managed=False):
    """The function that runs when you type `graph-rag-ask`."""
    configure_console()
    args = build_parser().parse_args(argv)
    config.validate()
    with GraphStore.from_config(db_path=args.db) as store:
        store.verify_connection()
        store.ensure_schema()
        result = retrieve(question=args.question, store=store, top_k=args.top_k, hops=args.hops, doc_top_k=args.doc_top_k)
        if not result.items and (not result.docs) and (not result.summaries):
            counts = store.stats()
            if not counts.get('entities') and (not counts.get('doc chunks')):
                raise SystemExit('The graph is empty - nothing has been indexed yet.\nRun:  graph-rag-index /path/to/your/repo')
            raise SystemExit(f"Nothing in this codebase looked relevant to that question.\nThe graph holds {counts['entities']} code blocks and {counts['doc chunks']} document sections; none of them scored above MIN_SIMILARITY ({config.MIN_SIMILARITY}).\nRephrase using names from the code, or adjust min_similarity with codelens config set --option min_similarity=VALUE.")
        direct = [i for i in result.items if i.hop == 0] + [d for d in result.docs if d.hop == 0]
        linked = [i for i in result.items if i.hop > 0] + [d for d in result.docs if d.hop > 0]
        print(f'Retrieved {len(direct)} direct matches + {len(linked)} connected via graph edges' + (f' ({result.dropped_count} dropped to fit the budget)' if result.dropped_count else ''))
        for summary in result.summaries:
            path = summary.summary.path or '(repository root)'
            print(f'  * OVER {path}/  (folder overview, similarity {summary.score:.3f})')
        for item in result.items:
            marker = '*' if item.hop == 0 else ' '
            entity = item.entity
            print(f'  {marker} CODE {entity.file_path}:{entity.start_line} {entity.kind} {entity.name}  ({item.reason})')
        for doc in result.docs:
            marker = '*' if doc.hop == 0 else ' '
            chunk = doc.chunk
            print(f'  {marker} DOCS {chunk.file_path}:{chunk.start_line} {chunk.heading_path}  ({doc.reason})')
        print('')
        if args.show_context:
            print('-' * 60)
            print(result.context_text)
            print('-' * 60)
            print('')
        if args.no_answer:
            return
        print(f'Asking {config.CHAT_MODEL} ...\n')
        answer = answer_question(result)
        print('=' * 60)
        print(answer)
        print('=' * 60)
if __name__ == '__main__':
    main()
