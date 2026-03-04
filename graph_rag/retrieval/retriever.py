"""
retrieval/retriever.py
======================

THE STEPS OF ANSWERING A QUESTION (this file does all but the last)

    1. EMBED     turn the question into a vector, using the same model that
                 embedded the code AND the docs - that is what makes all
                 three comparable.

    2. SEED      two separate vector searches:
                   - the code index, for the functions closest to the question
                   - the doc index, for the README/config sections closest
                 They are separate so that prose and code cannot crowd each
                 other out of a single ranking.

    3. EXPAND    walk outwards from those seeds:
                   - code -> code   along CALLS / CONTAINS / EXTENDS
                   - code -> code   along REFERENCES, through a shared route
                                    or env var - the ONLY hop that crosses a
                                    language boundary
                   - code -> docs   along MENTIONS, backwards
                   - docs -> code   along MENTIONS, forwards

    4. RANK      score every neighbour against the question and by the kind
                 of edge that reached it, so the budget buys relevance
                 rather than mere proximity.

    5. ASSEMBLE  stack it all into one text block, seeds first, cutting the
                 least useful material when we run out of room.

    (6. GENERATE happens in generation/ - the actual LLM call.)

WHY STEP 3'S MENTIONS EDGE MATTERS
----------------------------------
Ordinary RAG would find the README section only if the README's own wording
happened to match the question. Here, matching a FUNCTION also pulls in the
documentation written about that function, and matching a doc section pulls
in the code it describes. The link is structural, not textual - which is the
entire point of doing this in a graph.

WHY WE CUT THE LEAST USEFUL MATERIAL FIRST
------------------------------------------
There is a hard limit on how much text fits in the prompt. When we hit it we
have to drop something. The seeds are what the question was actually about,
so they stay.

Among neighbours the order used to be hop count alone - a graph distance,
which says nothing about whether a block helps answer the question. So a
class's twenty sibling methods, all one hop away, ranked exactly level with
the single function the question was really about, and the budget went to
whichever the database listed first. Now each neighbour carries its own
similarity to the question and the weight of the edge that reached it, and
the weakest go first. Dropping context always beats failing the request.
"""

from dataclasses import dataclass, field

from .. import config
from ..models import RetrievedDoc, RetrievedEntity, RetrievedSummary
from ..embedding import Embedder
from ..storage import GraphStore
from ..storage.graph_store import reciprocal_rank_fusion


@dataclass
class RetrievalResult:
    """What retrieval found, plus the text we will paste into the prompt."""

    question: str

    # Code blocks, best first.
    items: list[RetrievedEntity] = field(default_factory=list)

    # Documentation sections, best first.
    docs: list[RetrievedDoc] = field(default_factory=list)

    # Folder overviews. These answer the SHAPE of question the other two
    # cannot: "what is all this?" rather than "where is X?".
    summaries: list[RetrievedSummary] = field(default_factory=list)

    context_text: str = ""

    # Set when we had to drop material to stay inside the budget. Worth
    # showing the user: it usually means the question was too broad.
    dropped_count: int = 0


def _format_entity(item: RetrievedEntity, index: int) -> str:
    """
    Render one code block for the prompt.

    The header matters as much as the code. It gives the LLM the file and
    line numbers it needs in order to CITE its answer, and the `reason` line
    tells it how this block relates to the question - so it can say "changing
    X would affect Y" instead of just describing code in isolation.
    """
    entity = item.entity
    header_lines = [
        f"--- [{index}] CODE  {entity.file_path}:{entity.start_line}-{entity.end_line} ---",
        f"{entity.kind} {entity.name}  (language: {entity.language})",
    ]

    if item.hop == 0:
        header_lines.append(f"relevance: direct match (similarity {item.score:.3f})")
    else:
        header_lines.append(f"relevance: {item.hop} hop away - {item.reason}")

    if entity.docstring:
        summary = " ".join(entity.docstring.split())[:300]
        header_lines.append(f"doc: {summary}")

    return "\n".join(header_lines) + "\n\n" + _truncate_code(entity.code) + "\n"


