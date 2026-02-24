"""Real embedded-database contracts. No network or model credentials."""
import pytest
from graph_rag import config
from graph_rag.models import CodeEntity, DocChunk, FolderSummary, Relationship
from graph_rag.storage import GraphStore

@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(config, 'EMBEDDING_DIM', 3)
    monkeypatch.setattr(config, 'EMBEDDING_MODEL', 'test-model')
    monkeypatch.setattr(config, 'LADYBUG_BUFFER_MB', 64)

@pytest.fixture
def store(tmp_path, settings):
    with GraphStore(tmp_path / 'graph.lbug', repo_path=tmp_path) as db:
        db.ensure_schema()
        db.save_folders([{'path': '', 'hash': 'root', 'parent': None}])
        db.save_files([{'path': p, 'hash': 'h', 'parent': '', 'language': lang} for p, lang in [('a.py', 'python'), ('b.py', 'python'), ('client.ts', 'typescript'), ('README.md', None)]])
        yield db

def entity(name, path='a.py', kind='function', **extra):
    return CodeEntity(id=f'{path}::{name}', name=name, kind=kind, file_path=path, language='typescript' if path.endswith('.ts') else 'python', start_line=1, end_line=2, code=f'def {name}(): pass', content_hash=name, **extra)

def doc(name='Setup'):
    return DocChunk(id=f'README.md::{name}', file_path='README.md', doc_type='markdown', title=name, heading_path=name, level=1, start_line=1, end_line=3, text=f'{name}: use `checkout` to charge the cart.', content_hash=name)

def edge(a, b, kind='CALLS', confidence=0.8):
    return Relationship(source_id=a.id, target_id=b.id, kind=kind, confidence=confidence, resolved_by='test')

def test_transactions_roll_back(store):

    def fail(tx):
        tx.run("CREATE (:Folder {path:'rolled-back'})")
        raise RuntimeError('test rollback')
    with pytest.raises(RuntimeError, match='test rollback'):
        with store._session() as session:
            session.execute_write(fail)
    assert 'rolled-back' not in store.get_folder_hashes()
