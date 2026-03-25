import re

from .. import config

# Words that carry no signal in a code search and only dilute the ranking.
_STOP_WORDS = frozenset(
    {
        "the",
        "a",
        "an",
        "is",
        "are",
        "was",
        "were",
        "be",
        "been",
        "being",
        "do",
        "does",
        "did",
        "how",
        "what",
        "where",
        "when",
        "why",
        "which",
        "who",
        "whom",
        "this",
        "that",
        "these",
        "those",
        "of",
        "in",
        "on",
        "at",
        "to",
        "for",
        "with",
        "from",
        "by",
        "and",
        "or",
        "not",
        "it",
        "its",
        "i",
        "we",
        "you",
        "can",
        "could",
        "should",
        "would",
        "will",
        "shall",
        "get",
        "show",
        "me",
        "my",
        "our",
        "please",
        "tell",
        "about",
    }
)


def reciprocal_rank_fusion(
    rankings: list[list[str]],
    k: int | None = None,
    weights: list[float] | None = None,
) -> dict[str, float]:
    """
    Merge several ranked lists of ids into one score per id.

    Reciprocal Rank Fusion is the standard way to combine a vector search
    with a keyword search, and it is used here because the two produce
    scores that are NOT comparable: cosine similarity lives in 0..1 while a
    BM25 score is unbounded and depends on corpus statistics. Normalising
    them against each other would be guesswork.

    RRF sidesteps that entirely by throwing the scores away and keeping only
    the RANKS: an item scores 1/(k + rank) in each list it appears in, and
    the scores are added. So an item both searches rank highly beats one that
    only a single search liked, whatever the raw numbers said.

    `k` damps the difference between the top few positions so that rank 1
    does not overwhelm rank 3. The original paper's value of 60 was tuned for
    TREC runs a THOUSAND results deep; on the ~15-item lists fused here it
    flattens rank 1 and rank 15 to within 19% of each other, while merely
    appearing in both lists is worth about 100%. That is how a rank-1 hit
    gets outvoted by weaker agreement, so the default now comes from
    config.RRF_K, which is much smaller.

    `weights` scales each list's contribution, so a project whose questions
    usually name identifiers can lean on the keyword ranking without
    abandoning the vector one. Defaults to equal weight.
    """
    if weights is None:
        weights = [1.0] * len(rankings)
    if k is None:
        k = config.RRF_K

    scores: dict[str, float] = {}
    for ranking, weight in zip(rankings, weights):
        for position, identifier in enumerate(ranking):
            scores[identifier] = scores.get(identifier, 0.0) + weight / (k + position + 1)
    return scores


def _describe_connection(rel_type: str, outgoing: bool, seed_id: str) -> str:
    """
    Turn an edge into a short English phrase for the prompt.

    Telling the LLM "this function is CALLED BY the one you matched" rather
    than just dumping code helps it reason about direction - e.g. that a
    change here would affect the caller, not the other way round.
    """
    seed_name = seed_id.split("::")[-1]

    if rel_type == "CALLS":
        return f"calls {seed_name}" if not outgoing else f"called by {seed_name}"
    if rel_type == "CONTAINS":
        return f"defined inside {seed_name}" if outgoing else f"contains {seed_name}"
    if rel_type == "EXTENDS":
        return f"parent class of {seed_name}" if outgoing else f"extends {seed_name}"
    if rel_type == "USES_TYPE":
        # Direction matters more here than for any other edge. "the type
        # `Order` used by checkout" and "checkout, which works on this type"
        # send the model looking in opposite directions, and getting it
        # backwards is how an answer ends up describing the wrong end of a
        # dependency.
        return (
            f"the type used by {seed_name}"
            if outgoing
            else f"declares {seed_name} in its signature"
        )
    return f"connected to {seed_name}"


def _search_tokens(text: str) -> list[str]:
    """Preserve complete identifiers and add camel/snake components."""
    tokens = []
    for word in re.findall(r"[^\W]+", text, re.UNICODE):
        tokens.append(word.lower())
        parts = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", word).replace("_", " ").split()
        if len(parts) > 1:
            tokens.extend(p.lower() for p in parts)
    return tokens
