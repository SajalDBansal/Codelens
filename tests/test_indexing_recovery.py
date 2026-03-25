"""Local SQLite recovery tests; deterministic model doubles, no services."""

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
        real = [entity for entity in entities if entity.kind != "module"]
        if len(real) < config.MIN_ENTITIES_FOR_SUMMARY:
            return None
        return FolderSummary(
            id=f"summary::{path}",
            path=path,
            text=", ".join(sorted(entity.name for entity in real)),
            content_hash=folder_hash,
            entity_count=len(real),
        )


@pytest.fixture
def database(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "EMBEDDING_DIM", 8)
    with GraphStore(tmp_path / ".codelens" / "graph.sqlite") as store:
        store.verify_connection()
        store.ensure_schema()

        @contextmanager
        def borrowed():
            yield store

        monkeypatch.setattr(GraphStore, "from_config", classmethod(lambda cls, **kw: borrowed()))
        monkeypatch.setattr(indexer, "Embedder", FakeEmbedder)
        monkeypatch.setattr(indexer, "Summarizer", FakeSummarizer)
        monkeypatch.setattr(config, "GENERATE_SUMMARIES", True)
        yield store


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src/a.py").write_text(
        "def alpha():\n    return 1\n\ndef beta():\n    return 2\n\ndef gamma():\n    return 3\n",
        encoding="utf-8",
    )
    (tmp_path / "src/b.py").write_text(
        "def delta():\n    return 4\n\ndef epsilon():\n    return 5\n\ndef zeta():\n    return 6\n",
        encoding="utf-8",
    )
    (tmp_path / "README.md").write_text("# Demo\nUses `alpha` and `delta`.\n", encoding="utf-8")
    return tmp_path


def edit(path: Path):
    path.write_text(
        path.read_text(encoding="utf-8").replace("return 1", "return 10"), encoding="utf-8"
    )


@pytest.mark.parametrize("kind", ["code", "docs"])
def test_failed_embedding_cannot_reuse_the_previous_vector(database, repo, monkeypatch, kind):
    indexer.run_indexing(repo, verbose=False)
    if kind == "code":
        edit(repo / "src/a.py")
        method = "embed_entities"
    else:
        (repo / "README.md").write_text(
            "# Demo\nChanged documentation for `alpha`.\n", encoding="utf-8"
        )
        method = "embed_docs"

    def fail(self, items):
        raise RuntimeError("simulated embedding outage")

    with monkeypatch.context() as patch:
        patch.setattr(FakeEmbedder, method, fail)
        with pytest.raises(RuntimeError, match="simulated embedding outage"):
            indexer.run_indexing(repo, verbose=False)
    retry = indexer.run_indexing(repo, verbose=False)
    count = retry.blocks_embedded if kind == "code" else retry.doc_chunks_embedded
    assert count > 0, "a changed block must get a replacement vector on retry"


@pytest.mark.parametrize("force", [False, True])
def test_failed_parse_is_visible_and_retried(database, repo, monkeypatch, force):
    if force:
        indexer.run_indexing(repo, verbose=False)
    original = indexer.parse_file

    def fail(path, relative):
        if relative == "src/a.py":
            raise ValueError("simulated parser failure")
        return original(path, relative)

    with monkeypatch.context() as patch:
        patch.setattr(indexer, "parse_file", fail)
        report = indexer.run_indexing(repo, force=force, verbose=False)
    hashes = collect_file_hashes(build_merkle_tree(repo))
    assert database.get_file_hashes().get("src/a.py") != hashes["src/a.py"]
    assert "src/a.py" in report.failed_files
    assert indexer.run_indexing(repo, verbose=False).files_changed == 1
    assert database.get_file_hashes()["src/a.py"] == hashes["src/a.py"]


def summary_text(database):
    rows = database.query("SELECT * FROM nodes WHERE label='Summary' AND path='src'")
    return database._object(rows[0]).text if rows else ""


def test_incremental_summary_includes_unchanged_files(database, repo):
    indexer.run_indexing(repo, verbose=False)
    edit(repo / "src/a.py")
    report = indexer.run_indexing(repo, verbose=False)
    assert report.summaries_written == 1
    assert "alpha" in summary_text(database)
    assert "delta" in summary_text(database)
    assert report.blocks_reused > 0


def test_deletion_only_update_rebuilds_summary(database, repo):
    indexer.run_indexing(repo, verbose=False)
    (repo / "src/a.py").unlink()
    indexer.run_indexing(repo, verbose=False)
    assert "alpha" not in summary_text(database)
    assert "delta" in summary_text(database)


def test_summary_embedding_failure_is_retried(database, repo, monkeypatch):
    indexer.run_indexing(repo, verbose=False)
    edit(repo / "src/a.py")

    def fail(self, summaries):
        raise RuntimeError("simulated summary embedding outage")

    with monkeypatch.context() as patch:
        patch.setattr(FakeEmbedder, "embed_summaries", fail)
        with pytest.raises(RuntimeError, match="summary embedding outage"):
            indexer.run_indexing(repo, verbose=False)
    assert indexer.run_indexing(repo, verbose=False).summaries_written == 1


def test_unchanged_repository_is_still_a_noop(database, repo):
    indexer.run_indexing(repo, verbose=False)
    report = indexer.run_indexing(repo, verbose=False)
    assert report.files_changed == report.blocks_embedded == report.summaries_written == 0
