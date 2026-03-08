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
from .settings import (
    EMBEDDING_MODELS,
    REASONING_MODELS,
    Settings,
    api_key,
    load_settings,
    store_key,
)
from .state import StateError, home, locked, write_json

app = typer.Typer(help="List, inspect, and update saved settings.", no_args_is_help=False)

# All editable engine fields are discovered from EngineOptions, not a second list.
READ_ONLY = {"schema_version", "provider", "credential_source", "config_file"}
ALIASES = {
    "openai_api_key": "api_key",
    "chat_model": "reasoning_model",
    "embedding_dim": "embedding_dimensions",
    "dimensions": "embedding_dimensions",
}
MODEL_HELP = {
    "embedding_model": "Model used to embed code and questions; changes require keep/rebuild.",
    "embedding_dimensions": "Vector dimensions; changes require keep/rebuild.",
    "reasoning_model": "Chat Completions model for answers and new folder summaries.",
    "api_key": "Stored in the OS keyring; replace through hidden input, never displayed.",
}


def editable_names() -> list[str]:
    return [*MODEL_HELP, *EngineOptions.model_fields]


def normalize_name(name: str) -> str:
    result = name.strip().lower().removeprefix("engine.")
    result = ALIASES.get(result, result)
    if result not in {*editable_names(), *READ_ONLY}:
        raise StateError(f"Unknown setting '{name}'. Run codelens config list.")
    return result


def description(name: str) -> str:
    if name in MODEL_HELP:
        return MODEL_HELP[name]
    return EngineOptions.model_fields[name].description or name.replace("_", " ").capitalize()


def current_value(settings: Settings, name: str):
    if name == "api_key":
        try:
            api_key(settings)
        except StateError:
            return "unavailable (run codelens config update api_key)"
        return "saved in OS keyring (hidden)"
    if name == "config_file":
        return str(home() / "config.json")
    if name in EngineOptions.model_fields:
        return getattr(settings.engine, name)
    return getattr(settings, name)


def rendered(value) -> str:
    return value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)


def parse_value(name: str, raw: str):
    if name in {"embedding_model", "reasoning_model"}:
        return raw.strip()
    try:
        return json.loads(raw)
    except ValueError:
        raise StateError("Use JSON: a number, true/false, an array, or an object.") from None


def changed_settings(current: Settings, changes: dict) -> Settings:
    values = current.model_dump()
    for name, value in changes.items():
        if name in EngineOptions.model_fields:
            values["engine"][name] = value
        elif name in MODEL_HELP and name != "api_key":
            values[name] = value
        else:
            raise StateError(f"{name} is read-only. Run codelens config list for editable names.")
    model = values["embedding_model"]
    if model != current.embedding_model and "embedding_dimensions" not in changes:
        values["embedding_dimensions"] = EMBEDDING_MODELS.get(model, current.embedding_dimensions)
    return Settings.model_validate(values)


def validation_message(error: ValidationError) -> str:
    return "; ".join(item["msg"] for item in error.errors(include_input=False))


def save_changes(changes: dict, *, key: str | None = None, expected: Settings | None = None):
    """Re-read under the lock; never overwrite unrelated edits made during a prompt."""
    with locked(home() / ".state.lock"):
        current = load_settings() or Settings()
        if expected is not None:
            watched = set(changes)
            if watched & {"embedding_model", "embedding_dimensions"}:
                watched |= {"embedding_model", "embedding_dimensions"}
            if any(
                current_value(current, name) != current_value(expected, name) for name in watched
            ):
                raise StateError(
                    "This setting changed in another command. Run config update again."
                )
        selected = changed_settings(current, changes)
        if key is not None:
            store_key(key)
        write_json(home() / "config.json", selected)
    return selected


def report_saved(changes: dict) -> None:
    console.print(f"Saved {home() / 'config.json'}", style="green")
    if set(changes) & {"embedding_model", "embedding_dimensions"}:
        console.print(
            "Existing graphs keep their model until you choose to rebuild on embed/ask/retrieve."
        )
    elif set(changes) & {
        "ignored_directories",
        "extra_ignored_directories",
        "ignored_directory_suffixes",
        "prose_doc_types",
        "max_file_size_bytes",
        "max_doc_file_size_bytes",
        "max_doc_chunks_per_file",
        "max_prose_chunks_per_file",
        "max_doc_chunk_chars",
        "max_embed_chars",
        "extract_interfaces",
        "max_interface_fanout",
        "max_mention_targets",
        "max_name_candidates",
        "generate_summaries",
        "min_entities_for_summary",
    }:
        console.print("Run codelens embed --force to apply indexing changes to unchanged files.")


