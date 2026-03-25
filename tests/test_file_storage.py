"""Source identity, crash recovery, corruption and compaction contracts."""

import hashlib
import json

import pytest

from graph_rag import config
from graph_rag.models import CodeEntity, DocChunk
from graph_rag.storage import GraphStore


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_DIM", 3)
    with GraphStore(tmp_path / "graph.sqlite", repo_path=tmp_path) as db:
        db.ensure_schema()
        yield db


def entity(text, name="hello", start=1, end=2):
    return CodeEntity(
        id=f"src.py::{name}",
        kind="function",
        name=name,
        file_path="src.py",
        language="python",
        start_line=start,
        end_line=end,
        code=text,
        content_hash=hashlib.sha256(text.encode()).hexdigest(),
    )


@pytest.mark.parametrize("mutation", ["edit", "delete"])
def test_source_references_preserve_indexed_version(store, tmp_path, mutation):
    raw = '# café\r\ndef hello():\r\n    return "😀"\r\n'.encode()
    (tmp_path / "src.py").write_bytes(raw)
    text = 'def hello():\r\n    return "😀"'
    value = entity(text, start=2, end=3)
    store.save_source("src.py", raw)
    store.save_entities([value])
    store.save_embeddings({value.id: [1, 0, 0]})
    row = store._nodes("CodeEntity")[0]
    assert row["source_hash"] == hashlib.sha256(raw).hexdigest()
    assert row["text_hash"] is None
    assert "code" not in json.loads(row["data"])
    if mutation == "edit":
        (tmp_path / "src.py").write_text("# changed\n", encoding="utf-8")
    else:
        (tmp_path / "src.py").unlink()
    with GraphStore(store.path, read_only=True) as reopened:
        assert reopened.vector_search([1, 0, 0], 1)[0][0].code == text
        assert reopened.check_integrity()["sources"] == "ok"


def test_synthetic_code_and_midline_document_chunks(store):
    store.save_source("src.py", b"def hello(): pass\n")
    synthetic = entity("Module src.py contains hello", "module")
    store.save_entities([synthetic])
    store.save_source("README.md", "alpha βeta gamma".encode())
    chunk = DocChunk(
        id="README.md::part",
        file_path="README.md",
        doc_type="text",
        title="part",
        heading_path="part",
        level=0,
        start_line=1,
        end_line=1,
        text="βeta",
    )
    store.save_docs([chunk])
    code, doc = store._nodes("CodeEntity")[0], store._nodes("Doc")[0]
    assert code["text_hash"] and code["source_hash"] is None
    assert doc["source_hash"] and doc["start_char"] == 6
    assert store._object(doc).text == "βeta"
    assert store._object(code).code == synthetic.code


def test_interrupted_vector_publish_keeps_old_vectors_and_compacts_orphans(store, monkeypatch):
    value = entity("def hello(): pass")
    store.save_entities([value])
    store.save_embeddings({value.id: [1, 0, 0]})
    original = store._vectors.save

    def fail_after_file(*args):
        original(*args)
        raise RuntimeError("simulated interruption after durable segment")

    with monkeypatch.context() as patch:
        patch.setattr(store._vectors, "save", fail_after_file)
        with pytest.raises(RuntimeError, match="interruption"):
            store.save_embeddings({value.id: [0, 1, 0]})
    assert store.vector_search([1, 0, 0], 1)[0][1] == 1
    assert len(list(store._vectors.directory.glob("*.npy"))) == 2
    assert store.compact()["removed_segments"] == 1
    assert store.check_integrity()["vectors_checked"] == 1


def test_vector_corruption_is_detected_on_reopen(store):
    value = entity("def hello(): pass")
    store.save_entities([value])
    store.save_embeddings({value.id: [1, 0, 0]})
    segment = next(store._vectors.directory.glob("*.npy"))
    content = bytearray(segment.read_bytes())
    content[-1] ^= 1
    segment.write_bytes(content)
    with (
        GraphStore(store.path, read_only=True) as reopened,
        pytest.raises(ValueError, match="checksum"),
    ):
        reopened.vector_search([1, 0, 0], 1)


def test_compaction_reclaims_deleted_source_and_vectors(store):
    values = [entity("def hello(): pass"), entity("def goodbye(): pass", "goodbye")]
    store.save_entities(values)
    store.save_embeddings({values[0].id: [1, 0, 0], values[1].id: [0, 1, 0]})
    store.delete_entities([values[0].id])
    before = store.vector_search([0, 1, 0], 2)
    store.compact()
    assert store.vector_search([0, 1, 0], 2) == before
    assert len(store.query("SELECT * FROM blobs")) == 1
    assert store.check_integrity()["vectors_checked"] == 1


def test_source_corruption_is_not_silently_served(store):
    value = entity("def hello(): pass")
    store.save_entities([value])
    store.query("UPDATE blobs SET content=x'00'")
    with pytest.raises(ValueError, match="corrupt"):
        store.fulltext_search("hello", 1)


def test_non_sqlite_file_is_never_overwritten(tmp_path):
    path = tmp_path / "invalid.sqlite"
    path.write_bytes(b"unrecognized format sentinel")
    with pytest.raises(ValueError, match="not a SQLite index"):
        GraphStore(path)
    assert path.read_bytes() == b"unrecognized format sentinel"


def test_keyword_cache_tracks_other_connection_updates_and_deletions(store):
    value = entity("def hello(): return 'invoice'")
    store.save_entities([value])
    assert store.fulltext_search("invoice", 1)
    with GraphStore(store.path) as writer:
        writer.save_entities([entity("def hello(): return 'checkout'")])
        assert not store.fulltext_search("invoice", 1)
        assert store.fulltext_search("checkout", 1)
        writer.delete_entities([value.id])
        assert not store.fulltext_search("checkout", 1)
    assert store.query("SELECT count(*) AS n FROM keyword_terms") == [{"n": 0}]


def test_invalid_index_is_rejected_before_project_registration(tmp_path, monkeypatch):
    from graph_rag.cli import projects
    from graph_rag.cli.state import config_directory

    directory = tmp_path / ".codelens"
    directory.mkdir()
    invalid = directory / "graph.sqlite"
    invalid.write_bytes(b"invalid index sentinel")
    monkeypatch.chdir(tmp_path)
    with config_directory(tmp_path / "settings"):
        with pytest.raises(ValueError, match="not a SQLite index"):
            projects.add_project(tmp_path)
        with pytest.raises(ValueError, match="not a SQLite index"):
            projects.resolve_project()
    assert invalid.read_bytes() == b"invalid index sentinel"
    assert not (directory / "project.json").exists()
