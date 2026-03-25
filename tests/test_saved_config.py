"""Configuration must come from saved user settings, never ambient application variables."""

import importlib.util
import io
import json
import subprocess
import sys
from pathlib import Path

import pytest

# Reuse the isolated directory, credential doubles, and real-graph fixtures.
import test_cli_workflow as cli_fixtures

from graph_rag import config
from graph_rag.cli import app, ask_command, projects, settings, state

configured = cli_fixtures.configured
isolated = cli_fixtures.isolated
models = cli_fixtures.models
repo = cli_fixtures.repo


def test_import_ignores_environment_and_dotenv(tmp_path, monkeypatch):
    monkeypatch.setenv("EMBEDDING_DIM", "not-an-integer")
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-be-used")
    (tmp_path / ".env").write_text("EMBEDDING_DIM=broken\nOPENAI_API_KEY=dotenv-must-not-be-used\n")
    code = (
        "import json,sys; from graph_rag import config; "
        "print(json.dumps([config.EMBEDDING_DIM, "
        "config.OPENAI_API_KEY, 'dotenv' in sys.modules]))"
    )
    result = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=True
    )
    assert json.loads(result.stdout) == [1536, "", False]


def test_keyring_is_required_even_when_an_environment_key_exists(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "ignored-ambient-key")
    with pytest.raises(state.StateError, match="No saved OpenAI API key"):
        settings.api_key(settings.Settings())


def test_saved_models_and_tuning_override_ambient_values(repo, configured, models, monkeypatch):
    monkeypatch.setenv("EMBEDDING_MODEL", "text-embedding-3-large")
    monkeypatch.setenv("EMBEDDING_DIM", "3072")
    monkeypatch.setenv("CHAT_MODEL", "unwanted-model")
    monkeypatch.setenv("MIN_SIMILARITY", "1")
    monkeypatch.setenv("DB_PATH", str(repo / "unwanted.sqlite"))
    app.main(["embed", str(repo), "--quiet"])
    app.main(["ask", "checkout", "--project", "checkout"])
    assert ("query", "text-embedding-3-small", 8) in models
    assert ("answer", "gpt-4.1-nano") in models
    assert not (repo / "unwanted.sqlite").exists()


def test_old_config_preserves_choices_but_never_uses_env_credentials(monkeypatch):
    state.home().mkdir()
    path = state.home() / "config.json"
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "provider": "openai",
                "embedding_model": "text-embedding-3-large",
                "embedding_dimensions": 128,
                "reasoning_model": "gpt-4.1-mini",
                "credential_source": "env",
            }
        )
    )
    monkeypatch.setenv("OPENAI_API_KEY", "ignored-ambient-key")
    upgraded = settings.load_settings()
    assert upgraded.schema_version == 2 and upgraded.credential_source == "keyring"
    assert upgraded.embedding_dimensions == 128 and upgraded.reasoning_model == "gpt-4.1-mini"
    with pytest.raises(state.StateError, match="No saved OpenAI API key"):
        settings.api_key(upgraded)


def test_explicit_config_directory_replaces_environment_override(tmp_path, monkeypatch, capsys):
    original = state.home()
    alternate = tmp_path / "explicit-config"
    ignored = tmp_path / "ignored-config"
    monkeypatch.setenv("CODELENS_HOME", str(ignored))
    app.main(["--config-dir", str(alternate), "config", "set", "--reasoning-model", "gpt-4.1-mini"])
    capsys.readouterr()
    app.main(["--config-dir", str(alternate), "config", "show"])
    data = json.loads(capsys.readouterr().out)
    assert data["config_file"] == str(alternate / "config.json")
    assert data["reasoning_model"] == "gpt-4.1-mini"
    assert state.home() == original and not ignored.exists()


def test_piped_key_is_saved_without_echo_or_plaintext_file(monkeypatch, capsys):
    monkeypatch.setattr(sys, "stdin", io.StringIO("private-piped-test-key\n"))
    app.main(["config", "set", "--api-key-stdin"])
    assert settings.api_key(settings.load_settings()) == "private-piped-test-key"
    assert "private-piped-test-key" not in capsys.readouterr().out
    assert "private-piped-test-key" not in (state.home() / "config.json").read_text()


def test_environment_credential_flag_was_removed():
    with pytest.raises(SystemExit) as error:
        app.main(["config", "set", "--use-env"])
    assert error.value.code == 2


@pytest.mark.parametrize(
    "option",
    [
        "max_file_size_bytes=0",
        "default_top_k=0",
        "default_hops=3",
        "min_similarity=2",
        "unknown_option=1",
        "generate_summaries=bad",
    ],
)
def test_invalid_engine_option_does_not_overwrite_config(configured, option):
    before = (state.home() / "config.json").read_bytes()
    with pytest.raises(SystemExit):
        app.main(["config", "set", "--option", option])
    assert (state.home() / "config.json").read_bytes() == before


def test_saved_retrieval_defaults_and_explicit_overrides(repo, configured, models, monkeypatch):
    app.main(
        [
            "config",
            "set",
            "--option",
            "default_top_k=2",
            "--option",
            "default_hops=2",
            "--option",
            "default_doc_top_k=0",
            "--option",
            "min_similarity=0.25",
        ]
    )
    app.main(["embed", str(repo), "--quiet"])
    captured = []
    original = ask_command.retrieve

    def retrieve(**kwargs):
        captured.append(
            (kwargs["top_k"], kwargs["hops"], kwargs["doc_top_k"], config.MIN_SIMILARITY)
        )
        return original(**kwargs)

    monkeypatch.setattr(ask_command, "retrieve", retrieve)
    app.main(["retrieve", "checkout", "--project", "checkout"])
    app.main(
        [
            "retrieve",
            "checkout",
            "--project",
            "checkout",
            "--top-k",
            "3",
            "--hops",
            "1",
            "--doc-top-k",
            "1",
        ]
    )
    assert captured == [(2, 2, 0, 0.25), (3, 1, 1, 0.25)]
    assert config.MIN_SIMILARITY == 0.62


def test_saved_exclusions_apply_to_index_and_restore_runtime_defaults(repo, configured, models):
    hidden = repo / "generated_local"
    hidden.mkdir()
    (hidden / "ignore.py").write_text("def ignored():\n    return 1\n")
    app.main(["config", "set", "--option", 'extra_ignored_directories=["generated_local"]'])
    app.main(["embed", str(repo), "--quiet"])
    assert projects.load_project(repo).statistics["files"] == 3  # includes the managed .gitignore
    assert "generated_local" not in config.IGNORED_DIRECTORIES


def test_eval_uses_same_saved_settings_and_graph(repo, configured, models, tmp_path, capsys):
    app.main(["embed", str(repo), "--quiet"])
    questions = tmp_path / "questions.json"
    questions.write_text(json.dumps([{"question": "checkout", "expect": "checkout.py::checkout"}]))
    path = Path(__file__).resolve().parents[1] / "evals/run_eval.py"
    specification = importlib.util.spec_from_file_location("saved_config_eval", path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    with pytest.raises(SystemExit) as error:
        module.main(["--project", "checkout", "--questions", str(questions), "--min-recall", "1"])
    assert error.value.code == 0
    assert "Recall@all   1/1 = 100%" in capsys.readouterr().out
    assert ("query", "text-embedding-3-small", 8) in models
