# 20 - Current limitations

These are the boundaries of the published CodeLens 0.1.0 package. Use them when interpreting a graph or answer and deciding whether the tool fits your project.

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
| Platforms | Windows x64/Python 3.11–3.13; other targets are not verified. |

The package runs against local source files. It does not provide a website, remote repository download, multi-user authorization, or a service API.

## Interpret results carefully

A larger graph can contain more false links, and more retrieved text can dilute
an answer. Inspect the actual source and `ask --show-context` when a result matters.
Static relationships and requested citations do not prove runtime behavior.

Passing tests with fake vectors demonstrates storage and retrieval mechanics,
not semantic answer quality. Repository size, language features, model access,
and eligible source content affect coverage, latency, and cost.

See [Troubleshooting](19-troubleshooting.md) for diagnosing problems and
[Testing](18-testing.md) for evaluating a repository-specific result.

**Source:** [storage search](../graph_rag/storage/graph_store.py),
[retrieval](../graph_rag/retrieval/retriever.py), [answer generation](../graph_rag/generation/answerer.py).

[Previous](19-troubleshooting.md) | [Guide](README.md) | [Next: Glossary](21-glossary.md)
