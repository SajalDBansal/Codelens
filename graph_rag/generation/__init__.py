"""
generation/
===========

"Write the answer."

The last step: take the code that retrieval/ found and ask the LLM a
question about it.

  answerer.py   Builds the prompt and calls the OpenAI chat API.

This folder is the ONLY place that calls the chat endpoint. Note there is no
framework here - no LangChain, no agent loop. It is a string, a request, and
a response, which is exactly as complicated as this needs to be.
"""

from .answerer import answer_question

__all__ = ["answer_question"]
