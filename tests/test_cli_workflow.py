"""Real local graphs with deterministic model doubles: no keys, billing, or servers."""

import subprocess
import sys
from types import SimpleNamespace

import pytest

from graph_rag import config
from graph_rag.cli import (
    app,
    ask_command,
    config_commands,
    index_command,
    projects,
    settings,
    state,
)
from graph_rag.embedding import embedder
from graph_rag.generation.model_options import sampling_options
from graph_rag.pipeline import indexer
from graph_rag.retrieval import retriever
from graph_rag.storage import GraphStore


@pytest.fixture(autouse=True)
def isolated(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "OPENAI_API_KEY", "test-placeholder-not-a-real-key")
    monkeypatch.setattr(config, "LADYBUG_BUFFER_MB", 64)
    monkeypatch.setattr(config, "LADYBUG_THREADS", 1)
    monkeypatch.setattr(config, "GENERATE_SUMMARIES", False)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
    monkeypatch.chdir(tmp_path)
    keys = {}
    monkeypatch.setattr(
        settings.keyring,
        "set_password",
        lambda service, account, key: keys.update({(service, account): key}),
    )
    monkeypatch.setattr(
        settings.keyring, "get_password", lambda service, account: keys.get((service, account))
    )
    with state.config_directory(tmp_path / "user-data"):
        yield keys


@pytest.fixture
def configured():
    value = settings.Settings(
        embedding_dimensions=8,
        engine={"generate_summaries": False, "ladybug_buffer_mb": 64, "ladybug_threads": 1},
    )
    settings.store_key("test-placeholder-not-a-real-key")
    settings.save_settings(value)
    return value


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "checkout"
    root.mkdir()
    (root / "checkout.py").write_text(
        "def subtotal(price, quantity):\n    return price * quantity\n\n"
        'def checkout(price, quantity):\n    """Return the cart total."""\n'
        "    return subtotal(price, quantity)\n",
        encoding="utf-8",
    )
    (root / "README.md").write_text(
        "# Checkout\nThe `checkout` function uses `subtotal`.\n", encoding="utf-8"
    )
    return root


@pytest.fixture
def models(monkeypatch):
    calls = []

    class FakeEmbedder:
        def vectors(self, items):
            if items:
                calls.append(("index", config.EMBEDDING_MODEL, config.EMBEDDING_DIM))
            return {item.id: [1.0] + [0.0] * (config.EMBEDDING_DIM - 1) for item in items}

        embed_entities = vectors
        embed_docs = vectors
        embed_summaries = vectors

        def embed_query(self, question):
            calls.append(("query", config.EMBEDDING_MODEL, config.EMBEDDING_DIM))
            return [1.0] + [0.0] * (config.EMBEDDING_DIM - 1)

    def answer(result):
        calls.append(("answer", config.CHAT_MODEL))
        assert "checkout.py" in result.context_text
        return "checkout calls subtotal [1]."

    monkeypatch.setattr(indexer, "Embedder", FakeEmbedder)
    monkeypatch.setattr(
        indexer, "Summarizer", lambda: SimpleNamespace(summarise=lambda *args: None)
    )
    monkeypatch.setattr(retriever, "Embedder", FakeEmbedder)
    monkeypatch.setattr(ask_command, "answer_question", answer)
    return calls


def embed(repo):
    app.main(["embed", str(repo), "--quiet"])
    return projects.load_project(repo)


