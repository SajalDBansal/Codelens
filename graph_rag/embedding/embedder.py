"""
embedding/embedder.py
=====================

WHAT AN EMBEDDING IS
--------------------
An embedding model reads a piece of text and returns a list of numbers (1536
of them, for text-embedding-3-small). Texts that MEAN similar things get
similar lists. "Similar" is measured with cosine similarity, which is what
the local graph store computes over persisted vectors.

That is what lets someone ask "how does login work?" and get back a function
called `authenticate_user` - even though the words do not match at all.

THE MOST IMPORTANT IDEA IN THIS FILE
------------------------------------
We never embed bare code. We put a short "breadcrumb" in front of it first:

    File: src/auth/user_service.py
    Language: python
    Class: UserService
    Method: login
    Doc: Check the password and return a session token.

    def login(self, name, password):
        ...

Why this matters: a function body full of `if`, `return` and variable names
often does not say what it is FOR. The file path, the class it lives in and
the docstring do. Adding them measurably improves what search finds, and it
costs almost nothing.
"""

import time
from hashlib import sha256
from typing import Optional

from openai import OpenAI

from .. import config
from ..models import CodeEntity, DocChunk


def build_embedding_text(entity: CodeEntity) -> str:
    """
    Build the "breadcrumb + code" text that actually gets embedded.

    Kept as a plain function (not a method) so it is easy to call from a test
    and see exactly what would be sent.
    """
    lines = [
        f"File: {entity.file_path}",
        f"Language: {entity.language}",
    ]

    # The id looks like "path/to/file.py::UserService.login".
    # Everything before the last dot is the enclosing class, if any.
    qualified_name = entity.id.split("::")[-1]
    if "." in qualified_name:
        enclosing = qualified_name.rsplit(".", 1)[0]
        lines.append(f"Defined in: {enclosing}")

    # Capitalised kind reads better: "Method: login" rather than "method: login".
    lines.append(f"{entity.kind.capitalize()}: {entity.name}")

    if entity.docstring:
        # Docstrings can be long; the first few lines carry the summary.
        summary = " ".join(entity.docstring.split())[:400]
        lines.append(f"Doc: {summary}")

    breadcrumb = "\n".join(lines)

    # Truncate very long functions. Embedding models have a token limit, and
    # a 2000-line function is not going to embed meaningfully anyway.
    code = entity.code
    if len(code) > config.MAX_EMBED_CHARS:
        code = code[: config.MAX_EMBED_CHARS] + "\n... (truncated)"

    return f"{breadcrumb}\n\n{code}"


def build_doc_embedding_text(chunk: DocChunk) -> str:
    """
    The documentation version of build_embedding_text.

    Same breadcrumb idea, different breadcrumb. The heading trail is the
    valuable part here: a section titled just "Install" means nothing on its
    own, but "Setup > Install" tells the embedding model what is being
    installed. Without the trail, every "Install" section in every project
    would embed to roughly the same vector.
    """
    lines = [
        f"File: {chunk.file_path}",
        f"Document type: {chunk.doc_type}",
    ]

    if chunk.heading_path and chunk.heading_path != chunk.title:
        lines.append(f"Section: {chunk.heading_path}")
    else:
        lines.append(f"Section: {chunk.title}")

    breadcrumb = "\n".join(lines)

    text = chunk.text
    if len(text) > config.MAX_EMBED_CHARS:
        text = text[: config.MAX_EMBED_CHARS] + "\n... (truncated)"

    return f"{breadcrumb}\n\n{text}"


def embedding_fingerprint(text: str) -> str:
    """Hash the exact input and model settings, not just the function body."""
    value = f"{config.EMBEDDING_MODEL}\0{config.EMBEDDING_DIM}\0{text}"
    return sha256(value.encode("utf-8")).hexdigest()


def build_summary_embedding_text(summary) -> str:
    return f"Folder: {summary.path or '(repository root)'}\n\n{summary.text}"


def _is_retryable(error: Exception) -> bool:
    """
    Is waiting and trying again likely to help?

    Yes for rate limits (429), server errors (5xx) and connection problems.
    No for authentication and request errors - those are a misconfiguration,
    and the only useful response is to say so immediately.
    """
    status = getattr(error, "status_code", None)
    if status is None:
        response = getattr(error, "response", None)
        status = getattr(response, "status_code", None)

    if status is not None:
        return status == 429 or status >= 500

    # No status at all usually means the request never arrived - a DNS
    # failure, a dropped connection, a timeout. Those are worth a retry.
    return True


