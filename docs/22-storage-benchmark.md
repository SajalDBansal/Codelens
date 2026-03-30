# 22 - Measure local performance

The offline benchmark measures CodeLens parsing, SQLite storage, NumPy vector
search, graph retrieval, and source-context construction. It uses deterministic
model doubles, so it needs no API key and makes no OpenAI requests.

## Run a benchmark

From a development installation in the source folder:

```sh
python scripts/benchmark_storage.py --files 60 --queries 15 --output docs/benchmarks/storage/run-60.json
```

For a larger generated repository:

```sh
python scripts/benchmark_storage.py --files 300 --queries 15 --output docs/benchmarks/storage/run-300.json
```

The script creates a temporary repository with ten functions per Python file,
Markdown content, and deterministic folder summaries. Each run starts with a fresh
index. Use `--functions` or `--dimensions` to change the workload. Output folders
are created automatically; `docs/benchmarks/` is ignored by Git and excluded from
the source distribution.

## What the JSON records

| Measurement | Meaning |
| --- | --- |
| Initial indexing | Parse, embed doubles, connect, summarize doubles, and persist a fresh project. |
| Unchanged indexing | Scan again and reuse the existing index. |
| One-file update | Change one function and incrementally update its index. |
| Vector and keyword queries | Search an already-open store. |
| Full retrieval | Search, graph expansion, ranking, and context construction. |
| Reopened retrieval | Open storage, retrieve, and close it for each query. |
| Storage bytes | SQLite and all vector segments after the update. |
| Peak process memory | Python, parser, NumPy, model doubles, and storage together. |

Results also include dependency versions, workload parameters, graph counts,
evidence IDs, and context hashes. Timings include individual samples and summary
statistics. Run several times on an otherwise idle machine, keep the same Python
and dependency versions, and compare medians across those runs.

## Interpret the result

The model doubles test local mechanics, not semantic search quality. Real API
latency, embedding cost, answer quality, and your repository's language mix can
change end-to-end performance. Reopened timings include opening and closing the
index, but not Python startup; normal OS caches are still in use.

Exact vector search grows with corpus size. Source compression and index size
depend on the workload and update history. Do not treat one fixture as a latency
guarantee for every project. Use [retrieval evaluation](21-testing.md) separately
when assessing model-backed evidence quality.

[Previous: Testing](21-testing.md) | [Guide](README.md) | [Next: Troubleshooting](23-troubleshooting.md)
