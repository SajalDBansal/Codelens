"""Real embedded-database contracts. No network or model credentials."""

import pytest

from graph_rag import config
from graph_rag.models import CodeEntity, DocChunk, FolderSummary, Relationship
from graph_rag.storage import GraphStore


@pytest.fixture
def settings(monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_DIM", 3)
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "test-model")


@pytest.fixture
def store(tmp_path, settings):
    with GraphStore(tmp_path / "graph.sqlite", repo_path=tmp_path) as db:
        db.ensure_schema()
        db.save_folders([{"path": "", "hash": "root", "parent": None}])
        db.save_files(
            [
                {"path": p, "hash": "h", "parent": "", "language": lang}
                for p, lang in [
                    ("a.py", "python"),
                    ("b.py", "python"),
                    ("client.ts", "typescript"),
                    ("README.md", None),
                ]
            ]
        )
        yield db


def entity(name, path="a.py", kind="function", **extra):
    return CodeEntity(
        id=f"{path}::{name}",
        name=name,
        kind=kind,
        file_path=path,
        language="typescript" if path.endswith(".ts") else "python",
        start_line=1,
        end_line=2,
        code=f"def {name}(): pass",
        content_hash=name,
        **extra,
    )


def doc(name="Setup"):
    return DocChunk(
        id=f"README.md::{name}",
        file_path="README.md",
        doc_type="markdown",
        title=name,
        heading_path=name,
        level=1,
        start_line=1,
        end_line=3,
        text=f"{name}: use `checkout` to charge the cart.",
        content_hash=name,
    )


def edge(a, b, kind="CALLS", confidence=0.8):
    return Relationship(
        source_id=a.id, target_id=b.id, kind=kind, confidence=confidence, resolved_by="test"
    )


def test_persistence_and_all_vector_searches(store):
    a, b, c = entity("checkout"), entity("orthogonal"), entity("opposite")
    d = doc()
    s = FolderSummary(
        id="summary::", path="", text="checkout overview", content_hash="root", entity_count=3
    )
    store.save_entities([a, b, c])
    store.save_embeddings({a.id: [1.0, 0.0, 0.0], b.id: [0.0, 1.0, 0.0], c.id: [-1.0, 0.0, 0.0]})
    store.save_docs([d])
    store.save_doc_embeddings({d.id: [1.0, 0.0, 0.0]})
    store.save_summaries([s])
    store.save_summary_embeddings({s.id: [1.0, 0.0, 0.0]})
    path = store.path
    store.close()
    with GraphStore(path) as reopened:
        reopened.ensure_schema()
        hits = reopened.vector_search([1.0, 0.0, 0.0], 10)
        assert [x.id for x, _ in hits] == [a.id, b.id, c.id]
        assert [score for _, score in hits] == pytest.approx([1.0, 0.5, 0.0], abs=1e-6)
        assert reopened.doc_vector_search([1.0, 0.0, 0.0], 2)[0][0].id == d.id
        assert reopened.summary_vector_search([1.0, 0.0, 0.0], 2)[0][0].id == s.id
        assert reopened.stats()["embedded"] == 3


def test_graph_expansion_directions_and_file_boundary(store):
    caller, seed, callee, second, unrelated = [
        entity(n) for n in ("caller", "seed", "callee", "second", "unrelated")
    ]
    store.save_entities([caller, seed, callee, second, unrelated])
    store.save_relationships(
        [edge(caller, seed), edge(seed, callee), edge(callee, second), edge(second, seed)]
    )
    one = {r["entity"].id: r for r in store.expand_neighbours([seed.id], 1, 20)}
    assert set(one) == {caller.id, callee.id, second.id}
    assert one[caller.id]["reason"] == "calls seed"
    assert one[callee.id]["reason"] == "called by seed"
    assert one[callee.id]["confidence"] == 0.8
    two = store.expand_neighbours([caller.id], 2, 20)
    assert {r["entity"].id for r in two} == {seed.id, callee.id, second.id}
    assert all(r["entity"].id != unrelated.id for r in two)


def test_module_cap_preserves_semantic_neighbours(store, monkeypatch):
    monkeypatch.setattr(config, "MAX_MODULE_EXPANSION", 2)
    module = entity("<module>", kind="module")
    children = [entity(f"child{i}") for i in range(6)]
    store.save_entities([module] + children)
    store.save_relationships([edge(module, c, "CONTAINS") for c in children])
    store.save_embeddings({c.id: [1.0, float(i), 0.0] for i, c in enumerate(children)})
    hits = store.expand_neighbours([module.id], 1, 20, [1.0, 0.0, 0.0])
    assert [r["entity"].id for r in hits] == [children[0].id, children[1].id]


def test_document_links_in_both_directions(store):
    a, b = entity("checkout"), entity("charge")
    d, child = doc(), doc("Details")
    store.save_entities([a, b])
    store.save_docs([d, child])
    store.save_doc_relationships(
        [edge(d, a, "MENTIONS"), edge(d, b, "MENTIONS"), edge(d, child, "CONTAINS")]
    )
    assert {x.id for x, _ in store.entities_mentioned_by([d.id], 10)} == {a.id, b.id}
    assert store.docs_mentioning([a.id], 10)[0][0].id == d.id
    store.clear_doc_outgoing_edges("README.md")
    assert store.docs_mentioning([a.id], 10) == []


