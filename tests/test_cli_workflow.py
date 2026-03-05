"""Real local graphs with deterministic model doubles: no keys, billing, or servers."""
import subprocess
import sys
from types import SimpleNamespace
import pytest
from graph_rag import config
from graph_rag.cli import state
from graph_rag.embedding import embedder
from graph_rag.generation.model_options import sampling_options
from graph_rag.pipeline import indexer
from graph_rag.retrieval import retriever
from graph_rag.storage import GraphStore

@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    with state.config_directory(tmp_path / 'user-data'):
        yield {}

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
