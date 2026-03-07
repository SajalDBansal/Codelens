"""Real local graphs with deterministic model doubles: no keys, billing, or servers."""
import subprocess
import sys
from types import SimpleNamespace
import pytest
from graph_rag import config
from graph_rag.cli import ask_command, index_command, projects, settings, state
from graph_rag.embedding import embedder
from graph_rag.generation.model_options import sampling_options
from graph_rag.pipeline import indexer
from graph_rag.retrieval import retriever
from graph_rag.storage import GraphStore

@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, 'OPENAI_API_KEY', 'test-placeholder-not-a-real-key')
    monkeypatch.setattr(config, 'LADYBUG_BUFFER_MB', 64)
    monkeypatch.setattr(config, 'LADYBUG_THREADS', 1)
    monkeypatch.setattr(config, 'GENERATE_SUMMARIES', False)
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: False)
    monkeypatch.chdir(tmp_path)
    keys = {}
    monkeypatch.setattr(settings.keyring, 'set_password', lambda service, account, key: keys.update({(service, account): key}))
    monkeypatch.setattr(settings.keyring, 'get_password', lambda service, account: keys.get((service, account)))
    with state.config_directory(tmp_path / 'user-data'):
        yield keys

@pytest.fixture
def configured():
    value = settings.Settings(embedding_dimensions=8, engine={'generate_summaries': False, 'ladybug_buffer_mb': 64, 'ladybug_threads': 1})
    settings.store_key('test-placeholder-not-a-real-key')
    settings.save_settings(value)
    return value

@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'checkout'
    root.mkdir()
    (root / 'checkout.py').write_text('def subtotal(price, quantity):\n    return price * quantity\n\ndef checkout(price, quantity):\n    """Return the cart total."""\n    return subtotal(price, quantity)\n', encoding='utf-8')
    (root / 'README.md').write_text('# Checkout\nThe `checkout` function uses `subtotal`.\n', encoding='utf-8')
    return root

@pytest.fixture
def models(monkeypatch):
    calls = []

    class FakeEmbedder:

        def vectors(self, items):
            if items:
                calls.append(('index', config.EMBEDDING_MODEL, config.EMBEDDING_DIM))
            return {item.id: [1.0] + [0.0] * (config.EMBEDDING_DIM - 1) for item in items}
        embed_entities = vectors
        embed_docs = vectors
        embed_summaries = vectors

        def embed_query(self, question):
            calls.append(('query', config.EMBEDDING_MODEL, config.EMBEDDING_DIM))
            return [1.0] + [0.0] * (config.EMBEDDING_DIM - 1)

    def answer(result):
        calls.append(('answer', config.CHAT_MODEL))
        assert 'checkout.py' in result.context_text
        return 'checkout calls subtotal [1].'
    monkeypatch.setattr(indexer, 'Embedder', FakeEmbedder)
    monkeypatch.setattr(indexer, 'Summarizer', lambda: SimpleNamespace(summarise=lambda *args: None))
    monkeypatch.setattr(retriever, 'Embedder', FakeEmbedder)
    monkeypatch.setattr(ask_command, 'answer_question', answer)
    return calls

def embed(repo):
    app.main(['embed', str(repo), '--quiet'])
    return projects.load_project(repo)

def test_keyring_failure_never_falls_back_to_plaintext(monkeypatch):

    def fail(*args):
        raise RuntimeError('no credential backend')
    monkeypatch.setattr(settings.keyring, 'set_password', fail)
    with pytest.raises(state.StateError, match='No key was written to JSON'):
        settings.store_key('private-test-credential')
    assert not (state.home() / 'config.json').exists()

def test_atomic_write_preserves_previous_document(configured, monkeypatch):
    path = state.home() / 'config.json'
    before = path.read_bytes()

    def fail(*args):
        raise OSError('simulated replace failure')
    monkeypatch.setattr(state.os, 'replace', fail)
    with pytest.raises(OSError):
        settings.save_settings(settings.Settings())
    assert path.read_bytes() == before
    assert not list(state.home().glob('*.tmp'))

def test_readonly_inspection_does_not_create_database(tmp_path):
    missing = tmp_path / 'missing' / 'graph.lbug'
    with pytest.raises(FileNotFoundError):
        GraphStore(missing, read_only=True)
    assert not missing.parent.exists()

def test_registry_lock_prevents_overwrite(repo):
    first = projects.add_project(repo)
    with state.locked(state.home() / '.state.lock'), pytest.raises(state.StateError, match='Another command'):
        projects.remember(first, active=True)
    assert projects.registry().projects[0].id == first.id

def test_scoped_settings_restore_after_exception(configured):
    original = (config.EMBEDDING_MODEL, config.EMBEDDING_DIM, config.CHAT_MODEL)
    with pytest.raises(RuntimeError), settings.engine_settings(configured, profile=('text-embedding-3-large', 4)):
        assert config.EMBEDDING_DIM == 4
        raise RuntimeError('simulated failure')
    assert (config.EMBEDDING_MODEL, config.EMBEDDING_DIM, config.CHAT_MODEL) == original
