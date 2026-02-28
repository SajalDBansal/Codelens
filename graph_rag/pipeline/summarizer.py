"""
pipeline/summarizer.py
======================

"What is this folder FOR?"

WHY THIS FILE EXISTS
--------------------
Retrieval in this project is entirely LOCAL. Every answer starts from a point
match - a function, a doc section - and walks outwards along edges. That is
exactly right for "where is the session token created?", and it is useless
for "what are all the services here and how do they fit together?".

The reason is structural, not a tuning problem. Nothing in the graph
represents "the auth subsystem" or "this repository". A question whose answer
is a SYNTHESIS across many nodes has nothing to match against, so vector
search returns a handful of arbitrary local hits and the model fills the gap
by guessing. Asked what its services were, the demo repository produced a
confident architecture summary - "loosely coupled, interact via well-defined
interfaces" - that was not supported by a single edge in the graph.

So we give those questions something real to retrieve: one short summary per
folder, written at index time and embedded like anything else.

WHY IT IS AFFORDABLE
--------------------
Two decisions keep the cost down to something you would not notice:

  1. The summary is built from NAMES, KINDS AND DOCSTRINGS - never from
     bodies. That keeps the prompt small (a few hundred tokens for a folder
     that holds thousands of lines) and it keeps the summary stable, because
     editing the inside of a function does not change it.

  2. It is regenerated only when the folder's MERKLE HASH changes. The change
     detection that already tells us which files to re-embed tells us exactly
     which folders to re-summarise, so a run that touched one file rewrites
     one or two summaries, not the whole tree.

This is the piece that makes the system answer onboarding questions - "what
does this repo do", "where would I add a payment provider" - with evidence
rather than plausible invention.
"""

from typing import Optional

from openai import OpenAI
from ..generation.model_options import sampling_options

from .. import config
from ..models import CodeEntity, FolderSummary

SUMMARY_SYSTEM_PROMPT = """You summarise one folder of a codebase for a search index.

You are given the folder's path and an outline of what it contains: file names, and the functions, classes and constants declared in them with their documentation.

Write 2-4 sentences of plain prose that answer:
  - what this folder is responsible for,
  - the main things it defines and what they do,
  - anything notable about how it fits with the rest of the system.

Rules:
- Describe only what the outline shows. Never invent behaviour, dependencies or integrations that are not listed.
- Prefer the concrete names from the outline over vague words, so the text matches when someone searches for them.
- No headings, no bullet points, no preamble. Just the prose."""


def build_summary_prompt(folder_path: str, entities: list[CodeEntity]) -> str:
    """
    Build the outline we send for one folder.

    Names, kinds and docstrings only. A function's BODY is deliberately left
    out: it would multiply the token cost many times over, and it would also
    make the summary churn on every edit, defeating the hash check that keeps
    regeneration rare.
    """
    label = folder_path or "(repository root)"

    by_file: dict[str, list[CodeEntity]] = {}
    for entity in entities:
        by_file.setdefault(entity.file_path, []).append(entity)

    lines = [f"Folder: {label}", ""]
    for file_path in sorted(by_file):
        lines.append(f"{file_path}")
        for entity in sorted(by_file[file_path], key=lambda e: e.start_line):
            if entity.kind == "module":
                continue
            line = f"  {entity.kind} {entity.name}"
            if entity.docstring:
                summary = " ".join(entity.docstring.split())[:160]
                line += f" - {summary}"
            lines.append(line)

    return "\n".join(lines)


class Summarizer:
    """
    Turns a folder's outline into a short description, via the chat model.

    Kept as a class with the client on it so one indexing run reuses a single
    HTTP connection pool rather than building one per folder.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        self._client = OpenAI(api_key=api_key or config.OPENAI_API_KEY)
        self._model = model or config.CHAT_MODEL

    def summarise(
        self, folder_path: str, entities: list[CodeEntity], folder_hash: str
    ) -> Optional[FolderSummary]:
        """
        Write one folder's summary, or None if the call fails.

        A failure here must never take down an indexing run. Summaries are an
        enhancement: without one, a broad question is answered exactly as
        badly as it was before, while every specific question still works.
        Losing a whole index because a summary call was rate-limited would be
        a far worse trade.
        """
        real = [e for e in entities if e.kind != "module"]
        if len(real) < config.MIN_ENTITIES_FOR_SUMMARY:
            return None

        prompt = build_summary_prompt(folder_path, entities)

        try:
            response = self._client.chat.completions.create(
                model=self._model,
                **sampling_options(self._model),
                messages=[
                    {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
            )
            text = (response.choices[0].message.content or "").strip()
        except Exception as error:  # noqa: BLE001 - summaries are optional
            # Some models reject temperature=0; retry once without it, then
            # give up quietly rather than failing the run.
            if "temperature" not in str(error):
                return None
            try:
                response = self._client.chat.completions.create(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                )
                text = (response.choices[0].message.content or "").strip()
            except Exception:  # noqa: BLE001
                return None

        if not text:
            return None

        return FolderSummary(
            id=f"summary::{folder_path}",
            path=folder_path,
            text=text,
            entity_count=len(real),
            # The folder's Merkle hash IS the content hash: if it has not
            # changed, nothing inside the folder has changed at any depth, so
            # the summary is still accurate and must not be regenerated.
            content_hash=folder_hash,
        )