class Embedder:
    """
    A thin wrapper around the OpenAI embeddings endpoint.

    Two things it adds over calling the API directly:
      1. BATCHING - one request carries many texts, which is much faster and
         cheaper than one request per function.
      2. RETRIES - network calls fail sometimes. We wait and try again rather
         than losing a whole indexing run to one blip.
    """

    def __init__(self, api_key: Optional[str] = None, model: Optional[str] = None):
        # The OpenAI client reads the key from the environment by default,
        # but we pass it explicitly so all configuration flows through
        # config.py and there is only one place to look.
        self._client = OpenAI(api_key=api_key or config.OPENAI_API_KEY)
        self._model = model or config.EMBEDDING_MODEL

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        """
        Embed a list of texts. Returns one vector per input, in the same order.

        Order matters: the caller matches result[i] back to entity[i], so we
        must not reorder or drop anything.
        """
        if not texts:
            return []

        vectors: list[list[float]] = []

        for start in range(0, len(texts), config.EMBEDDING_BATCH_SIZE):
            batch = texts[start:start + config.EMBEDDING_BATCH_SIZE]
            vectors.extend(self._embed_one_batch(batch))

        return vectors

    def _embed_one_batch(self, batch: list[str], max_attempts: int = 4) -> list[list[float]]:
        """
        Send one batch, retrying with a growing wait if it fails.

        The growing wait is called *exponential backoff*: 1s, 2s, 4s. If the
        API is rate-limiting us, hammering it again immediately would just be
        rejected again; waiting longer each time lets it recover.
        """
        for attempt in range(max_attempts):
            try:
                options = {"dimensions": config.EMBEDDING_DIM} if self._model.startswith("text-embedding-3-") else {}
                response = self._client.embeddings.create(model=self._model, input=batch, **options)
                # The API guarantees the same order as the input, but it also
                # returns an explicit index, so we sort by it to be certain.
                items = sorted(response.data, key=lambda item: item.index)
                return [item.embedding for item in items]

            except Exception as error:  # noqa: BLE001 - inspected just below
                # Only RETRY what retrying can fix.
                #
                # This used to retry everything four times with backoff, so a
                # bad API key spent seven seconds failing four identical 401s
                # before reporting the problem. A 401, a 403 or a malformed
                # request will fail exactly the same way on every attempt;
                # only rate limits, timeouts and server errors are worth
                # waiting on.
                if not _is_retryable(error):
                    raise RuntimeError(
                        f"OpenAI embedding request failed and will not be "
                        f"retried: {error}"
                    ) from error

                is_last_attempt = attempt == max_attempts - 1
                if is_last_attempt:
                    raise RuntimeError(
                        f"OpenAI embedding request failed after {max_attempts} attempts: {error}"
                    ) from error
                wait_seconds = 2 ** attempt
                print(f"  embedding request failed ({error}); retrying in {wait_seconds}s")
                time.sleep(wait_seconds)

        return []  # unreachable, but keeps the type checker happy

    def embed_entities(self, entities: list[CodeEntity]) -> dict[str, list[float]]:
        """
        Embed a list of code entities. Returns {entity id: vector}.

        A dict keyed by id is what storage/ wants, and it removes any chance
        of a vector ending up attached to the wrong node.
        """
        if not entities:
            return {}

        texts = [build_embedding_text(entity) for entity in entities]
        vectors = self.embed_texts(texts)
        return {entity.id: vector for entity, vector in zip(entities, vectors)}

    def embed_docs(self, chunks: list[DocChunk]) -> dict[str, list[float]]:
        """
        Embed documentation chunks. Returns {chunk id: vector}.

        Uses the SAME model as the code embeddings on purpose. Both have to
        live in the same vector space, or a question could not be compared
        against code and prose with one embedding of itself.
        """
        if not chunks:
            return {}

        texts = [build_doc_embedding_text(chunk) for chunk in chunks]
        vectors = self.embed_texts(texts)
        return {chunk.id: vector for chunk, vector in zip(chunks, vectors)}

    def embed_summaries(self, summaries: list) -> dict[str, list[float]]:
        """
        Embed folder summaries. Returns {summary id: vector}.

        The SAME model again, for the same reason as docs: one question
        vector has to be comparable against code, prose and overviews alike,
        and that only holds if all four live in one vector space.
        """
        if not summaries:
            return {}

        texts = [build_summary_embedding_text(summary) for summary in summaries]
        vectors = self.embed_texts(texts)
        return {summary.id: vector for summary, vector in zip(summaries, vectors)}

    def embed_query(self, question: str) -> list[float]:
        """
        Embed a user's question so we can compare it against stored code.

        The question and the code go through the SAME model, which is what
        puts them in the same vector space and makes the comparison mean
        anything.
        """
        return self.embed_texts([question])[0]
