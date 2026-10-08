"""Index a curated merchant corpus and measure actual retrieval/context recall.

Run from the repository root. Does not call a model or merchant transaction API.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import statistics
import time
from pathlib import Path

from openjarvis.core.config import load_config
from openjarvis.core.types import Message, Role
from openjarvis.tools.storage.context import ContextConfig, inject_context
from openjarvis.tools.storage.sqlite import SQLiteMemory


def percentile(values: list[float], fraction: float) -> float:
    return sorted(values)[min(len(values) - 1, int(len(values) * fraction))]


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--index", action="store_true")
    parser.add_argument(
        "--config", default="configs/openjarvis/examples/ordering-kiosk-mcp.toml"
    )
    parser.add_argument("--corpus", default="configs/openjarvis/knowledge/trendcoffee")
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    root = Path(args.corpus)
    manifest = json.loads((root / "manifest.json").read_text())
    config = load_config(Path(args.config))
    started = time.perf_counter()
    backend = SQLiteMemory(config.memory.db_path)
    open_ms = (time.perf_counter() - started) * 1000
    if args.index:
        for document in manifest["documents"]:
            path = root / document["file"]
            content = path.read_text()
            if hashlib.sha256(path.read_bytes()).hexdigest() != document["sha256"]:
                raise ValueError(f"Corpus checksum mismatch: {path}")
            backend.replace_source(
                str(path.resolve()),
                [
                    (
                        content,
                        {
                            **document,
                            "corpus_id": manifest["corpus_id"],
                            "corpus_version": manifest["version"],
                        },
                    )
                ],
            )
    cases = json.loads((root / "retrieval-cases.json").read_text())
    ctx_config = ContextConfig(
        top_k=config.memory.context_top_k,
        min_score=config.memory.context_min_score,
        max_context_tokens=config.memory.context_max_tokens,
    )
    rows = []
    timings = []
    cold_timings = []
    for case in cases:
        messages = (
            [Message(role=Role.USER, content=case["previous"])]
            if case.get("previous")
            else []
        )
        query = case["query"]
        # Measure the same original query used by inject_context.
        results = backend.retrieve(query, top_k=ctx_config.top_k)
        ranking = [Path(result.source).stem for result in results]
        first_ms = None
        for _ in range(20):
            started = time.perf_counter()
            enriched = inject_context(query, messages, backend, config=ctx_config)
            elapsed = (time.perf_counter() - started) * 1000
            if first_ms is None:
                first_ms = elapsed
                cold_timings.append(elapsed)
            else:
                timings.append(elapsed)
        context = "\n".join(m.text for m in enriched if m.role == Role.SYSTEM)
        expected = case["expected"]
        rows.append(
            {
                **case,
                "ranking": ranking,
                "first_context_ms": first_ms,
                "top1_pass": ranking[:1] == [expected] if expected else not ranking,
                "top3_pass": expected in ranking if expected else not ranking,
                "context_pass": expected in context if expected else not context,
            }
        )
    output = {
        "database": str(Path(config.memory.db_path).expanduser()),
        "documents": backend.count(),
        "sqlite_open_ms": open_ms,
        "configuration": {
            "top_k": ctx_config.top_k,
            "max_context_tokens": ctx_config.max_context_tokens,
        },
        "cases": rows,
        "top1_pass": sum(r["top1_pass"] for r in rows),
        "top3_pass": sum(r["top3_pass"] for r in rows),
        "context_pass": sum(r["context_pass"] for r in rows),
        "first_context_p95_ms": percentile(cold_timings, 0.95),
        "warm_context_p50_ms": statistics.median(timings),
        "warm_context_p95_ms": percentile(timings, 0.95),
        "warm_context_samples": len(timings),
        "limits": (
            "Fixed synthetic retrieval cases; no model answer, STT/TTS or full "
            "voice latency measurement. First samples follow a ranking read, "
            "so are not cold-cache disk measurements."
        ),
    }
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(output, ensure_ascii=False, indent=2) + "\n")
    print(
        json.dumps(
            {k: v for k, v in output.items() if k != "cases"},
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
