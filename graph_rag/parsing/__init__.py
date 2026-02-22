"""
parsing/
========

"What is inside this file?"

Turns raw source text into structured `CodeEntity` objects (functions,
methods, classes) and `Relationship` objects (calls, containment,
inheritance), using tree-sitter.

  languages.py    The per-language lookup table. Every language names its
                  syntax nodes differently, so this is where we record that
                  "a function in Python is a `function_definition` node, but
                  in Rust it is a `function_item` node".

  code_parser.py  The actual walker for CODE. Language-agnostic: it reads
                  the rules out of languages.py and applies them.

  doc_parser.py   The walker for NON-CODE files (.md, .yaml, .json, .txt).
                  Same idea, different unit: it splits a document into
                  heading SECTIONS rather than functions, because that is
                  the piece of prose worth embedding on its own.

This folder does NOT know that LadybugDB or OpenAI exist. Give it a file path,
get back data. That makes it easy to test on its own.
"""

from .languages import (
    LanguageConfig,
    classify,
    detect_doc_type,
    detect_language,
    get_language_config,
    is_indexable,
    language_family,
)
from .code_parser import parse_file, prefer_by_scope, split_entity_id
from .doc_parser import parse_document

__all__ = [
    "LanguageConfig",
    "classify",
    "detect_doc_type",
    "detect_language",
    "get_language_config",
    "is_indexable",
    "language_family",
    "parse_file",
    "parse_document",
    "prefer_by_scope",
    "split_entity_id",
]
