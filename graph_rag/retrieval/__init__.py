"""
retrieval/
==========

"Find the code that answers this question."

This is the half of the system that runs when someone ASKS something, and it
is where the graph earns its keep:

    vector search   finds a few good starting points   (fuzzy, imprecise)
    graph traversal gathers everything connected       (exact, precise)

Neither is enough alone. Vector search alone is plain RAG: it hands you a
function that looks relevant but not its callers, so the LLM cannot tell you
what a change would break. Graph traversal alone has no way to pick a
starting point from an English question.

It searches CODE and DOCUMENTATION as two separate vector indexes, then
crosses between them along MENTIONS edges - so matching a function also pulls
in the README section about it, and matching a README section pulls in the
code it describes.

  retriever.py   Runs all of that, then assembles the result into a context
                 block that fits inside a token budget.
"""

from .retriever import RetrievalResult, retrieve

__all__ = ["RetrievalResult", "retrieve"]
