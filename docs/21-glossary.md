# 21 - Glossary

| Term | Plain-language meaning in CodeLens |
| --- | --- |
| RAG | Retrieval-Augmented Generation: find evidence, supply it as context, generate an answer. |
| Graph RAG | Retrieval that also uses relationships between indexed pieces. |
| Indexing | Preparing files for search: scan, parse, connect, embed, and store. The `embed` command runs this whole workflow. |
| Graph | Nodes representing things and edges representing relationships. |
| Node | A folder, file, code entity, document chunk, summary, interface, or metadata record. |
| Edge / relationship | A stored link such as `CALLS`, `CONTAINS`, or `MENTIONS`. |
| Code entity | A function, method, class, constant, implementation, or module unit extracted from source. |
| Module node | A file-level code entity for top-level material and an outline of definitions. |
| Chunk | A bounded piece of text indexed as a unit, such as a README section. |
| Parser | Software that interprets syntax and produces a tree of constructs. |
| Syntax tree | A hierarchy showing how constructs such as functions, parameters, and calls fit together. |
| Tree-sitter | The parsing library and grammars used to build those trees. |
| Static analysis | Learning from source text without running the application. |
| Scope | The enclosing definition/file context that helps distinguish names. |
| Receiver | The object before a method call, such as `service` in `service.save()`. |
| Arity | The number of arguments/parameters, used cautiously as resolution evidence. |
| External reference | A called/referenced name without an indexed eligible target, often a library symbol. |
| Ambiguous reference | A name with multiple plausible targets that resolution did not settle. |
| Interface token | A normalized shared literal such as a route, topic, table, variable name, or gRPC method. |
| Embedding / vector | A list of numbers representing a text input for similarity comparisons. |
| Dimensions | How many numbers are in each vector. |
| Embedding profile | The model and dimensions used by a graph; they must match query embeddings. |
| Fingerprint | A hash identifying the exact embedding input plus model settings. |
| Hash | A compact value computed from content to detect changes. It does not describe meaning. |
| Merkle tree | A hierarchy where parent hashes depend on child names/hashes, allowing subtree comparisons. |
| Incremental update | Reprocess changed/deleted content while reusing still-valid work. |
| Completion hash | A stored file/folder hash written after the relevant indexing work completes. |
| Generation | Either model-generated text, or a separate database generation during a model rebuild; context tells which. |
| Semantic search | Search by vector similarity rather than exact wording alone. |
| Cosine similarity | A comparison of vector directions; storage rescales it to a 0..1 score. |
| BM25 | A keyword-ranking method based on term frequency, rarity, and document length. |
| Hybrid search | Combining semantic and keyword retrieval. |
| RRF | Reciprocal Rank Fusion: combine positions from multiple ranked lists. |
| Seed | A direct search result used as a starting point for graph expansion. |
| Neighbor | A node reached from a seed through an allowed relationship. |
| Hop | One edge crossed in a graph path. |
| Context | The selected evidence text supplied to the answer model. |
| Context budget | The configured limit on included evidence; currently measured in characters. |
| Token | A unit used by a model's tokenizer; not the same as a character or word. |
| Prompt | Instructions and input messages sent to a generation model. |
| Grounding | Asking a model to base claims on supplied evidence. It is an objective, not a guarantee. |
| Citation | A file/line reference in an answer that the reader can check. |
| LadybugDB | The embedded graph database used for each local project. |
| Cypher | The graph query language used by the storage implementation. |
| Primary key | A unique identifier used to address a stored node. |
| Manifest | `.codelens/project.json`, describing a project's root, active graph, profiles, and status. |
| Registry | The user-level list of registered projects and selected default. |
| Keyring | The operating system's credential-storage integration. |
| CLI | Command-line interface: commands typed into a terminal. |
| Virtual environment | An isolated Python dependency/interpreter environment. |
| Wheel | A built Python distribution installed by package tooling. |
| Source distribution / sdist | An archive of package source and included support files used to build/install it. |
| Smoke test | A small integrated check that core pieces work together. |
| Model double | A controlled replacement for a real model call in a test. |
| Recall@all in this evaluator | Fraction of cases whose expected entity survives retrieval and context assembly. |
| Streaming | Delivering pieces/events before completion; not the same as printing stage messages. |

Read the [walkthrough](22-end-to-end-walkthrough.md) to see these terms applied to the same small example instead of learning them in isolation.

[Previous](20-limitations.md) | [Guide](README.md) | [Next: Walkthrough](22-end-to-end-walkthrough.md)
