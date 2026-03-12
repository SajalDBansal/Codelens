# 12 - How relationships are resolved

Finding a call named `save()` is easier than deciding which `save()` it means. The parser collects facts from one file; the indexer then compares them with the other indexed definitions. This chapter explains how links are selected and why uncertainty remains.

## Local structure first

The parser can directly establish class/member containment and many references within a file. It respects nested scopes and records unresolved names when it cannot settle a target locally. Ownership hints also connect definitions such as Go receiver methods and Rust implementations to types defined elsewhere.

Self-recursion is recorded on the entity. A library call such as Python's `sum()` normally has no source node in the project, so it remains an external reference rather than receiving a fabricated target.

## Cross-file name resolution

The current `resolve_reference` logic broadly follows these filters:

1. Remove candidates of inappropriate kinds. Constants, module nodes, and implementation containers are not ordinary call targets.
2. Keep candidates in the source language family when known. JavaScript/TypeScript/TSX share a family.
3. Link a single eligible candidate, or use scope preference to narrow duplicate names.
4. Use the caller's file and resolved import visibility when multiple candidates remain.
5. Use a receiver/owner type hint if it selects a plausible unique target.
6. Use argument-count evidence where signatures are reliable enough.
7. If ambiguity remains, retain the unresolved reference.

A unique same-family name can be linked even when import evidence is absent. This is a heuristic, not full compiler resolution. Optional/default/variadic parameters make arity less decisive; the parser records flexible signatures so a count does not incorrectly rule them out.

## File imports and type references

Import text is resolved against the set of files actually in this scan. Supported patterns include Python module paths/relative imports, quoted local paths, and some Rust paths. The resolver does not implement every build tool's aliases, package loader, or runtime module system.

`IMPORTS` edges are refreshed before cross-file call resolution. Removing an import from a successfully reparsed file removes its old stored import edge. `USES_TYPE` represents declared type dependencies in signatures or fields, not an invocation of that type.

## Evidence weights

| Resolution marker | Stored weight |
| --- | --- |
| `same-file` | 1.0 |
| `linked` | 0.95 |
| `type-reference` | 0.9 |
| `receiver-type` | 0.8 |
| `arity` | 0.6 |

These weights help rank retrieved neighbors. They are not statistical confidence measurements. `resolvedBy` describes the rule category recorded by the implementation; it is not a proof trace containing every inference.

## Documentation mentions

A backtick name in a document becomes a mention candidate. Matches are ranked by path proximity and capped by `max_mention_targets`, normally 3. Common names may link to multiple definitions. Prefer distinctive names and nearby documentation when interpreting a `MENTIONS` edge.

## Shared interfaces across languages

Text extraction looks for selected literal patterns for routes, event/queue topics, database tables, environment-variable names, and gRPC services/methods. For example:

```text
TypeScript fetch("/api/cart") -> Interface(route:/api/cart)
Python route("/api/cart")     -> the same Interface node
```

Routes can normalize parameter forms to a wildcard and strip a trailing slash. Common root/static-asset patterns are filtered. Interface tokens with excessive fanout are pruned; orphan tokens are cleaned up too.

Sharing a token establishes a discovery relationship. It does not prove producer/consumer direction, network reachability, or a runtime call. A table name can refer to different databases; a variable name can be reused in unrelated contexts. Generated/dynamic strings may never match at all.

## Incremental behavior

Changed files have appropriate outgoing relationships refreshed so removed references do not linger. Unresolved internal references are retained for later attempts. Names classified as external are reconsidered when a relevant name is introduced, rather than being retried blindly every time. Deleting targets removes their graph edges.

A disconnected graph is not automatically an error: independent utilities, dynamic behavior, missing parsers, and genuinely separate components can all create separate pieces. Connectivity metrics measure the stored structure, not correctness of the source or completeness of the analysis.

**Source:** [indexer.py](../graph_rag/pipeline/indexer.py), [import_resolver.py](../graph_rag/pipeline/import_resolver.py), [interfaces.py](../graph_rag/parsing/interfaces.py), [test_evidence_resolution.py](../tests/test_evidence_resolution.py), [test_type_edges.py](../tests/test_type_edges.py).

[Previous](11-data-model-and-schema.md) | [Guide](README.md) | [Next: Embeddings](13-embeddings.md)
