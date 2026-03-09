"""
cli/
====

The two commands a human actually types. THIS IS WHERE THE PROGRAM STARTS.

  index_command.py    graph-rag-index  -> build the graph and the embeddings
  ask_command.py      graph-rag-ask    -> ask a question about the code

Each file has one `main()` function, and pyproject.toml points a console
command at it. That is how typing `graph-rag-index` in a terminal ends up
running Python code.

These files do NOTHING except read command line arguments, call into
pipeline/ or retrieval/ + generation/, and print the result. All the real
logic lives in those folders, which keeps it testable without pretending to
type commands.
"""

def index_main() -> None:
    """Load the index command only when invoked."""
    from .index_command import main

    main()


def ask_main() -> None:
    """Load the ask command only when invoked."""
    from .ask_command import main

    main()

__all__ = ["index_main", "ask_main"]
