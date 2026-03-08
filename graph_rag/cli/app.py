"""Typer command tree for the local CodeLens package."""
import json
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Annotated
import typer
from pydantic import ValidationError
from . import config_commands
from ._console import configure_console
from .interactive import console
from .projects import add_project, entry_for, forget, inspect_database, load_project, registry, remember, resolve_project
from .settings import EMBEDDING_MODELS, REASONING_MODELS, Settings, api_key, load_settings, require_settings
from .state import StateError, config_directory, home
app = typer.Typer(help='Local codebase graphs and OpenAI RAG. Start with codelens init.', no_args_is_help=True, pretty_exceptions_enable=False, rich_markup_mode='rich')
app.add_typer(config_commands.app, name='config', rich_help_panel='Setup')
projects_app = typer.Typer(help='Register, find and select local projects.', no_args_is_help=True)
app.add_typer(projects_app, name='projects', rich_help_panel='Projects')

class ModelChange(str, Enum):
    keep = 'keep'
    rebuild = 'rebuild'
    error = 'error'
ConfigDir = Annotated[Path | None, typer.Option('--config-dir', help='Settings/registry directory (default: ~/.codelens).')]
Project = Annotated[str | None, typer.Option(help='Project name, ID, ID prefix, or registered path.')]
Database = Annotated[str | None, typer.Option('--db', help='Explicit graph file (legacy compatibility).')]
ModelPolicy = Annotated[ModelChange | None, typer.Option('--on-model-change', help='Embedding mismatch decision; otherwise prompts in a terminal.')]
TopK = Annotated[int | None, typer.Option('--top-k', min=1, help='Override saved default_top_k.')]
Hops = Annotated[int | None, typer.Option(min=1, max=2, help='Override saved default_hops (1 or 2).')]
DocTopK = Annotated[int | None, typer.Option('--doc-top-k', min=0, help='Override saved default_doc_top_k.')]
NoUpdate = Annotated[bool, typer.Option('--no-update', help='Query the stored version without checking source files.')]
ShowContext = Annotated[bool, typer.Option('--show-context', help='Show the evidence supplied to the answer model.')]
NoAnswer = Annotated[bool, typer.Option('--no-answer', help='Retrieve evidence without generating an answer.')]

@app.callback()
def root(ctx: typer.Context, config_dir: ConfigDir=None):
    ctx.with_resource(config_directory(config_dir))

@app.command('init', rich_help_panel='Setup')
def initialize():
    """Set up the API key and models; save all recommended settings."""
    config_commands.initialize()

def show(value):
    print(json.dumps(value, indent=2, ensure_ascii=False))

def available_models(settings: Settings) -> set[str]:
    from openai import OpenAI
    try:
        with OpenAI(api_key=api_key(settings), timeout=20, max_retries=1) as client:
            return {model.id for model in client.models.list()}
    except StateError:
        raise
    except Exception as error:
        raise StateError(f'OpenAI access check failed ({type(error).__name__}). Check credentials and connectivity.') from error

@config_commands.app.command('check')
def check_config():
    """Check credentials and model IDs online; send no repository content."""
    settings = require_settings()
    missing = {settings.embedding_model, settings.reasoning_model} - available_models(settings)
    if missing:
        raise StateError('Model IDs not returned for this key: ' + ', '.join(sorted(missing)))
    console.print('OpenAI credentials accepted; both model IDs are listed. Endpoint behavior and billing were not tested.', style='green')

@app.command(rich_help_panel='Setup')
def models(available: Annotated[bool, typer.Option('--available', help='List IDs available to your API key.')]=False):
    """List embedding choices and answer model suggestions."""
    if available:
        print('Account model IDs (listing does not establish endpoint compatibility):')
        print('\n'.join(sorted(available_models(require_settings()))))
    else:
        show({'embedding_models_and_default_dimensions': EMBEDDING_MODELS, 'answer_model_suggestions': REASONING_MODELS, 'note': 'Custom answer models must support Chat Completions with text input/output. Availability depends on your account.'})

def project_status(project):
    show({**project.model_dump(), 'database_path': str(project.database), 'stored_embedding': inspect_database(project.database), 'freshness': 'Source checked at last_indexed_at; ask/retrieve recheck by default.'})

@projects_app.command('list')
def list_projects(search: Annotated[str, typer.Argument(help='Optional name, ID, or path filter.')]=''):
    """List registered projects; * marks the selected default."""
    data = registry()
    for item in data.projects:
        if search.casefold() not in f'{item.id} {item.name} {item.root}'.casefold():
            continue
        marker = '*' if item.id == data.active_project_id else ' '
        available = 'available' if Path(item.root).is_dir() else 'source unavailable'
        print(f'{marker} {item.id}  {item.name}  [{available}]  {item.root}')
    if not data.projects:
        print('No registered projects. Run codelens embed PATH.')

@projects_app.command('add')
def register_project(path: Path, name: Annotated[str | None, typer.Option(help='Optional display name.')]=None, db: Database=None):
    """Register a project location without calling OpenAI."""
    project = add_project(path, name=name, db=db)
    print(f'Registered {project.name}: {project.id}')

@projects_app.command('use')
def use_project(project: str):
    """Choose the default project used outside another project directory."""
    selected = load_project(Path(entry_for(project).root))
    remember(selected, active=True)
    print(f'Selected {selected.name} ({selected.id}) as the default outside another project.')

@projects_app.command('show')
def show_project(project: str):
    """Show project metadata and the stored embedding profile."""
    project_status(load_project(Path(entry_for(project).root)))

@projects_app.command('remove')
def remove_project(project: str):
    """Unregister only; retain project metadata and graph files."""
    forget(project)
    print('Removed from the registry. Project metadata and graph files are retained.')

@app.command(rich_help_panel='Projects')
def status(project: Project=None):
    """Inspect local project metadata and the graph's embedding model."""
    project_status(resolve_project(project))

@app.command(rich_help_panel='Setup')
def doctor():
    """Check settings, credential availability and project locations; no network."""
    settings = load_settings()
    print(f'User data: {home()}')
    print(f"Configuration: {('present' if settings else 'missing; run codelens init')}")
    if settings:
        try:
            api_key(settings)
            print(f'Credential: available locally ({settings.credential_source}); not network-validated')
        except StateError as error:
            print(f'Credential: {error}')
    for item in registry().projects:
        print(f"Project {item.name}: {('available' if Path(item.root).is_dir() else 'source unavailable')}")

def main(argv=None):
    configure_console()
    try:
        app(args=argv, prog_name='codelens')
    except SystemExit as error:
        if error.code not in (None, 0):
            raise
    except ValidationError as error:
        raise SystemExit(f'Invalid settings: {config_commands.validation_message(error)}') from None
    except (StateError, OSError) as error:
        raise SystemExit(str(error)) from None
    except (KeyboardInterrupt, EOFError):
        raise SystemExit('Cancelled. Unsaved settings are unchanged; pending graph work can be retried.') from None
if __name__ == '__main__':
    main()
