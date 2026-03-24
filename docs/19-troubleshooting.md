# 19 - Troubleshooting by stage

Start with `codelens doctor`, `codelens projects list`, and `codelens status --project NAME`. These inspect local state without making model requests. A problem at installation, scanning, parsing, retrieval, or generation needs a different fix.

## The command cannot be found

Use the environment where CodeLens was installed. In the package directory on PowerShell:

```powershell
.\.venv\Scripts\codelens.exe --help
.\.venv\Scripts\python.exe -m graph_rag --help
```

In Git Bash, activate `.venv/Scripts/activate` or quote an absolute alias path:

```bash
alias codelens='"/c/Code Projects/codelens/.venv/Scripts/codelens.exe"'
```

Replace the example path. Backslashes and unquoted spaces can be interpreted by Bash. If an old `graph-rag-reviewer` distribution remains in the same environment, follow the rename/reinstall instructions in [setup](04-setup-and-first-run.md).

## Configuration or credential failure

Run `codelens init` to save settings and a key. Use `config update api_key` to replace a key through hidden input. A working OS keyring backend is required. Headless Linux may need credential-store configuration; copying an `.env` file is not a supported fallback.

`config check` verifies an authenticated model listing. If it succeeds but embedding/answering fails, inspect the selected model's endpoint compatibility and the service error. An account-visible ID is not a guarantee that the package can use that model for this operation.

Malformed manually edited JSON should be repaired or restored. Do not paste the API key into the configuration JSON. `doctor` reports credential availability without printing the key.

## Wrong project, duplicate name, or missing source

Use `projects list` to inspect IDs and locations. An explicit `--project ID` avoids ambiguity and overrides current-directory discovery. `projects use` only supplies the fallback outside another project.

Source roots are stored both in the project manifest and graph metadata. Moving/copying a `.codelens` folder to an unrelated path is not automatically supported. Preserve the original data; for a moved checkout, use a fresh project/graph at the intended root rather than editing ownership fields blindly. `--no-update` still requires the registered source directory to exist.

## Model or dimension mismatch

```sh
codelens ask "Explain checkout" --project demo --on-model-change keep
codelens embed --project demo --on-model-change rebuild
```

Keep uses the existing graph profile for one command. Rebuild creates a separate generation and may make many new model requests. `--force` is not an alternative to this compatibility check. The same dimension does not make vectors from different models interchangeable.

## No graph or no eligible content

Run embed if the project has only been registered. If the scan finds few or no files, check the supplied root, folder names/suffixes, size limits, file classification, permissions, symlinks, and binary detection. An accepted text-only repository may have documents but no code entities; zero `CALLS` edges can be correct.

If `artifacts` is excluded, its contents should not be indexed at any depth. After changing exclusions, force an embed to apply them to previously unchanged files. CodeLens's directory settings are independent of arbitrary `.gitignore` patterns.

## Parse errors or incomplete updates

Read the per-file failure messages, correct the source/parser/dependency problem, and rerun embed. Failed files remain eligible for retry. The normal CLI stops before answering against an ordinary update marked failed/indexing. Do not remove completion/status fields merely to bypass recovery.

For a failed replacement, repeat the rebuild with the same target profile to resume its pending path. Retried model work can include batches whose responses were not saved before the interruption.

## Native database errors or file-in-use errors

Close other CLI processes using the same project and close direct `GraphStore` connections. The CLI uses file locks and Ladybug uses native storage; a lock conflict is not a reason to run two writers anyway.

If a Python native-crash dialog reappears, record the exact command, Python/OS versions, installed Ladybug version, and whether the offline smoke script fails on a new disposable path. The current implementation uses Ladybug core search and Python keyword search, without loading downloadable native search extensions. Do not delete the real graph to diagnose a crash. Run `python -m pip check` and compare with the pinned dependency setup before changing versions.

## Retrieval misses something that exists

```sh
codelens retrieve "exact_function_name" --project demo
codelens ask "your specific question" --project demo --show-context
```

Check the stages in order: was the file eligible, did parsing extract the entity, is its vector valid, did search find it, did graph expansion reach it, and did the context budget retain it? Try exact identifiers and compare retrieval settings one at a time. Larger hop counts or top-k can add irrelevant context as well as useful context.

`--doc-top-k 0` disables document retrieval but not summary retrieval; set `default_summary_top_k` to zero if you want to inspect code without folder overviews. Similarity thresholds are not calibrated correctness probabilities. A direct keyword hit may have no returned semantic score.

## Good evidence but a bad answer

Compare the selected source with the response. A model can misunderstand ordering, overstate a shared-interface link, or invent an incorrect citation even when prompted otherwise. Try a narrower question or another compatible answer model. No model change is a substitute for inspecting evidence on important claims.

## Cost, stale summaries, and reset expectations

Large accepted text, forced embeddings, repeated failed batches, and regenerated summaries can increase usage. An unchanged embed can avoid model work; a question still requests an embedding. There is no guarantee of free retrieval.

A changed answer model does not rewrite existing summaries. Optional summary failures can leave an overview absent until a later eligible/forced update. `projects remove` unregisters only; `--force` reprocesses content rather than deleting every database generation. There is no reset/prune command, and no database-server deletion command is needed.

**Source:** [workflow.py](../graph_rag/cli/workflow.py), [state.py](../graph_rag/cli/state.py), [projects.py](../graph_rag/cli/projects.py), [local_connection.py](../graph_rag/storage/local_connection.py).

[Previous](18-testing.md) | [Guide](README.md) | [Next: Limits](20-limitations.md)
