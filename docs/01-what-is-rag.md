# 01 - What is RAG?

RAG stands for **Retrieval-Augmented Generation**. It means finding useful information first, then giving it to a language model as context for an answer. The [original RAG paper](https://arxiv.org/abs/2005.11401) combines language generation with retrieved external knowledge. CodeLens applies the broader retrieval-before-generation idea to source code.

## The problem

Imagine joining a project and asking, "Is tax applied before or after the discount?" A general model can explain common checkout designs, but it does not automatically know your local files. It needs the implementation to answer this project's question.

You could paste files into a chat. That works for small examples. As the project grows, you have to choose the right files, keep them current, and fit them into the model's input limit. CodeLens automates part of that selection.

## The three parts

1. **Retrieve:** find relevant source code and documentation.
2. **Augment:** put that evidence beside the question in the model's input.
3. **Generate:** ask the model to explain the evidence and cite its source.

```text
Question: "When is tax applied?"
    -> retrieve checkout() and add_tax()
    -> include their source, paths, and line numbers in the prompt
    -> generate an explanation based on those blocks
```

The model does not receive the complete graph database. It receives the selected text context assembled by the retriever.

## Why indexing happens first

Before questions can be answered, the project must be made searchable. CodeLens reads eligible files, extracts useful pieces, builds relationships, computes embeddings, and saves them. This preparation is called **indexing**. In the CLI, `codelens embed` starts that whole process, even though its name mentions only one stage.

An **embedding** is a list of numbers representing a text input. Text with related meaning can have similar vectors. For example, a question about "reducing a price" might match code named `apply_discount`, even without using the same words.

CodeLens also uses keyword search. Exact names matter in code: a literal identifier can be more useful than a broad semantic match. Neither method is perfect, so the package combines them.

## Two kinds of model work

| Model role | Input | Output |
| --- | --- | --- |
| Embedding model | A code/document chunk or a question | A numeric vector used for similarity search |
| Answer model | The question plus selected evidence | Human-readable text |

The answer model also writes optional folder summaries during indexing. It can differ from the embedding model. Choosing a new answer model does not change the vector space; choosing a new embedding model does.

## Why it is useful, and what it does not guarantee

RAG can reduce repeated manual file selection, limit irrelevant context, and expose the source of an answer. Incremental indexing helps keep the evidence aligned with edited files.

It does not guarantee correctness. Relevant files can be excluded, parsing can miss behavior, retrieval can choose weak evidence, and a model can misread good evidence. A wrong answer can originate at any stage. This is why CodeLens offers `retrieve` and `--show-context`: you can inspect what reached the model.

CodeLens does not train or fine-tune a model on your repository. It saves an index and sends selected inputs to OpenAI when needed. Local storage does not mean fully offline operation. For a known identifier, ordinary text search can still be the fastest tool.

**Check your understanding:** if retrieval misses `add_tax()`, a more capable answer model may still lack the evidence needed to explain it. Inspect retrieval before assuming generation is the problem.

**Source:** [retriever.py](../graph_rag/retrieval/retriever.py), [answerer.py](../graph_rag/generation/answerer.py).

[Guide](README.md) | [Next: Why Graph RAG for code?](02-why-graph-rag-for-code.md)
