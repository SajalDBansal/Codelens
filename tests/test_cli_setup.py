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
    monkeypatch.setattr(sys.stdin, 'isatty', lambda: True)

def test_init_order_and_all_defaults_saved(monkeypatch, capsys):
    terminal(monkeypatch)
    prompts = []

    def key(prompt):
        prompts.append('key')
        return 'private-init-test-key'

    def choice(label, options, current, **kwargs):
        prompts.append('embedding' if 'Embedding' in label else 'reasoning')
        return options[1]
    monkeypatch.setattr(config_commands.getpass, 'getpass', key)
    monkeypatch.setattr(interactive, 'choose', choice)
    app.main(['init'])
    saved = settings.load_settings()
    assert prompts == ['key', 'embedding', 'reasoning']
    assert saved.embedding_model == 'text-embedding-3-large'
    assert saved.embedding_dimensions == 3072
    assert saved.reasoning_model == 'gpt-4.1-mini'
    document = (state.home() / 'config.json').read_text()
    assert set(json.loads(document)['engine']) == set(EngineOptions.model_fields)
    assert 'private-init-test-key' not in document + capsys.readouterr().out
    assert settings.api_key(saved) == 'private-init-test-key'

def test_init_cancel_leaves_existing_key_and_settings(configured, monkeypatch):
    terminal(monkeypatch)
    before = (state.home() / 'config.json').read_bytes()
    monkeypatch.setattr(config_commands.getpass, 'getpass', lambda label: 'replacement-never-saved')

    def cancel(*args, **kwargs):
        raise KeyboardInterrupt
    monkeypatch.setattr(interactive, 'choose', cancel)
    with pytest.raises(SystemExit):
        app.main(['init'])
    assert (state.home() / 'config.json').read_bytes() == before
    assert settings.api_key(configured) == 'test-placeholder-not-a-real-key'

def test_list_covers_every_engine_constant_and_has_descriptions(capsys):
    app.main(['config', 'list', '--json'])
    catalog = json.loads(capsys.readouterr().out)
    assert not catalog['saved'] and (not state.home().exists())
    names = {row['name'] for row in catalog['settings']}
    for name in vars(config):
        if name.isupper():
            assert config_commands.normalize_name(name) in names, name
    assert all((field.description for field in EngineOptions.model_fields.values()))
    assert names == set(config_commands.editable_names())

@pytest.mark.parametrize('keys,expected', [('0\r', '50'), ('\x7f8\r', '8'), ('\r', '5'), ('\x157\r', '7')])
def test_real_editable_prompt_supports_append_backspace_enter_and_clear(monkeypatch, keys, expected):
    terminal(monkeypatch)
    with create_pipe_input() as pipe, create_app_session(input=pipe, output=DummyOutput()):
        pipe.send_text(keys)
        assert interactive.edit('default_top_k', '5') == expected
