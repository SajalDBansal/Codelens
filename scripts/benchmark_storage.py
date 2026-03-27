"""Reproducible offline benchmark of the local SQLite and NumPy pipeline.

Uses deterministic fake model outputs. Times measure local processing, not API
latency or semantic answer quality. Each run builds a fresh disposable project.
"""

import argparse
import hashlib
import json
import platform
import statistics
import sys
import tempfile
import time
from dataclasses import asdict
from pathlib import Path


def peak_memory_bytes():
    """Process peak RSS, including Python/imports; no polling interval bias."""
    if sys.platform == "win32":
        import ctypes
        from ctypes import wintypes

        class Counters(ctypes.Structure):
            _fields_ = [("cb", wintypes.DWORD), ("PageFaultCount", wintypes.DWORD)] + [
                (name, ctypes.c_size_t)
                for name in (
                    "PeakWorkingSetSize",
                    "WorkingSetSize",
                    "QuotaPeakPagedPoolUsage",
                    "QuotaPagedPoolUsage",
                    "QuotaPeakNonPagedPoolUsage",
                    "QuotaNonPagedPoolUsage",
                    "PagefileUsage",
                    "PeakPagefileUsage",
                )
            ]

        counters = Counters()
        counters.cb = ctypes.sizeof(counters)
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.GetCurrentProcess.restype = wintypes.HANDLE
        measure = ctypes.WinDLL("psapi", use_last_error=True).GetProcessMemoryInfo
        measure.argtypes = [wintypes.HANDLE, ctypes.POINTER(Counters), wintypes.DWORD]
        if not measure(kernel.GetCurrentProcess(), ctypes.byref(counters), counters.cb):
            raise ctypes.WinError(ctypes.get_last_error())
        return counters.PeakWorkingSetSize
    import resource

    value = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    return value if sys.platform == "darwin" else value * 1024


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--files", type=int, default=60)
    parser.add_argument("--functions", type=int, default=10)
    parser.add_argument("--dimensions", type=int, default=1536)
    parser.add_argument("--queries", type=int, default=15)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    import numpy as np

    from graph_rag import config
    from graph_rag.embedding import embedder
    from graph_rag.pipeline import indexer, summarizer
    from graph_rag.retrieval import retriever
    from graph_rag.storage import GraphStore

    config.EMBEDDING_DIM = args.dimensions
    config.EMBEDDING_MODEL = "offline-benchmark-v1"
    config.OPENAI_API_KEY = "unused-offline"

    def vector(text):
        seed = int.from_bytes(hashlib.sha256(text.encode()).digest()[:8], "little")
        value = np.random.default_rng(seed).standard_normal(args.dimensions).astype("float32")
        return (value / np.linalg.norm(value)).tolist()

    class FakeEmbedder:
        def __init__(self, *a, **kw):
            pass

        def embed_entities(self, items):
            return {x.id: vector(embedder.build_embedding_text(x)) for x in items}

        def embed_docs(self, items):
            return {x.id: vector(embedder.build_doc_embedding_text(x)) for x in items}

        def embed_summaries(self, items):
            return {x.id: vector(embedder.build_summary_embedding_text(x)) for x in items}

        def embed_query(self, question):
            return vector(question)

    class FakeSummarizer:
        def summarise(self, path, entities, folder_hash):
            from graph_rag.models import FolderSummary

            return FolderSummary(
                id=f"summary::{path}",
                path=path,
                text=summarizer.build_summary_prompt(path, entities)[:2000],
                content_hash=folder_hash,
                entity_count=len(entities),
            )

    indexer.Embedder = retriever.Embedder = FakeEmbedder
    indexer.Summarizer = FakeSummarizer

    args.output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="benchmark-", dir=args.output.parent) as folder:
        root = Path(folder)
        for i in range(args.files):
            path = root / f"area{i // 10}" / f"service{i}.py"
            path.parent.mkdir(exist_ok=True)
            lines = ["import os", f"ROUTE = '/service/{i}'", ""]
            for j in range(args.functions):
                name = f"calculate_{i}_{j}"
                lines += [
                    f"def {name}(value):",
                    f'    """Compute invoice total for service {i}, step {j}."""',
                    "    total = value",
                ]
                lines += [
                    f"    total += {k}  # adjustment for invoice processing" for k in range(12)
                ]
                if j:
                    lines.append(f"    total = calculate_{i}_{j - 1}(total)")
                lines += ["    return total", ""]
            path.write_text("\n".join(lines), encoding="utf-8")
        (root / "README.md").write_text(
            "# Invoices\n\nUse `calculate_0_0` to calculate invoice totals.\n\n"
            "## Configuration\n\nSet `INVOICE_MODE` for invoice processing.\n",
            encoding="utf-8",
        )
        db = root / ".codelens" / "graph.index"
        phases = {}

        def timed(name, callback):
            started = time.perf_counter()
            result = callback()
            phases[name] = time.perf_counter() - started
            return result

        first = timed(
            "initial_index_s", lambda: indexer.run_indexing(root, verbose=False, db_path=db)
        )
        noop = timed(
            "unchanged_index_s", lambda: indexer.run_indexing(root, verbose=False, db_path=db)
        )
        target = root / "area0/service0.py"
        target.write_text(
            target.read_text(encoding="utf-8").replace("total = value", "total = value + 1", 1),
            encoding="utf-8",
        )
        changed = timed(
            "one_file_update_s", lambda: indexer.run_indexing(root, verbose=False, db_path=db)
        )
        queries = [
            f"calculate_{i % args.files}_{i % args.functions} invoice total"
            for i in range(args.queries)
        ]
        query_vectors = [vector(q) for q in queries]
        timings = {
            "vector_ms": [],
            "keyword_ms": [],
            "retrieval_ms": [],
            "reopened_retrieval_ms": [],
        }
        evidence = []
        with GraphStore(db, read_only=True) as store:
            counts = store.stats()
            shape = store.connectivity_stats()
            for question, query_vector in zip(queries, query_vectors):
                for key, operation in (
                    ("vector_ms", lambda v=query_vector: store.vector_search(v, 20)),
                    ("keyword_ms", lambda q=question: store.fulltext_search(q, 20)),
                    ("retrieval_ms", lambda q=question: retriever.retrieve(q, store)),
                ):
                    start = time.perf_counter()
                    result = operation()
                    timings[key].append((time.perf_counter() - start) * 1000)
                    if key == "retrieval_ms":
                        evidence.append(
                            {
                                "ids": [r.entity.id for r in result.items],
                                "docs": [r.chunk.id for r in result.docs],
                                "context_hash": hashlib.sha256(
                                    result.context_text.encode()
                                ).hexdigest(),
                            }
                        )
        for question in queries:
            start = time.perf_counter()
            with GraphStore(db, read_only=True) as store:
                retriever.retrieve(question, store)
            timings["reopened_retrieval_ms"].append((time.perf_counter() - start) * 1000)
        result = {
            "peak_process_rss_bytes": peak_memory_bytes(),
            "python": platform.python_version(),
            "platform": platform.platform(),
            "numpy": np.__version__,
            "parameters": {k: str(v) if isinstance(v, Path) else v for k, v in vars(args).items()},
            "phases": phases,
            "counts": counts,
            "connectivity": shape,
            "initial_report": asdict(first),
            "unchanged_report": asdict(noop),
            "changed_report": asdict(changed),
            "timings": {
                k: {
                    "median": statistics.median(v),
                    "p95": sorted(v)[max(0, int(len(v) * 0.95) - 1)],
                    "samples": v,
                }
                for k, v in timings.items()
            },
            "storage_bytes": sum(p.stat().st_size for p in db.parent.rglob("*") if p.is_file()),
            "source_bytes": sum(p.stat().st_size for p in root.rglob("*.py")),
            "evidence": evidence,
            "model_calls": "deterministic fake responses; no network/model usage",
        }
        args.output.write_text(json.dumps(result, indent=2), encoding="utf-8")
        print(json.dumps({k: result[k] for k in ("phases", "counts", "storage_bytes")}, indent=2))


if __name__ == "__main__":
    main()
