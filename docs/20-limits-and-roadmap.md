# 20 - Current limits and possible next work

These docs describe what is implemented now. This chapter separates current capabilities from possible improvements so a reader does not mistake a proposal for a feature.

## Current limits

| Area | Current boundary |
| --- | --- |
| Models | OpenAI only; saved embedding choices and compatible text answer models. |
| Deployment | Local synchronous CLI; no hosted API/authentication/job queue. |
| Project scope | One graph per project and one selected project per question. |
| Versioning | Current local source plus retained model-rebuild generations; no commit history browser. |
| Parsing | Static syntax and resolution heuristics; incomplete runtime/dynamic behavior. |
| Documents | Chunk caps can omit later content; identical repeated heading trails can collide in chunk identity. |
| Shared interfaces | Literal-derived discovery links, not verified service calls. |
| Retrieval | Exact vector scans and per-connection Python keyword cache; large-corpus performance is not guaranteed. |
| Hashing | Eligible files are read to build the tree on each freshness scan. |
| Recovery | Repairable writes and completion markers; no atomic full-index transaction or per-API-batch durable checkpoint. |
| Summaries | Generated from outlines and can be missing/stale/inaccurate. |
| Answers | Prompted citations and grounding; no independent claim/citation verifier. |
| Progress | Human-readable stages/counters, not a complete structured event stream. |
| Chat | Independent questions; no saved conversation history or token streaming. |
| Data handling | Local graphs contain source; eligible content is sent to OpenAI for model work. Filename exclusions are not content-level secret detection. |
| Platforms | Current release checks focus on Windows/Python 3.12; other targets need explicit verification. |

Some limits are deliberate for a small local tool. Others identify worthwhile engineering work. None implies that a graph/database rewrite is required before the package can be useful.

## A practical improvement order

1. **Finish release verification:** clean-machine installation, supported Python/OS/dependency combinations, source/wheel checks, and a release process.
2. **Improve observability:** structured callbacks/events for scan, parse, embedding batches, graph writes, retrieval, and completion; preserve readable CLI output.
3. **Improve answer inspection:** return explicit retrieved paths and citation targets, distinguish missing coverage from no relevant evidence, and validate citations against the selected snapshot.
4. **Measure scale:** profile hashing, exact vector search, keyword-cache construction, graph expansion, native memory, and model usage on representative repositories before changing storage/search designs.
5. **Improve recovery granularity:** save completed embedding batches durably and introduce clearer generation/checkpoint semantics if the workflow needs them.
6. **Extend parsing and retrieval:** add language-specific evidence, stronger import resolution, and representative evaluation cases rather than treating more edges as automatically better.

These are suggestions, not a promised schedule. An alternative provider, persistent chat, automatic pruning, or a web interface would require explicit design and tests.

## If a website is built later

The current runtime settings context and local file locks are appropriate to sequential local commands. A concurrent service would need explicit per-request configuration, tenant/project authorization, isolated jobs, controlled source acquisition, streamed events, durable metadata, and storage lifecycle policies.

The website should not scrape terminal output and assume it is a complete event API. The embedding wrapper currently returns batch results, and answer generation collects the whole answer before printing. Progress instrumentation and answer streaming need to be added intentionally.

Likewise, sharing graphs between users of the same repository is not a current package feature. It needs a separate permission, version-identity, and data-retention design. The separate application/worker can evolve independently while reusing suitable engine boundaries.

## What counts as evidence of improvement?

More graph edges can mean better coverage or more false links. More retrieved text can improve recall or dilute the answer. Faster fake-vector tests do not prove semantic quality. Evaluate changes with structural tests, target-entity retrieval cases, answer/citation review, and representative resource measurements.

**Source:** [configuration context](../graph_rag/cli/settings.py), [storage search](../graph_rag/storage/graph_store.py), [retrieval result](../graph_rag/retrieval/retriever.py), [answer call](../graph_rag/generation/answerer.py).

[Previous](19-troubleshooting.md) | [Guide](README.md) | [Next: Glossary](21-glossary.md)