def test_shared_interfaces_and_pruning(store):
    a, b, c = entity("checkout"), entity("fetchCart", "client.ts"), entity("unused")
    d = doc()
    store.save_entities([a, b, c])
    store.save_docs([d])
    refs = [
        (a.id, "route", "/cart"),
        (b.id, "route", "/cart"),
        (d.id, "route", "/cart"),
        (c.id, "route", "/lonely"),
    ]
    store.save_interface_refs(refs)
    store.save_interface_refs(refs)
    assert store.stats()["REFERENCES edges"] == 4
    assert store.prune_noisy_interfaces(12) == 1
    assert {r["entity"].id for r in store.entities_sharing_interfaces([a.id], 10)} == {b.id}
    assert {r["entity"].id for r in store.entities_sharing_interfaces([d.id], 10)} == {a.id, b.id}
    assert store.connectivity_stats()["cross_language_edges"] == 1
    store.clear_interface_refs("client.ts")
    assert store.entities_sharing_interfaces([a.id], 10) == []


def test_keyword_cache_updates_and_safe_identifier_queries(store):
    a = entity("MAX_CART_ITEMS")
    store.save_entities([a])
    assert store.fulltext_search("where is MAX_CART_ITEMS?", 5)[0][0].id == a.id
    assert store.fulltext_search("max cart items", 5)[0][0].id == a.id
    assert store.fulltext_search("the a of and", 5) == []
    assert store.fulltext_search("", 5) == []
    assert store.fulltext_search('"(){}: OR where is MAX_CART_ITEMS?', 5)
    d = doc("Installation")
    store.save_docs([d])
    assert store.doc_fulltext_search("installation", 5)[0][0].id == d.id
    store.delete_entities([a.id])
    store.delete_docs([d.id])
    assert store.fulltext_search("MAX_CART_ITEMS", 5) == []
    assert store.doc_fulltext_search("installation", 5) == []


def test_resolver_total_and_reference_roundtrip(store, monkeypatch):
    monkeypatch.setattr(config, "MAX_NAME_CANDIDATES", 1)
    a = entity(
        "login",
        unresolved_calls=["later"],
        external_calls=["external"],
        call_owner_hints={"later": "Auth"},
        call_arity_hints={"later": 2},
        param_count=2,
    )
    b = entity("login", "b.py")
    store.save_entities([a, b])
    matches = store.resolve_names(["login"])["login"]
    assert matches["total"] == 2 and len(matches["candidates"]) == 1
    pending = store.get_reference_state([])
    assert pending[0]["callOwnerHints"] == ["later>Auth"]
    assert pending[0]["callArityHints"] == ["later>2"]
    store.save_reference_state(
        [
            {
                "id": a.id,
                "unresolvedCalls": [],
                "unresolvedTypes": [],
                "unresolvedExtends": [],
                "externalCalls": ["external"],
                "externalTypes": [],
            }
        ]
    )
    assert store.get_reference_state([]) == []
    assert store.get_reference_state(["external"])[0]["id"] == a.id
    assert store.get_external_references(["external"])[0]["id"] == a.id


def test_missing_owner_and_imports(store):
    owner = entity("Auth", kind="class")
    member = entity("login", "b.py", kind="method", owner_name="Auth")
    store.save_entities([owner, member])
    assert store.get_unowned_members()[0]["id"] == member.id
    store.save_relationships([edge(owner, member, "CONTAINS"), edge(owner, member, "USES_TYPE")])
    assert store.get_unowned_members() == []
    store.save_file_imports("a.py", ["b.py"])
    assert store.get_import_map() == {"a.py": {"b.py"}}
    store.clear_file_imports("a.py")
    assert store.get_import_map() == {}


def test_invalid_vectors_do_not_replace_valid_embeddings(store):
    a = entity("alpha")
    store.save_entities([a])
    store.save_embeddings({a.id: [1.0, 0.0, 0.0]})
    for vector in ([1.0, 0.0], [0.0, 0.0, 0.0], [float("nan"), 0.0, 0.0]):
        with pytest.raises(ValueError):
            store.save_embeddings({a.id: vector})
    assert store.vector_search([1.0, 0.0, 0.0], 1)[0][1] == pytest.approx(1.0)


def test_transactions_roll_back(store):
    def fail(tx):
        tx.run("INSERT INTO nodes(label,id,path) VALUES('Folder','rolled-back','rolled-back')")
        raise RuntimeError("test rollback")

    with pytest.raises(RuntimeError, match="test rollback"), store._session() as session:
        session.execute_write(fail)
    assert "rolled-back" not in store.get_folder_hashes()


def test_repo_and_model_guards(store, monkeypatch, tmp_path):
    path = store.path
    store.close()
    with (
        GraphStore(path, repo_path=tmp_path / "different") as other,
        pytest.raises(ValueError, match="belongs to"),
    ):
        other.ensure_schema()
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "different-model")
    with GraphStore(path) as other, pytest.raises(ValueError, match="original settings"):
        other.ensure_schema()


def test_two_local_databases_do_not_share_symbols(tmp_path, settings):
    for folder, name in [("one", "alpha"), ("two", "beta")]:
        root = tmp_path / folder
        root.mkdir()
        with GraphStore.from_config(root) as db:
            db.ensure_schema()
            db.save_entities([entity(name)])
    with GraphStore.from_config(tmp_path / "one") as db:
        db.ensure_schema()
        assert set(db.resolve_names(["alpha", "beta"])) == {"alpha"}


def test_database_files_excluded_from_repo_scan(tmp_path, settings):
    from graph_rag.change_detection import build_merkle_tree, collect_file_hashes

    (tmp_path / ".codelens").mkdir()
    (tmp_path / ".codelens" / "fake.py").write_text("def ignored(): pass")
    (tmp_path / "real.py").write_text("def included(): pass")
    assert set(collect_file_hashes(build_merkle_tree(tmp_path))) == {"real.py"}
