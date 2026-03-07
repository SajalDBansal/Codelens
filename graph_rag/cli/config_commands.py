"""First-run setup and discoverable, validated configuration commands."""
import getpass
import json
import sys
from typing import Annotated
import typer
from pydantic import ValidationError
from rich.panel import Panel
from rich.table import Table
from . import interactive
from .engine_options import EngineOptions
from .interactive import console
from .settings import EMBEDDING_MODELS, REASONING_MODELS, Settings, api_key, load_settings, store_key
from .state import StateError, home, locked, write_json
app = typer.Typer(help='List, inspect, and update saved settings.', no_args_is_help=False)
READ_ONLY = {'schema_version', 'provider', 'credential_source', 'config_file'}
ALIASES = {'openai_api_key': 'api_key', 'chat_model': 'reasoning_model', 'embedding_dim': 'embedding_dimensions', 'dimensions': 'embedding_dimensions'}
MODEL_HELP = {'embedding_model': 'Model used to embed code and questions; changes require keep/rebuild.', 'embedding_dimensions': 'Vector dimensions; changes require keep/rebuild.', 'reasoning_model': 'Chat Completions model for answers and new folder summaries.', 'api_key': 'Stored in the OS keyring; replace through hidden input, never displayed.'}

def editable_names() -> list[str]:
    return [*MODEL_HELP, *EngineOptions.model_fields]

def normalize_name(name: str) -> str:
    result = name.strip().lower().removeprefix('engine.')
    result = ALIASES.get(result, result)
    if result not in {*editable_names(), *READ_ONLY}:
        raise StateError(f"Unknown setting '{name}'. Run codelens config list.")
    return result

def description(name: str) -> str:
    if name in MODEL_HELP:
        return MODEL_HELP[name]
    return EngineOptions.model_fields[name].description or name.replace('_', ' ').capitalize()

def current_value(settings: Settings, name: str):
    if name == 'api_key':
        try:
            api_key(settings)
        except StateError:
            return 'unavailable (run codelens config update api_key)'
        return 'saved in OS keyring (hidden)'
    if name == 'config_file':
        return str(home() / 'config.json')
    if name in EngineOptions.model_fields:
        return getattr(settings.engine, name)
    return getattr(settings, name)

def rendered(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)

def changed_settings(current: Settings, changes: dict) -> Settings:
    values = current.model_dump()
    for name, value in changes.items():
        if name in EngineOptions.model_fields:
            values['engine'][name] = value
        elif name in MODEL_HELP and name != 'api_key':
            values[name] = value
        else:
            raise StateError(f'{name} is read-only. Run codelens config list for editable names.')
    model = values['embedding_model']
    if model != current.embedding_model and 'embedding_dimensions' not in changes:
        values['embedding_dimensions'] = EMBEDDING_MODELS.get(model, current.embedding_dimensions)
    return Settings.model_validate(values)

def validation_message(error: ValidationError) -> str:
    return '; '.join((item['msg'] for item in error.errors(include_input=False)))

def save_changes(changes: dict, *, key: str | None=None, expected: Settings | None=None):
    """Re-read under the lock; never overwrite unrelated edits made during a prompt."""
    with locked(home() / '.state.lock'):
        current = load_settings() or Settings()
        if expected is not None:
            watched = set(changes)
            if watched & {'embedding_model', 'embedding_dimensions'}:
                watched |= {'embedding_model', 'embedding_dimensions'}
            if any((current_value(current, name) != current_value(expected, name) for name in watched)):
                raise StateError('This setting changed in another command. Run config update again.')
        selected = changed_settings(current, changes)
        if key is not None:
            store_key(key)
        write_json(home() / 'config.json', selected)
    return selected

def initialize() -> Settings:
    interactive.require_terminal()
    current = load_settings() or Settings()
    console.print(Panel('API key > Embedding model > Reasoning model\nEnter keeps the current choice. Ctrl+C cancels setup.', title='CodeLens setup'))
    while True:
        value = getpass.getpass('1/3 OpenAI API key (hidden; Enter keeps saved key): ').strip()
        if value:
            break
        try:
            api_key(current)
            break
        except StateError:
            console.print('No saved key is available; enter your OpenAI API key.', style='yellow')
    embedding = interactive.choose('2/3 Embedding model', list(EMBEDDING_MODELS), current.embedding_model)
    while True:
        reasoning = interactive.choose('3/3 Reasoning / answer model (also used for folder summaries)', REASONING_MODELS, current.reasoning_model, custom=True)
        changes = {'embedding_model': embedding, 'reasoning_model': reasoning}
        try:
            changed_settings(current, changes)
            break
        except ValidationError as error:
            console.print(validation_message(error), style='yellow')
    selected = save_changes(changes, key=value or None, expected=current)
    console.print(f"Saved {home() / 'config.json'}", style='green')
    console.print(f'Embedding: {selected.embedding_model} ({selected.embedding_dimensions} dimensions)\nReasoning: {selected.reasoning_model}\nAll recommended settings are saved; existing tuning choices are preserved.\nYour API key is stored separately in the OS credential store.\nRun codelens config to see the commands, or codelens config list to see every setting.\nNext: codelens embed PATH')
    if embedding != current.embedding_model:
        console.print('Existing graphs will ask you to keep their model or rebuild when used.')
    return selected

@app.callback(invoke_without_command=True)
def config_menu(ctx: typer.Context):
    """Manage the user configuration shared by all local projects."""
    if ctx.invoked_subcommand is None:
        console.print(f"Configuration: {home() / 'config.json'}")
        console.print('First-time setup: codelens init')
        console.print(ctx.get_help())

@app.command('list')
def list_settings(as_json: Annotated[bool, typer.Option('--json', help='Output a JSON catalog.')]=False):
    """List every editable name, its current value, and its purpose."""
    saved = load_settings()
    settings = saved or Settings()
    rows = [{'name': name, 'value': current_value(settings, name), 'description': description(name)} for name in editable_names()]
    if as_json:
        print(json.dumps({'saved': saved is not None, 'config_file': str(home() / 'config.json'), 'settings': rows}, indent=2, ensure_ascii=False))
        return
    console.print(f"{('Saved configuration' if saved else 'Recommended defaults (not saved)')}: {home() / 'config.json'}")
    table = Table(title='CodeLens settings', show_lines=True)
    table.add_column('Name', style='cyan', overflow='fold')
    table.add_column('Current value', overflow='fold', max_width=40)
    table.add_column('Purpose', overflow='fold')
    for row in rows:
        preview = rendered(row['value'])
        if len(preview) > 64:
            preview = preview[:61] + '...'
        table.add_row(row['name'], preview, row['description'])
    console.print(table)
    console.print('Use config show NAME for the full value; config update NAME to edit it.')
    console.print('schema_version, provider, credential_source, and config_file are read-only metadata.')

@app.command('show')
def show_settings(name: Annotated[str | None, typer.Argument(help='Optional setting name.')]=None):
    """Print saved configuration (or one value) as JSON; never reveal the key."""
    settings = load_settings()
    if settings is None:
        raise StateError('Not configured yet. Run codelens init, or config list to preview defaults.')
    value = current_value(settings, normalize_name(name)) if name else {'config_file': str(home() / 'config.json'), **settings.model_dump(), 'api_key': current_value(settings, 'api_key')}
    print(json.dumps(value, indent=2, ensure_ascii=False))
