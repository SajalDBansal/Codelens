# 17 - Change detection and recovery

CodeLens should not regenerate every vector after a small edit. It uses file/folder hashes to locate changed content and embedding-input fingerprints to decide which parsed blocks need new vectors.

## Three levels of comparison

1. **File hash:** computed from the accepted file's bytes.
2. **Folder hash:** computed from sorted child names and hashes, propagating a child change toward the root. This is the Merkle-style tree.
3. **Embedding-input fingerprint:** compares a particular code/document/summary input with the vector actually stored for it.

Suppose `pricing.py` changes only in `add_tax()`. Its file hash and ancestor folder hashes change. The file is parsed again. `subtotal()` and `apply_discount()` can reuse their embeddings if their exact inputs still match; `add_tax()` gets a new vector. Properties such as line numbers are refreshed even for unchanged blocks in a reparsed file.

## What the optimization really skips

The current scanner walks eligible files and reads their contents to construct today's tree. Only then does `diff_against_stored` compare it with saved hashes and skip unchanged subtrees in the in-memory comparison.

Therefore, "folders skipped" means subsequent comparison/parsing/index work can be skipped. It does not mean the scanner detected unchanged content without disk reads. The CLI wording "skipped without opening" should be interpreted in that narrower comparison-stage sense. There is no filesystem watcher or persistent mtime shortcut that eliminates the initial scan.

## Additions, edits, deletions, and exclusions

New files have no matching stored hash. Edited files have a different hash. Previously stored paths not observed in the current eligible tree are deleted from the graph. This includes files newly excluded by settings or moved outside the project.

When a file is reparsed, its current entity/chunk IDs are compared with old IDs. Renamed/deleted definitions are removed. Outgoing relationships are refreshed so deleted calls and imports do not remain attached.

`--force` treats current files as needing work but still loads old state for deletion detection. It does not forget which files or definitions existed. Excluded files are not restored by force.

## Why completion hashes are saved late

A stored file/folder completion hash means the indexing run reached the relevant completion point. The indexer creates required folder/file nodes without publishing their new hashes, invalidates affected completion hashes, then parses, embeds, connects, and performs cleanup. Successful file/folder hashes are saved near the end.

Parse failures are recorded individually. Failed files and their ancestor folders do not receive successful completion hashes, so another ordinary run can reach them. The CLI reports the failure, marks an ordinary update failed, and stops before answering.

This is recoverable incremental processing, not an atomic snapshot. Some node/edge changes may already be committed. The CLI prevents questions against its recorded incomplete update; direct low-level graph access does not automatically provide that same policy.

## Two update modes

| Mode | Storage behavior | Failure behavior |
| --- | --- | --- |
| Normal incremental update | Modifies the active database. | Saved work may remain; status becomes failed; retry must finish before normal questions. |
| Embedding-profile rebuild | Creates a separate graph generation. | Previous graph stays active; pending replacement is recorded and can be resumed with the same target profile. |

On a successful rebuild, the manifest switches `active_database`, retains the previous reference, clears the pending reference, and records ready status/counts/model metadata. Old generations are not automatically pruned.

## Recovery commands

```sh
codelens status --project demo
codelens embed --project demo
```

If a model replacement failed, repeat the rebuild with the same configured model/dimensions:

```sh
codelens embed --project demo --on-model-change rebuild
```

`--no-update` does not allow querying an ordinary update marked failed/indexing. A root location mismatch is not repaired by rewriting a manifest blindly. Follow the [troubleshooting guide](23-troubleshooting.md) and preserve the original graph if unsure.

## Summary and interface cleanup

Folder summaries are regenerated from the complete current contents read back from the updated graph, not only newly parsed entities. Deletion-only updates and newly excluded folders also remove obsolete summaries and orphan/noisy interface tokens. Disabling summary generation removes summaries for changed folders rather than leaving old descriptions of changed content searchable.

Optional summary generation can fail without failing the entire index. An unchanged early-exit run does not necessarily retry a missing optional summary; force a run when deliberately trying to regenerate it.

**Source:** [merkle.py](../graph_rag/change_detection/merkle.py), [indexer.py](../graph_rag/pipeline/indexer.py), [workflow.py](../graph_rag/cli/workflow.py), [test_indexing_recovery.py](../tests/test_indexing_recovery.py), [test_force_deletions.py](../tests/test_force_deletions.py).

[Previous: Embeddings](16-embeddings.md) | [Guide](README.md) | [Next: Indexing pipeline](18-the-indexing-pipeline.md)
