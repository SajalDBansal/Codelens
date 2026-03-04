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
    items: list[RetrievedEntity] = field(default_factory=list)
    docs: list[RetrievedDoc] = field(default_factory=list)
    summaries: list[RetrievedSummary] = field(default_factory=list)
    context_text: str = ''
    dropped_count: int = 0

def _is_module(item) -> bool:
    """Is this retrieved item a whole-file `<module>` node?"""
    return getattr(item, 'kind', None) == 'module'

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
    adjusted = [(item, score * config.MODULE_KEYWORD_PENALTY if _is_module(item) else score) for item, score in keyword_hits]
    adjusted.sort(key=lambda pair: pair[1], reverse=True)
    return adjusted

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
    fused = reciprocal_rank_fusion([vector_ranking, keyword_ranking], weights=[config.HYBRID_VECTOR_WEIGHT, config.HYBRID_KEYWORD_WEIGHT])
    similarity = {key(item): score for item, score in vector_hits}
    items = {key(item): item for item, _ in vector_hits}
    items.update({key(item): item for item, _ in keyword_hits})
    ordered = [identifier for identifier, _ in sorted(fused.items(), key=lambda pair: pair[1], reverse=True)]
    reserved = [key(item) for item, _ in vector_hits[:max(0, config.HYBRID_RESERVED_VECTOR_SLOTS)]]
    seen = set(reserved)
    ordered = reserved + [i for i in ordered if i not in seen]
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
