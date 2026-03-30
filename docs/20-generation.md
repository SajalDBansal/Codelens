# 20 - Folder summaries and answer generation

CodeLens generates two kinds of prose: short folder summaries while indexing, and an answer after retrieval. Both use the saved answer/reasoning model. Embeddings use their separately configured model.

## Folder summaries

Individual functions help answer specific questions, but "what is this area responsible for?" may need an overview. `pipeline/summarizer.py` sends an outline containing folder/file paths, entity names/kinds, and shortened docstrings. Function bodies are deliberately omitted from this summary prompt.

The prompt asks for two to four sentences grounded in that outline. Module pseudo-entities are excluded from the real-definition count. By default, a folder needs at least three real entities before summary generation is attempted.

The indexer reads the complete relevant folder contents from the updated graph when regenerating a summary. The summary's content hash uses the folder hash, so unchanged folders can reuse it. The generated text is embedded and stored in a `Summary` node for later semantic retrieval.

A summary can be absent because the folder is too small, generation is disabled, the service call fails, or the response is empty. Generation failures return no optional summary instead of necessarily failing the index. A later embedding/storage failure is a different failure path. On an unchanged early-exit run, missing optional summaries are not automatically regenerated; a forced run can deliberately retry them.

Generated summaries are a navigation aid, not independent evidence. Their input is an outline and they can be wrong or incomplete.

## An answer request

After successful retrieval, `answer_question` creates a system instruction and a user message containing the selected context and original question, then makes a text Chat Completions request. There is no preceding router model, hidden multi-agent workflow, tool execution, or automatic repository modification.

Each context block has a label:

| Label | Meaning in the prompt |
| --- | --- |
| `CODE` | Source text from an indexed entity, with path and line range. |
| `DOCS` | Documentation/configuration text, which may describe intention rather than current behavior. |
| `OVERVIEW` | Generated folder summary for orientation. |

The instructions ask the model to use the supplied evidence, prefer code when code and documentation disagree, acknowledge missing information, cite `file_path:line`, and explain connections when an answer spans pieces.

These are instructions to a generative model, not an enforced proof system. The package does not independently parse every citation or verify that every claim follows from its cited lines. Users should check important claims against the displayed evidence and source.

## Example reasoning a reader can verify

In the checkout demo, `checkout()` obtains a subtotal, passes it to `apply_discount`, passes the discounted result to `add_tax`, then rounds the final total. A useful answer to "Is tax applied before discount?" should say tax is applied after discount and point to those calls.

The answer need not match a fixed sentence. What matters is whether its claim and citations agree with the selected source. If retrieval only supplies `add_tax()` but omits its caller, the model may lack enough information about the ordering and should say so.

## Parameters, history, and streaming

The package passes `temperature=0` only for model families recognized by its sampling helper; other model IDs use defaults. If a temperature parameter is rejected, answer generation can retry without it. This does not guarantee identical text across repeated calls.

Every CLI question is independent. The request contains the current question/context, not a persistent conversation history. `project.json` records the last successful reasoning model, but no chat transcript is saved.

The response is collected and printed after completion. Token streaming is not enabled. `retrieve` and `ask --no-answer` skip this answer request entirely, while still doing query embedding and any permitted source update.

## Choosing a different answer model

```sh
codelens models
codelens config update reasoning_model gpt-4.1-mini
```

Use an account-accessible model compatible with the package's text Chat Completions calls. Model-list access is not a complete endpoint test. Changing this setting affects future answers and newly generated summaries; it does not rebuild stored code vectors or automatically rewrite existing summaries.

**Source:** [summarizer.py](../graph_rag/pipeline/summarizer.py), [answerer.py](../graph_rag/generation/answerer.py), [model_options.py](../graph_rag/generation/model_options.py).

[Previous: Retrieval](19-retrieval.md) | [Guide](README.md) | [Next: Testing](21-testing.md)