def _format_doc(item: RetrievedDoc, index: int) -> str:
    """
    Render one documentation section for the prompt.

    Labelled DOCS rather than CODE so the model knows it is reading prose
    that DESCRIBES the system, not the system itself. Without that label it
    can quote a README's aspirational description as though it were the
    implementation.
    """
    chunk = item.chunk
    header_lines = [
        f"--- [{index}] DOCS  {chunk.file_path}:{chunk.start_line}-{chunk.end_line} ---",
        f"section: {chunk.heading_path}",
    ]

    if item.hop == 0:
        header_lines.append(f"relevance: direct match (similarity {item.score:.3f})")
    else:
        header_lines.append(f"relevance: {item.reason}")

    return "\n".join(header_lines) + "\n\n" + _truncate_code(chunk.text) + "\n"


def _is_module(item) -> bool:
    """Is this retrieved item a whole-file `<module>` node?"""
    return getattr(item, "kind", None) == "module"


def _damp_module_hits(keyword_hits):
    """
    Push `<module>` nodes down the KEYWORD ranking.

    A module node holds everything at the top of a file - imports, constants,
    wiring - so it is a bag of many unrelated tokens and matches almost any
    query that touches that file. It wins keyword rankings on sheer token
    count rather than on relevance: measured on the demo repository,
    `api.ts::<module>` scored 1.571 against a question about formatting
    prices, more than double any genuine answer, and then dragged its whole
    file in behind it through CONTAINS.

    This is a penalty rather than an exclusion on purpose. "What is in this
    file?" is a real question and the module node is the right answer to it -
    it just should not outrank a function that actually does the thing.
    """
    if config.MODULE_KEYWORD_PENALTY >= 1.0 or not keyword_hits:
        return keyword_hits

    adjusted = [
        (item, score * config.MODULE_KEYWORD_PENALTY if _is_module(item) else score)
        for item, score in keyword_hits
    ]
    adjusted.sort(key=lambda pair: pair[1], reverse=True)
    return adjusted


def _format_summary(item: RetrievedSummary, index: int) -> str:
    """
    Render one folder overview for the prompt.

    Labelled OVERVIEW rather than CODE or DOCS because it is neither: it is a
    description this system generated from the code, so it is a good map and
    a bad citation. The system prompt tells the model to navigate with it and
    to quote CODE for anything it actually asserts.
    """
    path = item.summary.path or "(repository root)"
    header = [
        f"--- [{index}] OVERVIEW  {path}/ ---",
        f"generated summary of {item.summary.entity_count} definitions "
        f"(similarity {item.score:.3f})",
    ]
    return "\n".join(header) + "\n\n" + item.summary.text + "\n"