def test_first_run_prompts_once_and_stores_no_key(repo, models, monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    responses = iter(["", ""])
    monkeypatch.setattr("builtins.input", lambda prompt: next(responses))
    monkeypatch.setattr(
        config_commands.getpass, "getpass", lambda prompt: "test-placeholder-not-a-real-key"
    )
    project = embed(repo)
    monkeypatch.setattr("builtins.input", lambda prompt: pytest.fail("Setup ran twice"))
    app.main(["ask", "What does checkout do?", "--project", project.id])
    assert settings.load_settings().embedding_dimensions == 1536
    for file in [
        state.home() / "config.json",
        state.home() / "projects.json",
        project.manifest_path,
    ]:
        assert config.OPENAI_API_KEY not in file.read_text(encoding="utf-8")


def test_noninteractive_first_run_is_actionable(repo):
    with pytest.raises(SystemExit, match="Run codelens init"):
        app.main(["embed", str(repo)])
    assert not (repo / ".codelens").exists()


def test_keyring_and_config_show_never_print_secret(monkeypatch, capsys):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(
        config_commands.getpass, "getpass", lambda prompt: "private-test-credential"
    )
    app.main(["config", "set", "--api-key"])
    assert settings.api_key(settings.load_settings()) == "private-test-credential"
    app.main(["config", "show"])
    app.main(["doctor"])
    assert "private-test-credential" not in capsys.readouterr().out
    assert "private-test-credential" not in (state.home() / "config.json").read_text()


def test_keyring_failure_never_falls_back_to_plaintext(monkeypatch):
    def fail(*args):
        raise RuntimeError("no credential backend")

    monkeypatch.setattr(settings.keyring, "set_password", fail)
    with pytest.raises(state.StateError, match="No key was written to JSON"):
        settings.store_key("private-test-credential")
    assert not (state.home() / "config.json").exists()


def test_config_validation_and_model_defaults(configured):
    app.main(["config", "set", "--embedding-model", "text-embedding-3-large"])
    assert settings.load_settings().embedding_dimensions == 3072
    with pytest.raises(SystemExit, match="Dimensions"):
        app.main(["config", "set", "--dimensions", "4000"])
    with pytest.raises(SystemExit, match="cannot generate answers"):
        app.main(["config", "set", "--reasoning-model", "text-embedding-3-small"])
    assert settings.load_settings().embedding_dimensions == 3072


def test_projects_select_search_and_remove_without_deleting(
    repo, tmp_path, configured, models, capsys, monkeypatch
):
    first = embed(repo)
    other = tmp_path / "other"
    other.mkdir()
    second = projects.add_project(other)
    app.main(["projects", "use", second.id])
    assert projects.resolve_project().id == second.id
    monkeypatch.chdir(repo)
    assert projects.resolve_project().id == first.id  # current repository wins over default
    monkeypatch.chdir(tmp_path)
    app.main(["projects", "list", "checkout"])
    assert str(repo) in capsys.readouterr().out
    assert projects.resolve_project(first.id[:8]).id == first.id
    app.main(["projects", "remove", first.id])
    assert first.database.exists() and first.manifest_path.exists()
    assert len(projects.registry().projects) == 1


def test_duplicate_project_names_require_id(repo, tmp_path):
    projects.add_project(repo, name="demo")
    second = tmp_path / "second"
    second.mkdir()
    projects.add_project(second, name="demo")
    with pytest.raises(state.StateError, match="ambiguous"):
        projects.entry_for("demo")


def test_registry_detects_missing_source_and_does_not_recreate_it(repo, capsys):
    project = projects.add_project(repo)
    moved = repo.with_name("moved")
    repo.rename(moved)
    app.main(["projects", "list"])
    assert "source unavailable" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="No project metadata"):
        app.main(["status", "--project", project.id])
    assert not repo.exists()


def test_corrupt_state_is_not_overwritten(configured):
    file = state.home() / "config.json"
    file.write_text("{broken", encoding="utf-8")
    with pytest.raises(SystemExit, match="Invalid or unsupported metadata"):
        app.main(["config", "set", "--reasoning-model", "gpt-4.1-mini"])
    assert file.read_text() == "{broken"


def test_atomic_write_preserves_previous_document(configured, monkeypatch):
    path = state.home() / "config.json"
    before = path.read_bytes()

    def fail(*args):
        raise OSError("simulated replace failure")

    monkeypatch.setattr(state.os, "replace", fail)
    with pytest.raises(OSError):
        settings.save_settings(settings.Settings())
    assert path.read_bytes() == before
    assert not list(state.home().glob("*.tmp"))


def test_project_lock_prevents_second_command(repo, configured, models):
    project = embed(repo)
    with (
        state.locked(repo / ".codelens" / "project.lock"),
        pytest.raises(SystemExit, match="Another command"),
    ):
        app.main(["ask", "checkout", "--project", project.id])


@pytest.mark.parametrize(
    "change",
    [["--embedding-model", "text-embedding-3-large", "--dimensions", "8"], ["--dimensions", "4"]],
)
def test_embedding_mismatch_stops_before_network(repo, configured, models, change):
    project = embed(repo)
    app.main(["config", "set", *change])
    models.clear()
    before = project.database.read_bytes()
    with pytest.raises(SystemExit, match="on-model-change"):
        app.main(["ask", "checkout", "--project", project.id])
    assert models == []
    assert project.database.read_bytes() == before


def test_keep_uses_old_query_model_but_new_reasoning_model(repo, configured, models):
    project = embed(repo)
    app.main(
        [
            "config",
            "set",
            "--embedding-model",
            "text-embedding-3-large",
            "--dimensions",
            "4",
            "--reasoning-model",
            "gpt-5-mini",
        ]
    )
    models.clear()
    app.main(["ask", "checkout", "--project", project.id, "--on-model-change", "keep"])
    assert ("query", "text-embedding-3-small", 8) in models
    assert ("answer", "gpt-5-mini") in models
    assert not [call for call in models if call[0] == "index"]
    assert projects.load_project(repo).last_reasoning_model == "gpt-5-mini"
    assert settings.load_settings().embedding_model == "text-embedding-3-large"


