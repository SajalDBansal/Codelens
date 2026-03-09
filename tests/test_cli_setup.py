"""Setup, complete configuration coverage, and real editable terminal input."""

import json
import sys

import pytest
import test_cli_workflow as cli_fixtures
from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from typer.testing import CliRunner

from graph_rag import config
from graph_rag.cli import app, config_commands, interactive, settings, state
from graph_rag.cli.engine_options import EngineOptions

isolated = cli_fixtures.isolated
configured = cli_fixtures.configured


def terminal(monkeypatch):
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)


def test_init_order_and_all_defaults_saved(monkeypatch, capsys):
    terminal(monkeypatch)
    prompts = []

    def key(prompt):
        prompts.append("key")
        return "private-init-test-key"

    def choice(label, options, current, **kwargs):
        prompts.append("embedding" if "Embedding" in label else "reasoning")
        return options[1]

    monkeypatch.setattr(config_commands.getpass, "getpass", key)
    monkeypatch.setattr(interactive, "choose", choice)
    app.main(["init"])
    saved = settings.load_settings()
    assert prompts == ["key", "embedding", "reasoning"]
    assert saved.embedding_model == "text-embedding-3-large"
    assert saved.embedding_dimensions == 3072
    assert saved.reasoning_model == "gpt-4.1-mini"
    document = (state.home() / "config.json").read_text()
    assert set(json.loads(document)["engine"]) == set(EngineOptions.model_fields)
    assert "private-init-test-key" not in document + capsys.readouterr().out
    assert settings.api_key(saved) == "private-init-test-key"


def test_init_again_preserves_tuning_and_shortened_dimensions(configured, monkeypatch):
    terminal(monkeypatch)
    app.main(["config", "update", "default_top_k", "11"])
    monkeypatch.setattr(config_commands.getpass, "getpass", lambda label: "")
    monkeypatch.setattr("builtins.input", lambda label: "")
    app.main(["init"])
    saved = settings.load_settings()
    assert saved.engine.default_top_k == 11
    assert saved.embedding_dimensions == 8
    assert settings.api_key(saved) == "test-placeholder-not-a-real-key"


def test_init_cancel_leaves_existing_key_and_settings(configured, monkeypatch):
    terminal(monkeypatch)
    before = (state.home() / "config.json").read_bytes()
    monkeypatch.setattr(config_commands.getpass, "getpass", lambda label: "replacement-never-saved")

    def cancel(*args, **kwargs):
        raise KeyboardInterrupt

    monkeypatch.setattr(interactive, "choose", cancel)
    with pytest.raises(SystemExit):
        app.main(["init"])
    assert (state.home() / "config.json").read_bytes() == before
    assert settings.api_key(configured) == "test-placeholder-not-a-real-key"


def test_bare_config_is_read_only_and_helpful(capsys):
    app.main(["config"])
    output = capsys.readouterr().out
    assert all(word in output for word in ["init", "list", "show", "update"])
    assert not state.home().exists()


def test_list_covers_every_engine_constant_and_has_descriptions(capsys):
    app.main(["config", "list", "--json"])
    catalog = json.loads(capsys.readouterr().out)
    assert not catalog["saved"] and not state.home().exists()
    names = {row["name"] for row in catalog["settings"]}
    for name in vars(config):
        if name.isupper():
            assert config_commands.normalize_name(name) in names, name
    assert all(field.description for field in EngineOptions.model_fields.values())
    assert names == set(config_commands.editable_names())


@pytest.mark.parametrize("name", ["default_top_k", "engine.default_top_k", "DEFAULT_TOP_K"])
def test_update_and_show_aliases(configured, name, capsys):
    app.main(["config", "update", name, "7"])
    capsys.readouterr()
    app.main(["config", "show", name])
    assert json.loads(capsys.readouterr().out) == 7


@pytest.mark.parametrize(
    "name,value",
    [
        ("default_top_k", "0"),
        ("default_top_k", "true"),
        ("default_top_k", "oops"),
        ("default_hops", "3"),
        ("min_similarity", "1.1"),
        ("rrf_k", "NaN"),
        ("ignored_directories", "[]"),
        ("ignored_directory_suffixes", '["../tmp"]'),
        ("prose_doc_types", '["unknown"]'),
        ("edge_type_weights", '{"CALLS":1}'),
        ("schema_version", "9"),
        ("provider", "anything"),
        ("unknown_setting", "1"),
        ("embedding_dimensions", "4000"),
        ("embedding_dimensions", "true"),
        ("reasoning_model", "text-embedding-3-small"),
    ],
)
def test_invalid_update_never_overwrites(configured, name, value):
    before = (state.home() / "config.json").read_bytes()
    with pytest.raises(SystemExit):
        app.main(["config", "update", name, value])
    assert (state.home() / "config.json").read_bytes() == before


