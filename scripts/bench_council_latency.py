"""Measure council latency on both engines against the same prompt.

Produces the before/after number for spec section 14.1. Serial fan-out versus
Send-API fan-out is the thing being measured.
"""
import os
import statistics
import sys
import time
from pathlib import Path

# Run as `python scripts/bench_council_latency.py` (per the task brief), plain
# script invocation only puts scripts/ on sys.path, not the repo root, so
# `lambdas` (a real package — lambdas/__init__.py exists) isn't importable.
# Pytest gets this for free via its own rootdir insertion; a bare script
# doesn't. Same fix already used by scripts/batch_generate.py,
# scripts/backfill_jobs.py and scripts/rescore_batch.py.
sys.path.insert(0, str(Path(__file__).parent.parent))

PROMPT = "Summarise why a backend engineer with Python and AWS suits a platform role."
RUNS = 5


def bench(engine: str) -> dict:
    os.environ["COUNCIL_ENGINE"] = engine
    from importlib import reload
    from lambdas.pipeline import ai_helper
    reload(ai_helper)

    timings = []
    for _ in range(RUNS):
        start = time.perf_counter()
        ai_helper.council_complete(PROMPT, task_description="benchmark", n_generators=2)
        timings.append(time.perf_counter() - start)
    timings.sort()
    return {
        "engine": engine,
        "p50": round(statistics.median(timings), 2),
        "p95": round(timings[int(len(timings) * 0.95) - 1], 2),
    }


if __name__ == "__main__":
    for engine in ("legacy", "langgraph"):
        print(bench(engine))
