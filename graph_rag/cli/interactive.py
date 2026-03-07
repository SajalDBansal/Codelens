"""Small terminal helpers; editable prompts never persist input history."""

import sys

from prompt_toolkit import prompt
from prompt_toolkit.completion import WordCompleter
from rich.console import Console
from rich.table import Table

from .state import StateError

console = Console(markup=False, highlight=False)


def require_terminal() -> None:
    if not sys.stdin.isatty():
        raise StateError(
            "Interactive input needs a terminal. Run codelens init in a terminal, "
            "or use codelens config update NAME VALUE. For credentials, pipe a key "
            "from your secret manager to codelens config set --api-key-stdin."
        )


def edit(label: str, current: str, choices: list[str] | None = None) -> str:
    """Put the current value in the input buffer, with the cursor at the end."""
    require_terminal()
    return prompt(
        f"{label}: ",
        default=current,
        completer=WordCompleter(choices, sentence=True) if choices else None,
        complete_while_typing=False,
    ).strip()


def choose(label: str, options: list[str], current: str, *, custom: bool = False) -> str:
    table = Table(title=label, show_header=False, box=None)
    for index, item in enumerate(options, 1):
        table.add_row(str(index), item, "current" if item == current else "")
    console.print(table)
    while True:
        value = input(f"Choose a number or model ID [Enter: {current}]: ").strip()
        if not value:
            return current
        if value.isdigit() and 1 <= int(value) <= len(options):
            return options[int(value) - 1]
        if value in options or (custom and not value.isdigit()):
            return value
        console.print("Select one of the listed models.", style="yellow")
