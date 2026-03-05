"""
generation/answerer.py
======================

The final LLM call.

THE PROMPT IS THE WHOLE DESIGN
------------------------------
Two instructions in the system prompt below do almost all the work:

  "Answer using ONLY the code context provided."
      Without this the model happily answers from its general knowledge of
      how login systems usually work, and produces a confident, plausible,
      WRONG description of your code. This one line is what makes the answer
      *grounded*.

  "If the context does not contain the answer, say so."
      Gives the model an acceptable way out. Without it, a model that cannot
      find the answer will invent one, because answering is what it is for.

Everything else is presentation.
"""

from typing import Optional

from openai import OpenAI
from .model_options import sampling_options

from .. import config
from ..retrieval import RetrievalResult


SYSTEM_PROMPT = """You are a precise code assistant answering questions about a specific codebase.

You will be given numbered blocks retrieved from that codebase. Each block header shows its file path, line numbers, and how it relates to the question (a direct match, or something reached from a match by a call/containment/inheritance/mentions edge).

Blocks are labelled CODE, DOCS or OVERVIEW:
- CODE is real source code. It is what the system actually does.
- DOCS is prose from a README, comment file, or config. It describes what the system is INTENDED to do, and it can be out of date.
- OVERVIEW is a generated summary of one folder. Use it to orient yourself and to describe how areas relate, but never cite it as evidence for a specific claim - quote CODE for that.

Rules:
1. Answer using ONLY the context provided. Do not use general knowledge about how such systems are usually built.
2. When CODE and DOCS disagree, trust the CODE and say plainly that the documentation does not match it. Never present a DOCS claim as verified behaviour.
3. If the context does not contain enough information to answer, say exactly that, and say what would need to be looked at. Never guess.
4. Cite what you rely on as `file_path:line`, e.g. `src/auth/user_service.py:42`.
5. When the answer involves several pieces, explain how they connect - the headers tell you which blocks call which, which docs describe which code, and which blocks share an HTTP route or environment variable across languages.
6. Two pieces of code are only related if a block header says so. Do not assume services talk to each other because it would be conventional for them to.
7. Be concise and concrete. Quote short snippets rather than restating whole functions."""


def build_user_prompt(question: str, context_text: str) -> str:
    """
    Assemble the message we send.

    Note the ORDER: context first, question last. Models attend well to the
    end of a prompt, so putting the question after the code keeps it from
    being lost in the middle of a large context block.
    """
    if not context_text:
        return (
            f"No code context could be retrieved from the graph.\n\n"
            f"Question: {question}\n\n"
            f"Reply that the codebase has not been indexed yet, or that nothing "
            f"relevant was found, and suggest running the indexer."
        )

    return (
        "Here is the relevant code and documentation retrieved from the codebase:\n\n"
        f"{context_text}\n\n"
        "-------------------------------------------\n"
        f"Question: {question}"
    )


def answer_question(
    result: RetrievalResult,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
) -> str:
    """
    Send the retrieved context plus the question to the LLM, return the answer.

    `temperature=0` asks the model to be as deterministic as it can. For
    factual questions about code that is what we want - creativity here just
    means inconsistency between runs.
    """
    client = OpenAI(api_key=api_key or config.OPENAI_API_KEY)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": build_user_prompt(result.question, result.context_text)},
    ]

    try:
        response = client.chat.completions.create(
            model=model or config.CHAT_MODEL,
            **sampling_options(model or config.CHAT_MODEL),
            messages=messages,
        )
    except Exception as error:  # noqa: BLE001 - inspected, then re-raised
        # Some newer models accept ONLY the default temperature and reject
        # `temperature=0` outright. Rather than making the choice of model a
        # trap, we notice that specific refusal and send the request again
        # without it. Every other failure is re-raised untouched.
        if "temperature" not in str(error):
            raise
        response = client.chat.completions.create(
            model=model or config.CHAT_MODEL,
            messages=messages,
        )

    return response.choices[0].message.content or "(the model returned an empty response)"