def _hybrid(vector_hits, keyword_hits, key, top_k: int):
    """
    Fuse a vector ranking and a keyword ranking into one list of `top_k`.

    Returns [(item, score), ...] where score is the cosine similarity where we
    have one, best first.

    The two searches produce numbers that cannot be compared: cosine
    similarity is bounded 0..1, a BM25 score is not bounded at all. So we
    fuse on RANK rather than on score - see
    storage.graph_store.reciprocal_rank_fusion.

    A hit found by BOTH searches ends up above one found by either alone,
    which is usually what we want: agreement between "means this" and "says
    this" is a strong signal.

    BUT AGREEMENT MUST NOT BE ABLE TO OVERRULE CERTAINTY
    ----------------------------------------------------
    RRF keeps ranks and discards scores, so it also discards how CONFIDENT
    each search was. With a large k and short lists, the distance between
    rank 1 and rank 15 is worth far less than appearing in both lists at all.
    In a small or single-domain codebase nearly every keyword hit is also in
    the vector top-N, so agreement becomes nearly free and stops carrying any
    information - while still outvoting position five to one.

    The failure that came from this was silent and total. Asked "how do I
    format a price for display?", the vector search put the correct answer
    first with similarity 0.767; fusion ranked it sixth, one slot past
    top_k=5, and it never reached the model. The five items that displaced it
    were weaker matches whose only merit was appearing in both lists.

    So the top few vector hits are reserved a seat before fusion is consulted.
    They still have to clear the similarity floors below - this is a tie-break
    rule, not a licence to return rubbish when nothing matched.
    """
    keyword_hits = _damp_module_hits(keyword_hits)

    vector_ranking = [key(item) for item, _ in vector_hits]
    keyword_ranking = [key(item) for item, _ in keyword_hits]

    fused = reciprocal_rank_fusion(
        [vector_ranking, keyword_ranking],
        weights=[config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT],
    )

    # Keep the original cosine similarity where we have one, because it is
    # the number a human can actually interpret, and it is what MIN_SIMILARITY
    # is expressed in.
    similarity = {key(item): score for item, score in vector_hits}
    items = {key(item): item for item, _ in vector_hits}
    items.update({key(item): item for item, _ in keyword_hits})

    ordered = [identifier for identifier, _ in
               sorted(fused.items(), key=lambda pair: pair[1], reverse=True)]

    # The reserved slots: the strongest vector hits, in their own order,
    # ahead of whatever fusion preferred.
    reserved = [key(item) for item, _ in
                vector_hits[: max(0, config.HYBRID_RESERVED_VECTOR_SLOTS)]]
    seen = set(reserved)
    ordered = reserved + [i for i in ordered if i not in seen]

    # TWO floors, and they do different jobs.
    #
    # MIN_SIMILARITY is absolute: it is what makes "nothing here answers your
    # question" possible, and it has to be calibrated per repository.
    #
    # The relative floor needs no calibration, because it is measured against
    # this question's OWN best answer: once we have found something clearly
    # good, anything far below it is padding that dilutes the context rather
    # than adding to it. A keyword hit carries no cosine score and is never
    # filtered by either - it is kept on the strength of matching literally.
    best_similarity = max(similarity.values(), default=None)
    relative_floor = None
    if best_similarity is not None and config.RELATIVE_SIMILARITY_MARGIN > 0:
        relative_floor = best_similarity - config.RELATIVE_SIMILARITY_MARGIN

    results = []
    for identifier in ordered:
        score = similarity.get(identifier)
        if score is not None:
            if score < config.MIN_SIMILARITY:
                continue
            if relative_floor is not None and score < relative_floor:
                continue
        results.append((items[identifier], score if score is not None else 0.0))
        if len(results) >= top_k:
            break
    return results


def _truncate_code(text: str) -> str:
    """Cap one block so a single huge entity cannot eat the whole budget."""
    if len(text) <= config.MAX_BLOCK_CHARS:
        return text
    return text[: config.MAX_BLOCK_CHARS] + "\n... (block truncated)"


