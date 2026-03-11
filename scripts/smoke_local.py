"""Exercise indexing and retrieval without network or provider credentials.

Uses deterministic test embeddings, not semantic model output. The real parser,
Ladybug storage, graph traversal, keyword search, and context builder all run.
Pass --db to retain a local test database; otherwise a temporary one is used.
"""
import argparse
from contextlib import ExitStack
import json
from pathlib import Path
import tempfile
from unittest.mock import patch

from graph_rag import config
from graph_rag.models import FolderSummary
from graph_rag.pipeline import indexer
from graph_rag.retrieval import retriever
from graph_rag.storage import GraphStore


class TestEmbedder:
    def _vectors(self, items):
        return {item.id: [1.0, 0.0, 0.0] for item in items}

    embed_entities = _vectors
    embed_docs = _vectors
    embed_summaries = _vectors

    def embed_query(self, question):
        return [1.0, 0.0, 0.0]


class TestSummarizer:
    def summarise(self, path, entities, folder_hash):
        return FolderSummary(id=f'summary::{path}', path=path,
                             text='Checkout uses subtotal, discount, and tax.',
                             content_hash=folder_hash, entity_count=len(entities))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--db', type=Path)
    args = parser.parse_args()
    repo = Path(__file__).resolve().parents[1] / 'examples' / 'smoke_repo'
    with ExitStack() as stack:
        target = args.db
        if target is None:
            target = Path(stack.enter_context(tempfile.TemporaryDirectory())) / 'graph.lbug'
        stack.enter_context(patch.object(config, 'EMBEDDING_MODEL', 'deterministic-smoke-only'))
        stack.enter_context(patch.object(config, 'EMBEDDING_DIM', 3))
        stack.enter_context(patch.object(config, 'GENERATE_SUMMARIES', True))
        stack.enter_context(patch.object(indexer, 'Embedder', TestEmbedder))
        stack.enter_context(patch.object(indexer, 'Summarizer', TestSummarizer))
        stack.enter_context(patch.object(retriever, 'Embedder', TestEmbedder))
        first = indexer.run_indexing(repo, db_path=target)
        repeat = indexer.run_indexing(repo, db_path=target)
        assert repeat.blocks_embedded == repeat.doc_chunks_embedded == repeat.summaries_written == 0
        with GraphStore(target) as store:
            store.ensure_schema()
            counts = store.stats()
            assert counts['entities'] >= 4
            assert counts['CALLS edges'] == 3
            result = retriever.retrieve('How does checkout use apply_discount and add_tax?', store)
            identifiers = {item.entity.id for item in result.items}
            assert {'checkout.py::checkout', 'pricing.py::apply_discount', 'pricing.py::add_tax'} <= identifiers
            assert 'checkout.py' in result.context_text and 'pricing.py' in result.context_text
        print(json.dumps({'mode':'offline test doubles; no AI answer generated',
                          'database':str(target.resolve()), 'statistics':counts,
                          'initial_embeddings':first.blocks_embedded + first.doc_chunks_embedded,
                          'repeat_embeddings':0,'retrieved_ids':sorted(identifiers)}, indent=2))
        print('PASS: local parse -> embed double -> persist -> reopen -> retrieve -> source context')


if __name__ == '__main__':
    main()