def initialize() -> Settings:
    interactive.require_terminal()
    current = load_settings() or Settings()
    console.print(
        Panel(
            "API key > Embedding model > Reasoning model\n"
            "Enter keeps the current choice. Ctrl+C cancels setup.",
            title="CodeLens setup",
        )
    )
    # Gather all input and validate before changing credentials or the config file.
    while True:
        value = getpass.getpass("1/3 OpenAI API key (hidden; Enter keeps saved key): ").strip()
        if value:
            break
        try:
            api_key(current)
            break
        except StateError:
            console.print("No saved key is available; enter your OpenAI API key.", style="yellow")
    embedding = interactive.choose(
        "2/3 Embedding model", list(EMBEDDING_MODELS), current.embedding_model
    )
    while True:
        reasoning = interactive.choose(
            "3/3 Reasoning / answer model (also used for folder summaries)",
            REASONING_MODELS,
            current.reasoning_model,
            custom=True,
        )
        changes = {"embedding_model": embedding, "reasoning_model": reasoning}
        try:
            changed_settings(current, changes)
            break
        except ValidationError as error:
            console.print(validation_message(error), style="yellow")
    selected = save_changes(changes, key=value or None, expected=current)
    console.print(f"Saved {home() / 'config.json'}", style="green")
    console.print(
        f"Embedding: {selected.embedding_model} ({selected.embedding_dimensions} dimensions)\n"
        f"Reasoning: {selected.reasoning_model}\n"
        "All recommended settings are saved; existing tuning choices are preserved.\n"
        "Your API key is stored separately in the OS credential store.\n"
        "Run codelens config to see the commands, or codelens config list to see every setting.\n"
        "Next: codelens embed PATH"
    )
    if embedding != current.embedding_model:
        console.print("Existing graphs will ask you to keep their model or rebuild when used.")
    return selected


@app.callback(invoke_without_command=True)
def config_menu(ctx: typer.Context):
    """Manage the user configuration shared by all local projects."""
    if ctx.invoked_subcommand is None:
        console.print(f"Configuration: {home() / 'config.json'}")
        console.print("First-time setup: codelens init")
        console.print(ctx.get_help())


