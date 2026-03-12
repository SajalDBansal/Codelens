# 02 - Why Graph RAG for code?

A codebase contains pieces and connections. A graph represents a piece as a **node** and a connection as an **edge**. A function can be a node; "this function calls that function" can be an edge.

## Start with a real example

In the bundled checkout demo:

```text
checkout.py::checkout
    --CALLS--> pricing.py::subtotal
    --CALLS--> pricing.py::apply_discount
    --CALLS--> pricing.py::add_tax
```

A question may match `checkout` strongly but mention none of the helper names. Following the graph can collect those helpers as evidence. This approach is called **seed and expand**: search finds starting nodes, then graph traversal finds connected nodes.

Graph construction and embedding are different operations. The parser and resolver establish structure. The embedding model produces vectors for semantic search; it does not invent the call graph.

## What the different edges mean

| Edge | Example | Interpretation |
| --- | --- | --- |
| `CONTAINS` | File -> function; class -> method | A structural parent contains a child. |
| `CALLS` | `checkout` -> `add_tax` | Static call resolution linked the caller and target. |
| `EXTENDS` | Child class -> parent class | An inheritance or supported type-implementation relationship. |
| `USES_TYPE` | Function -> declared type | A signature/field references an indexed type. |
| `IMPORTS` | File -> another file | An import resolved to a file in this project. |
| `MENTIONS` | README section -> function | A named reference in documentation was matched to code. |
| `REFERENCES` | Code/document -> interface token | The text contains a shared route, topic, table, environment-variable name, or gRPC token. |

Stored relationships do not all participate in the same search. Code-neighbor traversal uses `CALLS`, `CONTAINS`, `EXTENDS`, and `USES_TYPE`. Document mentions and shared interfaces are expanded separately. `IMPORTS` helps build and resolve the graph; ordinary code-neighbor expansion does not traverse it directly.

## A connection is useful evidence, not certainty

CodeLens parses syntax and applies matching rules. It does not run the program or replace a compiler. Names, imports, scopes, declared types, and argument counts help resolve references, but dynamic dispatch and framework wiring can still be missed or misidentified.

For example, two services may both mention `/api/cart`. Their shared `Interface` node is a useful discovery link. It does not prove that one service calls the other, that the URL is reachable, or that both use the same host or HTTP method. The UI/CLI explanation should describe this as a shared interface, not a confirmed runtime call.

Some relationships carry `confidence` and `resolvedBy`. Confidence values are ranking weights chosen for resolution rules, not measured probabilities. A value of `0.8` does not mean "80% proven correct."

## Why only one or two hops?

A **hop** crosses one graph edge. More hops can reveal a wider flow, but also gather unrelated neighbors and increase query work. The CLI defaults to one hop and permits two. It also limits module expansion and the amount of text included in the answer context.

The highest-scoring seed is not necessarily the whole answer. Conversely, a nearby node is not necessarily relevant. CodeLens ranks graph neighbors using their similarity to the question, relationship type, confidence weight, and distance.

## What this adds to ordinary search

Semantic search finds approximate meaning. Keyword search finds identifiers and words. Graph expansion adds relationships between selected pieces. Together they can help with "how does this flow work?", "what calls this?", and "where is this documented?" A full dependency audit still requires more complete analysis and human checking.

**Source:** [schema.py](../graph_rag/storage/schema.py), [indexer.py](../graph_rag/pipeline/indexer.py), [retriever.py](../graph_rag/retrieval/retriever.py).

[Previous](01-what-is-rag.md) | [Guide](README.md) | [Next: Product](03-what-this-product-does.md)
