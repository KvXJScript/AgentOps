"""AgentOps regression evals: `uv run python -m evals [--live] [--category NAME]` from `backend/`.

Prints a per-case table, writes `evals/reports/latest.md` and exits 1 if any case fails.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
from dataclasses import replace
from pathlib import Path
from typing import Any

from agentops.config import DEFAULT_EMBEDDING_MODEL
from agentops.infrastructure.embeddings.fastembed import FastEmbedEmbedder
from evals.checks import MIN_SCORE, RUNNERS, TOP_K, CaseResult, CheckFailed, Env, Skip, rank_of
from evals.index import CorpusIndex, default_ranking
from evals.live import run_live

HERE = Path(__file__).parent
CASES_FILE = HERE / "cases.json"
REPORT_FILE = HERE / "reports" / "latest.md"
STATUSES = ("pass", "fail", "xfail", "xpass", "skip")


def load_suite() -> dict[str, Any]:
    suite: dict[str, Any] = json.loads(CASES_FILE.read_text(encoding="utf-8"))
    ids = [c["id"] for c in suite["cases"]]
    duplicates = {i for i in ids if ids.count(i) > 1}
    if duplicates:
        raise SystemExit(f"duplicate case ids: {sorted(duplicates)}")
    unknown = {c["category"] for c in suite["cases"]} - RUNNERS.keys()
    if unknown:
        raise SystemExit(f"unknown categories: {sorted(unknown)}")
    return suite


async def run_case(case: dict[str, Any], env: Env) -> CaseResult:
    """A case with `known_issue` that fails is XFAIL (reported, not fatal); if it passes it is XPASS."""
    result = await _run_case(case, env)
    known = case.get("known_issue")
    if known and result.status == "fail":
        result.status, result.reason = "xfail", f"{result.reason} — known: {known}"
    elif known and result.status == "pass":
        result.status, result.reason = "xpass", f"{result.reason} — fixed? remove `known_issue`"
    return result


async def _run_case(case: dict[str, Any], env: Env) -> CaseResult:
    try:
        return await RUNNERS[case["category"]](case, env)
    except Skip as exc:
        return CaseResult(case["id"], case["category"], "skip", str(exc))
    except CheckFailed as exc:
        return CaseResult(case["id"], case["category"], "fail", str(exc), exc.metrics)
    except Exception as exc:  # a crash is a failure with its type, never a traceback with data
        return CaseResult(case["id"], case["category"], "fail", f"crashed: {type(exc).__name__}: {exc}"[:200])


async def hybrid_experiment(cases: list[dict[str, Any]], index: CorpusIndex) -> list[dict[str, Any]]:
    """Per positive, unscoped retrieval case: rank of the expected chunk under each ranker.

    `vector` is plain cosine (all adjustments off), `identifier`/`deprecated` turn on one adjustment of the
    default `RankingPolicy`, and `adopted` is the default policy itself (what production and the cases use).
    """
    adopted_policy = default_ranking()
    policies = {
        "identifier": replace(adopted_policy, deprecated_penalty=0.0),
        "deprecated": replace(adopted_policy, identifier_boost=0.0),
        "adopted": adopted_policy,
    }
    rows = []
    for case in cases:
        exp = case["expect"]
        if case["category"] != "retrieval" or "document" not in exp or "scope" in case:
            continue
        vector, _ = rank_of(await index.vector_ranking(case["query"]), exp)
        bm25_ranking = [(c, s) for c, s in index.bm25_ranking(case["query"]) if s > 0]
        bm25, _ = rank_of(bm25_ranking, exp)
        hybrid, _ = rank_of(await index.hybrid_ranking(case["query"]), exp)
        row = {"id": case["id"], "max_rank": exp["max_rank"], "vector": vector, "bm25": bm25, "hybrid": hybrid}
        for name, policy in policies.items():
            row[name], _ = rank_of(await index.ranking(case["query"], policy=policy), exp)
        rows.append(row)
    return rows


def _fmt_metrics(metrics: dict[str, Any]) -> str:
    keys = ("rank", "score", "top_score", "max_rank", "model_calls", "cited", "evidence", "refs", "analyze_attempts")
    parts = [f"{k}={metrics[k]}" for k in keys if k in metrics and metrics[k] not in (None, [])]
    return " ".join(parts)


def print_table(results: list[CaseResult]) -> None:
    width = max(len(r.id) for r in results)
    for r in results:
        tag = " LIVE" if r.live else ""
        print(f"{r.status.upper():5}{tag:5} {r.category:11} {r.id:{width}}  {_fmt_metrics(r.metrics):48} {r.reason}")


def _rank(value: int | None) -> str:
    return "–" if value is None else str(value)


def write_report(
    suite: dict[str, Any], results: list[CaseResult], experiment: list[dict[str, Any]], meta: dict[str, Any]
) -> None:
    counts = {s: sum(r.status == s for r in results) for s in STATUSES}
    lines = [
        f"# Eval report — {suite['suite']} v{suite['version']}",
        "",
        f"- Generated: {meta['generated']} (took {meta['seconds']:.1f} s)",
        f"- Embedding model: `{meta['embedding_model']}`; retrieval = exact in-memory cosine over the real chunker"
        f" + fastembed (same scoring as `PgVectorRetriever`, no HNSW); top_k={TOP_K}, min_score={MIN_SCORE}",
        f"- Corpus: {meta['documents']} documents, {meta['chunks']} chunks; model calls are scripted"
        + (" (plus LIVE cases below)" if meta["live"] else ""),
        "- **" + ", ".join(f"{counts[s]} {s}" for s in STATUSES) + "** (xfail = known weakness, see `known_issue`)",
        "",
        "| status | category | case | key numbers | reason |",
        "|---|---|---|---|---|",
    ]
    for r in results:
        status = r.status.upper() + (" (LIVE)" if r.live else "")
        reason = r.reason.replace("|", "\\|")
        lines.append(f"| {status} | {r.category} | `{r.id}` | {_fmt_metrics(r.metrics)} | {reason} |")
    lines += [
        "",
        "## Experiment: vector vs hybrid (vector + BM25, reciprocal-rank fusion k=60)",
        "",
        "Rank of the expected chunk (document + heading); `–` = not ranked (BM25: no term overlap).",
        "",
        "| case | max_rank | vector | bm25 | hybrid | change |",
        "|---|---|---|---|---|---|",
    ]
    for row in experiment:
        v, h = row["vector"], row["hybrid"]
        change = "=" if v == h else ("better" if (h or 99) < (v or 99) else "worse")
        lines.append(
            f"| `{row['id']}` | {row['max_rank']} | {_rank(v)} | {_rank(row['bm25'])} | {_rank(h)} | {change} |"
        )
    better = sum((r["hybrid"] or 99) < (r["vector"] or 99) for r in experiment)
    worse = sum((r["hybrid"] or 99) > (r["vector"] or 99) for r in experiment)
    pass_v = sum((r["vector"] or 99) <= r["max_rank"] for r in experiment)
    pass_h = sum((r["hybrid"] or 99) <= r["max_rank"] for r in experiment)
    lines += [
        "",
        f"Hybrid better on {better}, worse on {worse}, equal on {len(experiment) - better - worse} cases; "
        f"within max_rank: vector {pass_v}/{len(experiment)}, hybrid {pass_h}/{len(experiment)}.",
        "",
        "## Ranking adjustments: plain vector vs the adopted `RankingPolicy` (ADR 0007)",
        "",
        f"Defaults: {default_ranking()}. `identifier` / `deprecated` = only that adjustment on.",
        "",
        "| case | max_rank | vector | identifier | deprecated | adopted | change |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in experiment:
        v, a = row["vector"], row["adopted"]
        change = "=" if v == a else ("better" if (a or 99) < (v or 99) else "worse")
        cells = " | ".join(_rank(row[k]) for k in ("vector", "identifier", "deprecated", "adopted"))
        lines.append(f"| `{row['id']}` | {row['max_rank']} | {cells} | {change} |")
    pass_a = sum((r["adopted"] or 99) <= r["max_rank"] for r in experiment)
    better_a = sum((r["adopted"] or 99) < (r["vector"] or 99) for r in experiment)
    worse_a = sum((r["adopted"] or 99) > (r["vector"] or 99) for r in experiment)
    lines += [
        "",
        f"Adopted better on {better_a}, worse on {worse_a}, equal on {len(experiment) - better_a - worse_a} cases; "
        f"within max_rank: vector {pass_v}/{len(experiment)}, adopted {pass_a}/{len(experiment)}.",
        "",
    ]
    REPORT_FILE.parent.mkdir(parents=True, exist_ok=True)
    REPORT_FILE.write_text("\n".join(lines), encoding="utf-8")


async def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m evals", description=__doc__)
    parser.add_argument("--live", action="store_true", help="also run ≤5 cases against the configured real provider")
    parser.add_argument("--category", choices=sorted(RUNNERS), help="run one category only")
    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.WARNING)
    logging.getLogger("agentops").setLevel(logging.ERROR)

    started = time.monotonic()
    suite = load_suite()
    cases = [c for c in suite["cases"] if args.category in (None, c["category"])]
    embedder = FastEmbedEmbedder(
        DEFAULT_EMBEDDING_MODEL,  # fixed: expectations are calibrated for this model
        cache_dir=os.environ.get("EMBEDDING_CACHE_DIR") or None,  # else fastembed honours FASTEMBED_CACHE_PATH
    )
    index = await CorpusIndex.build(embedder)
    env = Env(index)
    results = [await run_case(case, env) for case in cases]
    if args.live:
        results += await run_live(env)
    experiment = await hybrid_experiment(cases, index)

    print_table(results)
    meta = {
        "generated": time.strftime("%Y-%m-%d %H:%M:%S %Z"),
        "seconds": time.monotonic() - started,
        "embedding_model": embedder.model,
        "documents": len({c.document_id for c in index.chunks}),
        "chunks": len(index.chunks),
        "live": args.live,
    }
    write_report(suite, results, experiment, meta)
    counts = {s: sum(r.status == s for r in results) for s in STATUSES}
    summary = ", ".join(f"{counts[s]} {s}" for s in STATUSES)
    print(f"\n{summary} → {REPORT_FILE.relative_to(HERE.parent)}")
    return 1 if counts["fail"] else 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
