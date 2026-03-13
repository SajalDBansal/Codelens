# 18 - How to test and evaluate the package

There are different questions to test: does the code behave correctly, does local storage survive an update, does retrieval find expected evidence, and does a model produce a good answer? One passing smoke test cannot establish all four.

The compatibility and local verification guide explains the supported Python versions, reproducible dependencies, and local checks. GitHub Actions and CI/CD setup are deferred. Use its constrained install command when reproducing the tested Windows baseline.

## Automated tests without OpenAI requests

From the source checkout, in its Python environment:

```sh
python -m pip install -e ".[dev]"
python -m pytest -q
```

In unactivated PowerShell use `.\.venv\Scripts\python.exe` instead of `python`. Tests use temporary graphs and model/credential substitutes. Installing dependencies or initializing parsers on a clean machine may require network access, but the test model behavior does not require a real OpenAI key.

| Test area | Examples of what is checked |
| --- | --- |
| Parsing and languages | Entities, nesting, ownership, line ranges, call/type extraction. |
| Resolution | Duplicate names, scope/import/type/arity evidence, interfaces. |
| Storage | Ladybug tables, vector dimensions, persisted writes, searches. |
| Incremental updates | Changed/deleted definitions, stale vectors, failed parsing, forced deletions. |
| Retrieval | Ranking/fusion, relevance floors, module expansion, context limits. |
| CLI/configuration | Setup, editable inputs, key hiding, model changes, project selection, `.gitignore`. |
| OpenAI SDK compatibility | Real SDK request serialization and response/error handling through an in-memory HTTP transport. No live API calls. |
| Recovery | Interrupted indexing and replacement-graph publication. |

Run focused files while changing a component:

```sh
python -m pytest tests/test_ladybug_store.py tests/test_retrieval_rules.py -q
python scripts/test_recovery.py
```

Test counts change as the project evolves. Use the output of your current run rather than a historical count as proof of coverage.

## Local smoke test

```sh
python scripts/smoke_local.py
```

The script parses the bundled checkout demo, supplies deterministic vectors and a summary, writes and reopens a real local graph, retrieves expected definitions, then checks that an unchanged repeat does no new embedding work. It does not generate an AI answer or measure semantic embeddings.

To retain its graph for inspection:

```sh
python scripts/smoke_local.py --db "/path/to/temporary-test/graph.lbug"
```

Use a new disposable path, not a real project's active database. The smoke profile deliberately uses three-dimensional fake vectors and is not interchangeable with a normal configured graph. The script calls the engine directly and does not create a normal user project manifest.

## Model-backed retrieval evaluation

First embed the matching demo through the normal CLI:

```sh
codelens embed examples/smoke_repo
python evals/run_eval.py --project smoke_repo --questions evals/questions.smoke.json
```

The smoke question set contains identifiers from the bundled checkout files. `questions.demo.json` targets a different demonstration layout; do not assume its expectations match any repository you choose.

Evaluation uses the stored snapshot without automatically updating source. It refuses a recorded incomplete update, uses saved configuration/profile checks, and embeds each question through OpenAI. It does not generate final answers. Model access and usage charges therefore still apply.

Each case has the form:

```json
{
  "question": "Where is percentage tax applied?",
  "expect": "pricing.py::add_tax"
}
```

`Recall@seed` is the fraction of cases whose expected entity appears among retained direct hits. `Recall@all` includes graph-expanded results retained in the final context. `--min-recall` controls the required overall fraction; the default is 0.9. These are target-entity hit measurements, not precision, citation correctness, or end-to-end answer accuracy. A parser ID change may require reviewing expected IDs.

## Check answers separately

Run `ask --show-context` and compare the answer with the actual source. Ask both answerable and unanswerable questions. Check ordering, source paths, cited lines, omitted evidence, and whether the answer admits uncertainty. Keep an evaluation set representative of the repository rather than optimizing only for one small example.

## Distribution checks

A wheel installation should provide `codelens --help` and `python -m graph_rag --help`. A source archive should include these docs, tests, scripts, evaluation cases, and the example files referenced here. The manifest includes those support files in the source distribution; the runtime wheel contains the import package plus its metadata/license rather than the whole repository.

**Source:** [tests](../tests), [smoke_local.py](../scripts/smoke_local.py), [run_eval.py](../evals/run_eval.py), [questions.smoke.json](../evals/questions.smoke.json).

[Previous](17-generation.md) | [Guide](README.md) | [Next: Troubleshooting](19-troubleshooting.md)
