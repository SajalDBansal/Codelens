# 22 - Follow one project from files to an answer

This walkthrough uses the real [checkout demo](../examples/smoke_repo). Read its three files alongside this chapter. The model-backed commands below can incur OpenAI usage; [the offline smoke test](18-testing.md) checks the same local mechanics with controlled vectors instead.

## 1. Understand the source before asking AI

`checkout.py` imports three helpers from `pricing.py`. Its `checkout` function calls `subtotal`, then `apply_discount`, then `add_tax`, and finally Python's `round`.

For inputs `[100, 50]`, discount `10`, tax `20`:

```text
subtotal:       100 + 50 = 150
discount:      150 * (1 - 10 / 100) = 135
tax:           135 * (1 + 20 / 100) = 162
rounding:      162.00
```

This is a manually checkable expected behavior. The question "Is tax applied before or after discount?" has an answer in the call order, not merely in the name `add_tax`.

## 2. Register and index

From the package source checkout, after installation:

```sh
codelens init
codelens projects add examples/smoke_repo --name checkout-demo
codelens embed --project checkout-demo
```

Registration creates a manifest and registry entry and ensures `.codelens/` is ignored by Git. Indexing creates the local graph and records its model profile. Because the managed workflow can create `.gitignore`, its file/document counts may differ from the direct-engine smoke script's original three-file fixture. Counts are observations of the current eligible tree, not fixed milestones to memorize.

## 3. Follow a function through parsing and storage

`parse_file` reads `pricing.py` and produces the entity `pricing.py::add_tax`. Its source coordinates, body, name, kind, docstring, and hints go into a `CodeEntity` object.

The indexer saves it under a `File` node and compares its embedding fingerprint. For a first index there is no matching vector. `build_embedding_text` adds the path/language/name/docstring breadcrumb; the embedder sends that text to OpenAI. Storage attaches the returned vector and matching hash.

This happens for other entities too. The file also has a module entity representing its top-level material. Built-in functions such as `sum` and `round` do not become local function definitions just because the source calls them.

## 4. Follow a relationship

The parser sees `add_tax(discounted, tax)` inside `checkout`. Import resolution links `checkout.py` to `pricing.py`. The name resolver locates the appropriate indexed `add_tax`, and the graph stores:

```text
checkout.py::checkout --CALLS--> pricing.py::add_tax
```

The other two helpers are connected similarly. The README's backtick names produce document-to-code mention candidates. If summaries are enabled and the folder meets the threshold, the summary model describes its definition outline, and that prose gets its own vector.

## 5. Ask for evidence first

```sh
codelens retrieve "Is tax applied before or after the discount?" --project checkout-demo
```

The workflow checks source freshness. The retriever embeds the question, searches code/documents, fuses rankings, follows connected evidence, and formats context.

A useful result should include enough of `checkout` to establish order and the relevant pricing helpers to explain the operations. Which items are direct hits and which arrive through expansion depends on the question/model/settings. The system does not guarantee that a particular helper is always found by the graph rather than search.

Read the displayed paths, line ranges, and reasons. The `*` marker denotes a direct seed. Inspect whether the text actually supports the question before judging an AI answer.

## 6. Generate the answer

```sh
codelens ask "Is tax applied before or after the discount?" --project checkout-demo --show-context
```

This performs another query retrieval and sends its context plus the question to the answer model. The prompt asks it to explain that tax follows the discount and cite the source lines that show the order, if that evidence was retrieved. It may word the answer differently from this chapter.

The final answer is printed as a complete response. No persistent conversation is created. To ask a follow-up, make it self-contained rather than assuming the previous exchange is retained.

## 7. Change the source and observe an update

For a disposable copy of the demo, edit the `add_tax` docstring or function body and ask again. The scan sees a new file/folder hash. Parsing refreshes positions/properties, changed embedding inputs are regenerated, and still-matching helper inputs can reuse vectors. The module input or folder summary may also change, so do not assume exactly one new model request.

For a simple no-change comparison:

```sh
codelens embed --project checkout-demo
codelens embed --project checkout-demo
```

The second completed unchanged run should report no indexing changes. A later question still embeds its query even if the graph needs no update.

## 8. Inspect profile and project state

```sh
codelens status --project checkout-demo
codelens projects list
codelens config show embedding_model
codelens config show reasoning_model
```

Status shows the active database, stored profile, counts, and timestamps. Its source freshness is the last completed index, not a new scan performed by status itself.

Changing the reasoning model affects future generated text. Changing the embedding model/dimensions requires keeping the old profile or building a separate replacement. The [recovery chapter](14-change-detection.md) explains what happens if that replacement fails.

## 9. Evaluate retrieval against matching expectations

```sh
python evals/run_eval.py --project checkout-demo --questions evals/questions.smoke.json
```

This checks whether expected checkout/pricing entities appear in the stored snapshot's retrieved evidence. It makes query-embedding calls but does not generate answers or automatically update the source. It complements the manual explanation/citation check rather than replacing it.

**Source:** [checkout.py](../examples/smoke_repo/checkout.py), [pricing.py](../examples/smoke_repo/pricing.py), [demo README](../examples/smoke_repo/README.md), [smoke script](../scripts/smoke_local.py).

[Previous](21-glossary.md) | [Guide](README.md) | [Next: Contributing](23-contributor-guide.md)