def retrieve(
    question: str,
    store: GraphStore,
    top_k: int = config.DEFAULT_TOP_K,
    hops: int = config.DEFAULT_HOPS,
    doc_top_k: int = config.DEFAULT_DOC_TOP_K,
    max_chars: int = config.MAX_CONTEXT_CHARS,
    summary_top_k: int = config.DEFAULT_SUMMARY_TOP_K,
) -> RetrievalResult:
    """
    Find the code and documentation relevant to `question`.

    `store` is passed in rather than created here so the caller controls the
    connection's lifetime (open it once, use it for several questions).
    """
    result = RetrievalResult(question=question)

    # --- STEP 1: embed the question -----------------------------------
    embedder = Embedder()
    question_vector = embedder.embed_query(question)

    # --- STEP 2: hybrid search, per index -----------------------------
    # Each index is searched TWICE - once by meaning (the embedding) and once
    # by keyword (the local BM25 index) - and the two rankings are fused.
    # See storage/schema.py for why one of them alone is not enough.
    code_seeds = _hybrid(
        vector_hits=store.vector_search(question_vector, top_k=top_k * 3),
        keyword_hits=store.fulltext_search(question, top_k=top_k * 3),
        key=lambda entity: entity.id,
        top_k=top_k,
    )
    doc_seeds = _hybrid(
        vector_hits=store.doc_vector_search(question_vector, top_k=doc_top_k * 3),
        keyword_hits=store.doc_fulltext_search(question, top_k=doc_top_k * 3),
        key=lambda chunk: chunk.id,
        top_k=doc_top_k,
    ) if doc_top_k else []

    # Folder overviews. A third, separate search for the same reason docs got
    # a second one: a broad question and a specific one should not compete for
    # the same slots. Vector-only - a generated overview has no identifier in
    # it worth matching literally.
    summary_hits = store.summary_vector_search(question_vector, top_k=summary_top_k) \
        if summary_top_k else []
    result.summaries = [
        RetrievedSummary(summary=summary, score=score, reason="overview of this area")
        for summary, score in summary_hits
        if score >= config.MIN_SIMILARITY
    ]

    if not code_seeds and not doc_seeds:
        # Almost always means the graph is empty or nothing was embedded.
        return result

    seed_items = [
        RetrievedEntity(entity=entity, hop=0, score=score, reason="matched the question")
        for entity, score in code_seeds
    ]
    seed_docs = [
        RetrievedDoc(chunk=chunk, hop=0, score=score, reason="matched the question")
        for chunk, score in doc_seeds
    ]

    # --- STEP 3: expand outwards along the edges ----------------------
    seed_ids = [item.entity.id for item in seed_items]
    doc_seed_ids = [item.chunk.id for item in seed_docs]

    # 3a. code -> code, the original graph walk.
    # Ask for a generous number of neighbours; the character budget below is
    # what really decides how many survive.
    raw_neighbours = store.expand_neighbours(
        seed_ids, hops=hops, limit=top_k * 8, query_embedding=question_vector
    )
    neighbour_items = [
        RetrievedEntity(
            entity=n["entity"],
            hop=n["hop"],
            score=n["relevance"],
            reason=n["reason"],
        )
        for n in raw_neighbours
    ]
    # How useful each neighbour looks, as opposed to how near it is.
    #
    # A neighbour's rank now combines three things: how close it is to the
    # question (a real cosine score, computed in the database against the
    # same question vector the seeds used), what KIND of edge reached it, and
    # how many hops away it is. Before, only the last of those was consulted,
    # which meant the context budget was spent on topology - a seed's twenty
    # siblings outrank the one function that answers the question purely
    # because they were returned first.
    #
    # A fourth thing now counts: how much the EDGE ITSELF is trusted. Not
    # every link is equally certain - one resolved because exactly one
    # definition in the repository answered to the name is stronger than one
    # settled by reading a parameter annotation, which is stronger again than
    # one settled only by counting arguments. Those weaker edges exist
    # because refusing to create them cost real connectivity (an entire auth
    # module fell out of the graph), but they should lose to a certain edge
    # when the character budget is tight rather than displace it.
    neighbour_rank = {
        id(item): (
            config.NEIGHBOUR_RELEVANCE_WEIGHT * item.score
            * config.EDGE_TYPE_WEIGHTS.get(n["rel_type"], 0.5)
            * n.get("confidence", 1.0)
            - 0.15 * item.hop
        )
        for item, n in zip(neighbour_items, raw_neighbours)
    }

    # Ids we already have, so expansion never returns a duplicate.
    known_entity_ids = set(seed_ids) | {item.entity.id for item in neighbour_items}

    # 3b. docs -> code. If the question matched a README section, pull in the
    # code that section actually talks about, so the answer can quote real
    # implementation rather than repeating the documentation back.
    for entity, reason in store.entities_mentioned_by(doc_seed_ids, limit=top_k * 2):
        if entity.id not in known_entity_ids:
            known_entity_ids.add(entity.id)
            neighbour_items.append(RetrievedEntity(entity=entity, hop=1, reason=reason))

    # 3b-2. code -> code ACROSS a language boundary, through a shared route
    # or environment variable. This is the only hop in the system that can
    # leave the language it started in: a TypeScript client that fetches
    # "/api/cart" and the Python handler that serves it are connected here
    # and nowhere else. Without it, "how do these services fit together?" had
    # no edges to read and the model answered by inventing an architecture.
    for shared in store.entities_sharing_interfaces(seed_ids, limit=top_k * 2):
        entity = shared["entity"]
        if entity.id not in known_entity_ids:
            known_entity_ids.add(entity.id)
            neighbour_items.append(
                RetrievedEntity(entity=entity, hop=1, reason=shared["reason"])
            )

    # 3c. code -> docs. The reverse: we matched a function, so pull in the
    # documentation written about it. Plain RAG cannot do this at all.
    known_doc_ids = set(doc_seed_ids)
    neighbour_docs: list[RetrievedDoc] = []
    for chunk, reason in store.docs_mentioning(list(known_entity_ids), limit=doc_top_k * 3):
        if chunk.id not in known_doc_ids:
            known_doc_ids.add(chunk.id)
            neighbour_docs.append(RetrievedDoc(chunk=chunk, hop=1, reason=reason))

    # --- STEP 4: assemble within the budget ---------------------------
    # Order: seeds first (both kinds), then everything reached by an edge.
    seed_items.sort(key=lambda item: item.score, reverse=True)
    seed_docs.sort(key=lambda item: item.score, reverse=True)
    # Best-looking neighbours first, so that when the character budget runs
    # out it is the least useful material that gets dropped rather than
    # whatever happened to be furthest down the result set.
    neighbour_items.sort(key=lambda item: neighbour_rank.get(id(item), 0.0), reverse=True)

    # Each entry is (sort_key, kind, item). Seeds are interleaved code-first
    # so that when the budget is tight the code still gets in.
    # Overviews go FIRST and are small. They are the map the model reads
    # before the territory: a couple of hundred characters that say what part
    # of the system this is, which measurably changes how it interprets the
    # code blocks underneath.
    ordered: list[tuple[str, object]] = (
        [("summary", item) for item in result.summaries]
        + [("code", item) for item in seed_items]
        + [("doc", item) for item in seed_docs]
        + [("code", item) for item in neighbour_items]
        + [("doc", item) for item in neighbour_docs]
    )

    blocks: list[str] = []
    used_chars = 0
    kept_summaries: list[RetrievedSummary] = []

    for kind, item in ordered:
        index = len(blocks) + 1
        if kind == "code":
            block = _format_entity(item, index)
        elif kind == "doc":
            block = _format_doc(item, index)
        else:
            block = _format_summary(item, index)

        if used_chars + len(block) > max_chars:
            # This block does not fit - so SKIP IT and try the next one.
            #
            # This used to `break`. That made one oversized block discard
            # every block behind it, however relevant: a single 50 KB entity
            # ranked first returned a completely EMPTY context, and the model
            # was asked the question with nothing to answer from. Blocks are
            # roughly ordered by relevance, not strictly, and a later one that
            # fits is always better than nothing.
            result.dropped_count += 1
            continue

        blocks.append(block)
        used_chars += len(block)

        if kind == "code":
            result.items.append(item)
        elif kind == "doc":
            result.docs.append(item)
        else:
            kept_summaries.append(item)

    # Only the overviews that actually made it into the prompt.
    result.summaries = kept_summaries
    result.context_text = "\n".join(blocks)
    return result
