"""
graph_rag
=========

A graph-based RAG (Retrieval Augmented Generation) system for source code.

------------------------------------------------------------------------
THE TWO ENTRY POINTS
------------------------------------------------------------------------

  graph-rag-index   Reads a codebase -> parses it -> stores the structure as
                    a graph in SQLite -> creates embeddings.
                    (the "build the knowledge" command)

  graph-rag-ask     Takes a question -> finds relevant code in the graph ->
                    sends it to the LLM -> prints a grounded answer.
                    (the "use the knowledge" command)

------------------------------------------------------------------------
THE FOLDERS, IN THE ORDER DATA FLOWS THROUGH THEM
------------------------------------------------------------------------

  config.py           Built-in defaults; the CLI applies saved config.json settings.
  models.py           The shapes of our data (CodeEntity, Relationship, ...).

  change_detection/   "What changed since last time?"  Merkle hash tree.
  parsing/            "What is in this file?"          tree-sitter ->
                      functions and classes for code, heading SECTIONS for
                      documentation (.md, .yaml, .json, ...).
  embedding/          "What does this mean?"           text -> vector (OpenAI).
  storage/            "Where does it all live?"        every SQLite query.
  pipeline/           "Run the whole indexing job."    orchestrates the above.

  retrieval/          "Find the code that answers this question."
  generation/         "Write the answer."              the final OpenAI call.

  cli/                The two commands a human actually types.

The rule that keeps this understandable: a folder may import from folders
ABOVE it in that list, never from folders below it. So `parsing` never
imports `storage`, and `storage` never imports `pipeline`. This is why you
can read any single folder without having read the rest of the project.
"""

__version__ = "0.1.0"