@app.command("list")
def list_settings(
    as_json: Annotated[bool, typer.Option("--json", help="Output a JSON catalog.")] = False,
):
    """List every editable name, its current value, and its purpose."""
    saved = load_settings()
    settings = saved or Settings()
    rows = [
        {"name": name, "value": current_value(settings, name), "description": description(name)}
        for name in editable_names()
    ]
    if as_json:
        print(
            json.dumps(
                {
                    "saved": saved is not None,
                    "config_file": str(home() / "config.json"),
                    "settings": rows,
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return
    console.print(
        f"{'Saved configuration' if saved else 'Recommended defaults (not saved)'}: {home() / 'config.json'}"
    )
    table = Table(title="CodeLens settings", show_lines=True)
    table.add_column("Name", style="cyan", overflow="fold")
    table.add_column("Current value", overflow="fold", max_width=40)
    table.add_column("Purpose", overflow="fold")
    for row in rows:
        preview = rendered(row["value"])
        if len(preview) > 64:
            preview = preview[:61] + "..."
        table.add_row(row["name"], preview, row["description"])
    console.print(table)
    console.print("Use config show NAME for the full value; config update NAME to edit it.")
    console.print(
        "schema_version, provider, credential_source, and config_file are read-only metadata."
    )


@app.command("show")
def show_settings(
    name: Annotated[str | None, typer.Argument(help="Optional setting name.")] = None,
):
    """Print saved configuration (or one value) as JSON; never reveal the key."""
    settings = load_settings()
    if settings is None:
        raise StateError(
            "Not configured yet. Run codelens init, or config list to preview defaults."
        )
    value = (
        current_value(settings, normalize_name(name))
        if name
        else {
            "config_file": str(home() / "config.json"),
            **settings.model_dump(),
            "api_key": current_value(settings, "api_key"),
        }
    )
    print(json.dumps(value, indent=2, ensure_ascii=False))


@app.command("update")
def update_setting(
    name: Annotated[
        str | None, typer.Argument(help="Name from config list; omit to choose interactively.")
    ] = None,
    value: Annotated[
        str | None, typer.Argument(help="Optional new value. Omit to edit the current value.")
    ] = None,
):
    """Edit a prefilled value; Enter saves, Backspace edits, Ctrl+U clears, Ctrl+C cancels."""
    if name is None:
        list_settings()
        name = interactive.edit("Setting name (Tab completes)", "", editable_names())
    name = normalize_name(name)
    if name in READ_ONLY:
        raise StateError(f"{name} is read-only metadata.")
    current = load_settings() or Settings()
    if name == "api_key":
        if value is not None:
            raise StateError(
                "Do not pass API keys as arguments. Use config update api_key for hidden input."
            )
        interactive.require_terminal()
        key = getpass.getpass("New OpenAI API key (hidden; Enter keeps saved key): ").strip()
        if not key:
            api_key(current)
            console.print("Kept the saved API key.")
            return
        save_changes({}, key=key)
        console.print("API key saved to the OS credential store.", style="green")
        return
    if value is None:
        console.print(description(name))
        console.print("Edit the prefilled value. Enter saves; Ctrl+U clears; Ctrl+C cancels.")
        choices = (
            list(EMBEDDING_MODELS)
            if name == "embedding_model"
            else (REASONING_MODELS if name == "reasoning_model" else None)
        )
        if choices:
            console.print("Models: " + ", ".join(choices))
        draft = rendered(current_value(current, name))
        while True:
            draft = interactive.edit(name, draft, choices)
            try:
                changes = {name: parse_value(name, draft)}
                changed_settings(current, changes)
                break
            except (StateError, ValidationError) as error:
                console.print(
                    validation_message(error) if isinstance(error, ValidationError) else str(error),
                    style="yellow",
                )
        save_changes(changes, expected=current)
    else:
        changes = {name: parse_value(name, value)}
        save_changes(changes)
    report_saved(changes)


@app.command("set")
def set_settings(
    embedding_model: Annotated[str | None, typer.Option(help="Embedding model ID.")] = None,
    dimensions: Annotated[int | None, typer.Option(min=1, help="Embedding dimensions.")] = None,
    reasoning_model: Annotated[str | None, typer.Option(help="Answer/summary model ID.")] = None,
    api_key_prompt: Annotated[
        bool, typer.Option("--api-key", help="Prompt for a hidden API key.")
    ] = False,
    api_key_stdin: Annotated[
        bool, typer.Option("--api-key-stdin", help="Read a key from a secret-manager pipe.")
    ] = False,
    option: Annotated[list[str] | None, typer.Option(help="Engine NAME=JSON; repeatable.")] = None,
):
    """Set several values at once (also preserves the original scripting interface)."""
    if api_key_prompt and api_key_stdin:
        raise StateError("Choose either --api-key or --api-key-stdin.")
    changes = {}
    for name, value in (
        ("embedding_model", embedding_model),
        ("embedding_dimensions", dimensions),
        ("reasoning_model", reasoning_model),
    ):
        if value is not None:
            changes[name] = value
    for item in option or []:
        name, separator, raw = item.partition("=")
        if not separator:
            raise StateError("Use --option NAME=JSON, for example generate_summaries=false.")
        name = normalize_name(name)
        if name not in EngineOptions.model_fields:
            raise StateError(
                "--option accepts engine settings; use config update for model settings."
            )
        changes[name] = parse_value(name, raw)
    changed_settings(load_settings() or Settings(), changes)  # validate before requesting a secret
    key = None
    if api_key_prompt:
        interactive.require_terminal()
        key = getpass.getpass("OpenAI API key (hidden): ")
    elif api_key_stdin:
        if sys.stdin.isatty():
            raise StateError("--api-key-stdin expects piped input. Use --api-key in a terminal.")
        key = sys.stdin.read(16385).strip()
        if len(key) > 16384 or "\n" in key or "\r" in key:
            raise StateError("Expected one API key on standard input.")
    if not changes and key is None:
        raise StateError("Select a setting to change. Run config update or config set --help.")
    save_changes(changes, key=key)
    report_saved(changes)
