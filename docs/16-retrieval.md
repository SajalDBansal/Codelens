# 16 - From a question to useful evidence

Retrieval decides which pieces the answer model will see. A better model cannot reliably explain a missing function, so this stage is worth inspecting independently with `codelens retrieve`.

## 1. Ensure the selected graph is usable

The CLI selects and locks the project, checks the embedding profile, and normally refreshes changed source. `--no-update` intentionally skips that refresh, subject to the incomplete-update and profile checks. The retriever itself receives an open `GraphStore`; it does not manage project discovery or updates.

## 2. Embed the question

The question is embedded with the graph's embedding model and dimensions. This is an OpenAI request even for `retrieve` or `ask --no-answer`. The current package does not have a small-model intent router or query-rewriting stage: the question goes directly into retrieval.

## 3. Search code and documents separately

For each enabled category, the retriever requests a larger candidate pool from semantic search and keyword search: normally three times its requested seed count. Code and documents have separate quotas, so one does not fill every position in the other's ranking. Folder summaries use a separate vector-only search.

**Semantic search** computes exact cosine similarity over the stored vectors. Storage rescales it as:

```text
score = (1 + cosine_similarity) / 2
```

The result is clamped to 0..1. A score is not a likelihood that the answer is correct. There is no ANN/HNSW index in this implementation.

**Keyword search** uses a Python BM25-style index of names, paths, text, and documentation. It tokenizes identifiers as well as words, ignores selected stop words, and boosts an exact matching entity/title name. The postings cache is built per open store and invalidated on writes.

## 4. Fuse and filter candidates

Raw BM25 and cosine scores are not on the same scale, so CodeLens combines list positions with reciprocal rank fusion:

```text
fused_score(item) = sum(weight_of_list / (rrf_k + rank_in_list))
```

Ranks start at 1; default `rrf_k` is 20. Before keyword fusion, broad module-node hits are damped by `module_keyword_penalty`. The first `hybrid_reserved_vector_slots` semantic candidates are given priority positions, then remaining fused candidates are considered.

When a candidate has a returned semantic score, it must clear `min_similarity` and the relative floor below this question's best vector candidate. With defaults, the relative floor is best score minus 0.08. Keyword-only candidates without a score in the returned vector pool bypass those semantic floors and receive a display score of zero. Zero in that case means "no retained semantic score," not "proved irrelevant."

Reserved candidates still face filtering. A vector weight of zero is not a switch that suppresses query embeddings or the separate reserved-candidate logic. Inspect settings in context rather than assuming every weight is an on/off control.

## 5. Expand connections

| Expansion | Purpose |
| --- | --- |
| Code seeds -> code neighbors | Follow incoming/outgoing `CALLS`, `CONTAINS`, `EXTENDS`, and `USES_TYPE`, to one or two hops. |
| Document seeds -> mentioned code | Bring implementation alongside relevant prose. |
| Code seeds -> shared interfaces -> code | Find code referencing the same route/topic/table/env/gRPC token. |
| Collected code -> mentioning documents | Add descriptions of retrieved code. |

The structural query keeps intermediate nodes typed as code entities. A matched module's containment expansion is capped. Other expansion routes have their own result limits. Duplicate IDs are removed before context assembly.

Structural neighbors are ordered using a score shaped like:

```text
relevance_weight * semantic_score * edge_type_weight * confidence
    - 0.15 * hop_count
```

Document/interface expansions use separate logic and a fallback rank. For two-hop results, the reported reason describes the stored first-edge information; it is not a complete intermediate-node/path trace. The package does not yet return a frontend-ready evidence-path graph.

## 6. Fit evidence into context

The assembly order is folder overviews, code seeds, document seeds, ranked code neighbors, then neighboring documents. Seed lists are sorted by their retained semantic score for assembly. Blocks include source locations, kinds, text, and retrieval reasons.

Code/document bodies are truncated by `max_block_chars`; the total assembled context is limited by `max_context_chars`. If a block does not fit, it is skipped and later blocks are still considered. `dropped_count` records these omissions. Character budgets do not exactly equal model-token budgets, and the surrounding answer instructions/question add more text.

`RetrievalResult` contains the included code items, documents, summaries, question, context text, and dropped count. It does not contain every search candidate or a durable replay trace.

## 7. Inspect before generating

```sh
codelens retrieve "Where is discount validated?" --project smoke_repo
codelens ask "How is tax applied?" --project smoke_repo --show-context
```

The `*` marker denotes a direct hit (`hop == 0`), not necessarily a vector-only hit. Reasons explain connected results. A retrieval failure can mean no indexed content, a missed candidate, overly strict filtering, or a context budget that kept no useful evidence. The CLI stops before answer generation when nothing was retained, but the query embedding has already occurred.

**Source:** [retriever.py](../graph_rag/retrieval/retriever.py), [graph_store.py](../graph_rag/storage/graph_store.py), [ask_command.py](../graph_rag/cli/ask_command.py), [test_retrieval_rules.py](../tests/test_retrieval_rules.py).

[Previous](15-the-indexing-pipeline.md) | [Guide](README.md) | [Next: Generation](17-generation.md)