def test_collection_options_apply_and_restore(configured):
    old = {
        name: getattr(config, name)
        for name in [
            "IGNORED_DIRECTORIES",
            "IGNORED_DIRECTORY_SUFFIXES",
            "PROSE_DOC_TYPES",
            "EDGE_TYPE_WEIGHTS",
        ]
    }
    weights = {**config.EDGE_TYPE_WEIGHTS, "CALLS": 0.25}
    for name, value in {
        "ignored_directories": [".codelens", "custom"],
        "extra_ignored_directories": ["additional"],
        "ignored_directory_suffixes": [".generated"],
        "prose_doc_types": ["markdown"],
        "edge_type_weights": weights,
    }.items():
        app.main(["config", "update", name, json.dumps(value)])
    with settings.engine_settings(settings.load_settings()):
        assert config.IGNORED_DIRECTORIES == {".codelens", "custom", "additional"}
        assert config.IGNORED_DIRECTORY_SUFFIXES == (".generated",)
        assert config.PROSE_DOC_TYPES == {"markdown"}
        assert config.EDGE_TYPE_WEIGHTS["CALLS"] == 0.25
    assert {name: getattr(config, name) for name in old} == old


@pytest.mark.parametrize(
    "keys,expected", [("0\r", "50"), ("\x7f8\r", "8"), ("\r", "5"), ("\x157\r", "7")]
)
def test_real_editable_prompt_supports_append_backspace_enter_and_clear(
    monkeypatch, keys, expected
):
    terminal(monkeypatch)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        pipe.send_text(keys)
        assert interactive.edit("default_top_k", "5") == expected


def test_update_uses_current_value_and_retries_invalid_input(configured, monkeypatch):
    terminal(monkeypatch)
    calls = []
    responses = iter(["0", "8"])

    def edit(label, current, choices):
        calls.append(current)
        return next(responses)

    monkeypatch.setattr(interactive, "edit", edit)
    app.main(["config", "update", "default_top_k"])
    assert calls == ["5", "0"]
    assert settings.load_settings().engine.default_top_k == 8


def test_update_selects_name_when_omitted(configured, monkeypatch):
    terminal(monkeypatch)
    responses = iter(["generate_summaries", "true"])
    monkeypatch.setattr(interactive, "edit", lambda *args: next(responses))
    app.main(["config", "update"])
    assert settings.load_settings().engine.generate_summaries is True


def test_cancel_prefilled_update_does_not_save(configured, monkeypatch):
    terminal(monkeypatch)
    before = (state.home() / "config.json").read_bytes()
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        pipe.send_text("7\x03")
        with pytest.raises(SystemExit):
            app.main(["config", "update", "default_top_k"])
    assert (state.home() / "config.json").read_bytes() == before


def test_update_merges_concurrent_unrelated_change(configured, monkeypatch):
    def edit(*args):
        config_commands.save_changes({"ladybug_threads": 3})
        return "9"

    monkeypatch.setattr(interactive, "edit", edit)
    app.main(["config", "update", "default_top_k"])
    saved = settings.load_settings()
    assert saved.engine.default_top_k == 9 and saved.engine.ladybug_threads == 3


def test_update_rejects_concurrent_same_setting_change(configured, monkeypatch):
    def edit(*args):
        config_commands.save_changes({"default_top_k": 12})
        return "9"

    monkeypatch.setattr(interactive, "edit", edit)
    with pytest.raises(SystemExit, match="changed in another command"):
        app.main(["config", "update", "default_top_k"])
    assert settings.load_settings().engine.default_top_k == 12


def test_key_update_hidden_and_show_masked(configured, monkeypatch, capsys):
    terminal(monkeypatch)
    monkeypatch.setattr(config_commands.getpass, "getpass", lambda label: "private-replacement-key")
    app.main(["config", "update", "OPENAI_API_KEY"])
    app.main(["config", "show", "api_key"])
    app.main(["config", "list", "--json"])
    assert settings.api_key(configured) == "private-replacement-key"
    assert "private-replacement-key" not in capsys.readouterr().out
    assert "private-replacement-key" not in (state.home() / "config.json").read_text()
    with pytest.raises(SystemExit, match="Do not pass API keys"):
        app.main(["config", "update", "api_key", "never-use-an-argument"])


def test_interactive_commands_without_tty_give_actionable_errors(configured):
    for command in [
        ["init"],
        ["config", "update", "default_top_k"],
        ["config", "update", "api_key"],
    ]:
        with pytest.raises(SystemExit, match="terminal"):
            app.main(command)


def test_model_update_sets_dimensions_and_keeps_other_settings(configured):
    app.main(["config", "update", "embedding_model", "text-embedding-3-large"])
    saved = settings.load_settings()
    assert saved.embedding_dimensions == 3072
    assert saved.engine == configured.engine


def test_typer_runner_help_and_numeric_validation():
    runner = CliRunner()
    for command in [[], ["config"], ["config", "update"], ["ask"], ["projects", "add"]]:
        result = runner.invoke(app.app, [*command, "--help"])
        assert result.exit_code == 0, result.output
        assert "Usage:" in result.output
        assert "\x1b[" not in result.output
    result = runner.invoke(app.app, ["ask", "question", "--hops", "3"])
    assert result.exit_code == 2
    assert not state.home().exists()
