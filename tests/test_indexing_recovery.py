"""Local Ladybug recovery tests; deterministic model doubles, no services."""
from contextlib import contextmanager
from pathlib import Path
import pytest
from graph_rag import config
from graph_rag.change_detection import build_merkle_tree, collect_file_hashes
from graph_rag.models import FolderSummary
from graph_rag.pipeline import indexer
from graph_rag.storage import GraphStore

class FakeEmbedder:

    def _vectors(self, items):
        return {item.id: [1.0] + [0.0] * (config.EMBEDDING_DIM - 1) for item in items}
    embed_entities = _vectors
    embed_docs = _vectors
    embed_summaries = _vectors

class FakeSummarizer:

    def summarise(self, path, entities, folder_hash):
        real = [entity for entity in entities if entity.kind != 'module']
        if len(real) < config.MIN_ENTITIES_FOR_SUMMARY:
            return None
        return FolderSummary(id=f'summary::{path}', path=path, text=', '.join(sorted((entity.name for entity in real))), content_hash=folder_hash, entity_count=len(real))

@pytest.fixture
def database(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'EMBEDDING_DIM', 8)
    with GraphStore(tmp_path / '.codelens' / 'graph.lbug') as store:
        store.verify_connection()
        store.ensure_schema()

        @contextmanager
        def borrowed():
            yield store
        monkeypatch.setattr(GraphStore, 'from_config', classmethod(lambda cls, **kw: borrowed()))
        monkeypatch.setattr(indexer, 'Embedder', FakeEmbedder)
        monkeypatch.setattr(indexer, 'Summarizer', FakeSummarizer)
        monkeypatch.setattr(config, 'GENERATE_SUMMARIES', True)
        yield store

@pytest.fixture
def repo(tmp_path):
    (tmp_path / 'src').mkdir()
    (tmp_path / 'src/a.py').write_text('def alpha():\n    return 1\n\ndef beta():\n    return 2\n\ndef gamma():\n    return 3\n', encoding='utf-8')
    (tmp_path / 'src/b.py').write_text('def delta():\n    return 4\n\ndef epsilon():\n    return 5\n\ndef zeta():\n    return 6\n', encoding='utf-8')
    (tmp_path / 'README.md').write_text('# Demo\nUses `alpha` and `delta`.\n', encoding='utf-8')
    return tmp_path

def edit(path: Path):
    path.write_text(path.read_text(encoding='utf-8').replace('return 1', 'return 10'), encoding='utf-8')

def summary_text(database):
    with database._session() as session:
        row = session.run("MATCH (s:Summary {path: 'src'}) RETURN s.text AS text").single()
        return row['text'] if row else ''

def test_unchanged_repository_is_still_a_noop(database, repo):
    indexer.run_indexing(repo, verbose=False)
    report = indexer.run_indexing(repo, verbose=False)
    assert report.files_changed == report.blocks_embedded == report.summaries_written == 0
