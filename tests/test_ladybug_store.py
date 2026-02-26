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

def test_document_links_in_both_directions(store):
    a, b = (entity('checkout'), entity('charge'))
    d, child = (doc(), doc('Details'))
    store.save_entities([a, b])
    store.save_docs([d, child])
    store.save_doc_relationships([edge(d, a, 'MENTIONS'), edge(d, b, 'MENTIONS'), edge(d, child, 'CONTAINS')])
    assert {x.id for x, _ in store.entities_mentioned_by([d.id], 10)} == {a.id, b.id}
    assert store.docs_mentioning([a.id], 10)[0][0].id == d.id
    store.clear_doc_outgoing_edges('README.md')
    assert store.docs_mentioning([a.id], 10) == []

def test_resolver_total_and_reference_roundtrip(store, monkeypatch):
    monkeypatch.setattr(config, 'MAX_NAME_CANDIDATES', 1)
    a = entity('login', unresolved_calls=['later'], external_calls=['external'], call_owner_hints={'later': 'Auth'}, call_arity_hints={'later': 2}, param_count=2)
    b = entity('login', 'b.py')
    store.save_entities([a, b])
    matches = store.resolve_names(['login'])['login']
    assert matches['total'] == 2 and len(matches['candidates']) == 1
    pending = store.get_reference_state([])
    assert pending[0]['callOwnerHints'] == ['later>Auth']
    assert pending[0]['callArityHints'] == ['later>2']
    store.save_reference_state([{'id': a.id, 'unresolvedCalls': [], 'unresolvedTypes': [], 'unresolvedExtends': [], 'externalCalls': ['external'], 'externalTypes': []}])
    assert store.get_reference_state([]) == []
    assert store.get_reference_state(['external'])[0]['id'] == a.id
    assert store.get_external_references(['external'])[0]['id'] == a.id

def test_missing_owner_and_imports(store):
    owner = entity('Auth', kind='class')
    member = entity('login', 'b.py', kind='method', owner_name='Auth')
    store.save_entities([owner, member])
    assert store.get_unowned_members()[0]['id'] == member.id
    store.save_relationships([edge(owner, member, 'CONTAINS'), edge(owner, member, 'USES_TYPE')])
    assert store.get_unowned_members() == []
    store.save_file_imports('a.py', ['b.py'])
    assert store.get_import_map() == {'a.py': {'b.py'}}
    store.clear_file_imports('a.py')
    assert store.get_import_map() == {}

def test_transactions_roll_back(store):

    def fail(tx):
        tx.run("CREATE (:Folder {path:'rolled-back'})")
        raise RuntimeError('test rollback')
    with pytest.raises(RuntimeError, match='test rollback'):
        with store._session() as session:
            session.execute_write(fail)
    assert 'rolled-back' not in store.get_folder_hashes()