def test_reasoning_change_does_not_reembed(repo, configured, models):
    project = embed(repo)
    app.main(["config", "set", "--reasoning-model", "gpt-4.1-mini"])
    models.clear()
    app.main(["ask", "checkout", "--project", project.id])
    assert not [call for call in models if call[0] == "index"]
    assert projects.load_project(repo).database == project.database


def test_rebuild_failure_preserves_previous_graph_then_resumes(
    repo, configured, models, monkeypatch
):
    project = embed(repo)
    before = project.database.read_bytes()
    app.main(["config", "set", "--dimensions", "4"])
    original = indexer.Embedder.embed_entities

    def fail(self, items):
        raise RuntimeError("simulated API outage")

    with monkeypatch.context() as patch:
        patch.setattr(indexer.Embedder, "embed_entities", fail)
        with pytest.raises(RuntimeError, match="simulated API outage"):
            app.main(["embed", "--project", project.id, "--on-model-change", "rebuild", "--quiet"])
    failed = projects.load_project(repo)
    assert failed.status == "ready"
    assert failed.database == project.database
    assert project.database.read_bytes() == before
    pending = (repo / failed.pending_database).resolve()
    app.main(["embed", "--project", project.id, "--on-model-change", "rebuild", "--quiet"])
    rebuilt = projects.load_project(repo)
    assert rebuilt.database == pending
    assert rebuilt.embedding_dimensions == 4 and rebuilt.pending_database is None
    assert project.active_database in rebuilt.previous_databases
    assert project.database.read_bytes() == before
    assert indexer.Embedder.embed_entities is original
    app.main(["retrieve", "checkout", "--project", project.id])
    assert ("query", "text-embedding-3-small", 4) in models


def test_freshness_check_and_no_update(repo, configured, models, capsys):
    project = embed(repo)
    (repo / "checkout.py").write_text("def new_checkout():\n    return 42\n", encoding="utf-8")
    models.clear()
    app.main(["retrieve", "checkout", "--project", project.id, "--no-update"])
    assert not [call for call in models if call[0] == "index"]
    app.main(["retrieve", "checkout", "--project", project.id])
    assert [call for call in models if call[0] == "index"]
    assert "new_checkout" in capsys.readouterr().out
    assert projects.load_project(repo).last_reasoning_model is None
    assert not [call for call in models if call[0] == "answer"]


def test_failed_incremental_update_blocks_answer_until_recovered(
    repo, configured, models, monkeypatch
):
    project = embed(repo)
    (repo / "checkout.py").write_text("def checkout():\n    return 42\n", encoding="utf-8")

    def fail(self, items):
        raise RuntimeError("simulated outage")

    models.clear()
    with monkeypatch.context() as patch:
        patch.setattr(indexer.Embedder, "embed_entities", fail)
        with pytest.raises(RuntimeError, match="simulated outage"):
            app.main(["ask", "checkout", "--project", project.id])
    assert projects.load_project(repo).status == "failed"
    assert models == []
    with pytest.raises(SystemExit, match="did not complete"):
        app.main(["ask", "checkout", "--project", project.id, "--no-update"])
    app.main(["ask", "checkout", "--project", project.id])
    assert projects.load_project(repo).status == "ready"


def test_no_update_cannot_rebuild(repo, configured, models):
    project = embed(repo)
    app.main(["config", "set", "--dimensions", "4"])
    with pytest.raises(SystemExit, match="cannot be combined"):
        app.main(
            [
                "ask",
                "checkout",
                "--project",
                project.id,
                "--no-update",
                "--on-model-change",
                "rebuild",
            ]
        )


def test_adopts_old_graph_and_legacy_commands(repo, configured, models):
    database = repo / ".codelens" / "old.lbug"
    with settings.engine_settings(configured):
        indexer.run_indexing(repo, db_path=database, verbose=False)
    assert not (repo / ".codelens" / "project.json").exists()
    index_command.main([str(repo), "--db", str(database), "--quiet"])
    ask_command.main(["checkout", "--db", str(database), "--no-answer"])
    assert projects.load_project(repo).database == database
    assert len(projects.registry().projects) == 1


def test_readonly_inspection_does_not_create_database(tmp_path):
    missing = tmp_path / "missing" / "graph.lbug"
    with pytest.raises(FileNotFoundError):
        GraphStore(missing, read_only=True)
    assert not missing.parent.exists()


