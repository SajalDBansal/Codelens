"""
embedding/
==========

"What does this code mean?"

Turns a piece of code into an *embedding*: a long list of numbers that
captures its meaning. Two pieces of code that do similar things end up with
similar numbers, which is what makes "search by meaning" possible.

  embedder.py   Builds the text we embed (code plus a context breadcrumb)
                and calls the OpenAI embeddings API.

This folder is the only place that calls the OpenAI embeddings endpoint.
If you ever want to switch to a local model instead, this is the one file
you would change.
"""

from .embedder import Embedder, build_doc_embedding_text, build_embedding_text

__all__ = ["Embedder", "build_doc_embedding_text", "build_embedding_text"]