@pytest.mark.parametrize(
    "model,expected",
    [
        ("text-embedding-3-small", 8),
        ("text-embedding-3-large", 8),
        ("text-embedding-ada-002", None),
    ],
)
def test_embedding_request_sends_selected_dimensions(monkeypatch, model, expected):
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(data=[SimpleNamespace(index=0, embedding=[1.0] * 8)])

    monkeypatch.setattr(config, "EMBEDDING_DIM", 8)
    monkeypatch.setattr(
        embedder, "OpenAI", lambda **kw: SimpleNamespace(embeddings=SimpleNamespace(create=create))
    )
    embedder.Embedder(model=model).embed_query("checkout")
    assert captured.get("dimensions") == expected


@pytest.mark.parametrize(
    "model,expected",
    [("gpt-4.1-nano", {"temperature": 0}), ("gpt-5-mini", {}), ("o3", {}), ("custom-model", {})],
)
def test_sampling_options_avoid_incompatible_reasoning_parameters(model, expected):
    assert sampling_options(model) == expected


def test_model_access_check_only_lists_ids(configured, monkeypatch, capsys):
    monkeypatch.setattr(
        app, "available_models", lambda cfg: {cfg.embedding_model, cfg.reasoning_model}
    )
    app.main(["config", "check"])
    assert "billing were not tested" in " ".join(capsys.readouterr().out.split())


def test_registry_lock_prevents_overwrite(repo):
    first = projects.add_project(repo)
    with (
        state.locked(state.home() / ".state.lock"),
        pytest.raises(state.StateError, match="Another command"),
    ):
        projects.remember(first, active=True)
    assert projects.registry().projects[0].id == first.id


def test_help_in_fresh_python_process_has_no_side_effects(tmp_path):
    # Use an explicit isolated directory, even in the child process.
    result = subprocess.run(
        [sys.executable, "-m", "graph_rag", "--config-dir", str(state.home()), "--help"],
        text=True,
        encoding="utf-8",
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    assert "retrieve" in result.stdout and not result.stderr
    assert not state.home().exists()


def test_legacy_command_names_use_saved_settings(repo, configured, models, monkeypatch):
    monkeypatch.setattr(config, "EMBEDDING_MODEL", "text-embedding-3-small")
    monkeypatch.setattr(config, "EMBEDDING_DIM", 8)
    index_command.main([str(repo), "--quiet"])
    ask_command.main(["checkout", "--db", str(repo / ".codelens" / "graph.lbug"), "--no-answer"])
    assert settings.load_settings().schema_version == 2
    assert ("query", "text-embedding-3-small", 8) in models


def test_graph_from_another_repository_is_rejected(repo, tmp_path, configured, models):
    project = embed(repo)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    with pytest.raises(state.StateError, match="belongs to"):
        projects.add_project(unrelated, db=project.database)


def test_database_metadata_overrules_manifest_model_cache(repo, configured, models):
    project = embed(repo)
    project.embedding_model = "text-embedding-3-large"
    project.embedding_dimensions = 4
    projects.save_project(project)
    models.clear()
    app.main(["retrieve", "checkout", "--project", project.id, "--no-update"])
    assert ("query", "text-embedding-3-small", 8) in models


def test_interactive_model_choice_defaults_to_keep(repo, configured, models, monkeypatch):
    project = embed(repo)
    app.main(["config", "set", "--dimensions", "4"])
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    models.clear()
    app.main(["retrieve", "checkout", "--project", project.id, "--no-update"])
    assert ("query", "text-embedding-3-small", 8) in models


def test_scoped_settings_restore_after_exception(configured):
    original = (config.EMBEDDING_MODEL, config.EMBEDDING_DIM, config.CHAT_MODEL)
    with (
        pytest.raises(RuntimeError),
        settings.engine_settings(configured, profile=("text-embedding-3-large", 4)),
    ):
        assert config.EMBEDDING_DIM == 4
        raise RuntimeError("simulated failure")
    assert (config.EMBEDDING_MODEL, config.EMBEDDING_DIM, config.CHAT_MODEL) == original


def test_project_lock_also_blocks_another_process(repo):
    projects.add_project(repo)
    lock = repo / ".codelens" / "project.lock"
    code = (
        "import sys\nfrom filelock import FileLock, Timeout\n"
        "try:\n with FileLock(sys.argv[1], timeout=0): pass\n"
        "except Timeout: sys.exit(7)\n"
    )
    with state.locked(lock):
        result = subprocess.run(
            [sys.executable, "-c", code, str(lock)], capture_output=True, check=False
        )
    assert result.returncode == 7, result.stderr
