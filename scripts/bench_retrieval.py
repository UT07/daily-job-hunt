#!/usr/bin/env python3
"""Retrieval quality and fabrication impact (Task 18).

Part 1 sweeps k (and attempts ef_search) for the recall/latency table.
Part 2 is the number that matters: fabrication rate with the evidence pool
off vs on, measured by the guard already used in production
(tailor_resume._check_fabrication), by actually running the real,
unmodified tailor_resume.handler() for the same jobs twice -- once per
BULLET_RAG setting. Both arms call the identical handler() function with an
identical event dict and the identical council_complete configuration
(n_generators=2, temperature=0.3); the only thing that differs between them
is the BULLET_RAG env var, which safe_evidence_block() reads at call time.
That is what "differ ONLY in the flag" requires, and the only way to
guarantee it without re-deriving (and risking drift from) the real
tailoring prompt/validation/retry logic is to call handler() itself.

handler() is production code: it really does `db.table("jobs").update(...)`
and a real S3 `put_object`. This benchmark must not write to jobs/jobs_raw/
user_resumes (Task 18 constraint) and has no reason to write real S3
objects, so both are intercepted -- see GuardedDB / _GuardedTable below.
Reads pass straight through to the real Supabase client; only mutating
calls on the three guarded tables are swallowed. The tailored tex itself is
recovered from the mocked S3 client's captured put_object() call instead of
round-tripping through a real bucket.

Usage:
    source .venv/bin/activate
    python scripts/bench_retrieval.py                    # sweep + fabrication, N=10/arm
    BENCH_USER_ID=<uuid> python scripts/bench_retrieval.py
    BENCH_N=6 python scripts/bench_retrieval.py           # smaller N if rate-limited
    BENCH_SKIP_SWEEP=1 python scripts/bench_retrieval.py         # fabrication only
    BENCH_SKIP_FABRICATION=1 python scripts/bench_retrieval.py   # sweep only
"""
import logging
import os
import re
import sys
import time
from pathlib import Path
from statistics import mean
from unittest.mock import MagicMock, patch

# lambdas/pipeline is this repo's CodeUri root for the pipeline Lambdas, so
# retrieval/*.py and tailor_resume.py resolve `ai_helper` as a flat sibling
# import there (see the docstring in retrieval/embeddings.py). Putting it on
# sys.path here mirrors scripts/backfill_job_embeddings.py and
# scripts/tune_dedup_threshold.py -- never `from lambdas.pipeline...`, which
# a test forbids for anything under lambdas/pipeline/ and which cannot
# resolve once CodeUri flattens that directory into a zip Lambda's /var/task.
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lambdas" / "pipeline"))

import ai_helper  # noqa: E402
import tailor_resume  # noqa: E402
from retrieval.bullets import index_bullets, retrieve_evidence  # noqa: E402

logging.basicConfig(level=logging.INFO, format="%(message)s")

# Only 2 user_resumes rows exist in prod today, both for this user (per Task
# 18 ground truth); used as the BENCH_USER_ID default so the script runs
# out of the box without requiring the caller to look the id up first.
DEFAULT_USER_ID = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"

K_VALUES = [4, 8, 12]
EF_VALUES = [40, 80, 160]
GUARDED_TABLES = {"jobs", "jobs_raw", "user_resumes"}
MIN_DESCRIPTION_CHARS = 500  # skip the "0-char description" jobs_raw rows (backlog item)


# ---------------------------------------------------------------------------
# Write guard for the fabrication comparison
# ---------------------------------------------------------------------------

class _NoopResult:
    data = []
    count = None


class _NoopQuery:
    """Swallows any chained filter call and returns an empty result."""

    def __getattr__(self, _name):
        return lambda *a, **kw: self

    def execute(self):
        return _NoopResult()


class _GuardedTable:
    """Reads pass through to the real table; writes are captured, not sent.

    handler() as written today only ever writes to `jobs` (an update after
    a successful tailor). insert/upsert/delete are guarded too, defensively,
    so a future change to handler() can't silently start writing to
    jobs_raw or user_resumes through this benchmark.
    """

    def __init__(self, real, sink):
        self._real = real
        self._sink = sink

    def select(self, *a, **kw):
        return self._real.select(*a, **kw)

    def update(self, payload, *a, **kw):
        self._sink.append(dict(payload))
        return _NoopQuery()

    def insert(self, *a, **kw):
        return _NoopQuery()

    def upsert(self, *a, **kw):
        return _NoopQuery()

    def delete(self, *a, **kw):
        return _NoopQuery()


class GuardedDB:
    """Wraps the real Supabase client so handler() runs unmodified with zero
    risk of writing to jobs/jobs_raw/user_resumes. `blocked_updates` records
    what WOULD have been written (e.g. tailoring_model) purely so this
    benchmark can report which provider/model produced each sample --
    handler()'s own return value doesn't include that.
    """

    def __init__(self, real_db):
        self._real = real_db
        self.blocked_updates: list[dict] = []

    def table(self, name):
        real_table = self._real.table(name)
        if name in GUARDED_TABLES:
            return _GuardedTable(real_table, self.blocked_updates)
        return real_table

    def rpc(self, *a, **kw):
        return self._real.rpc(*a, **kw)


def tailor_guarded(real_db, job_hash: str, user_id: str, bullet_rag: bool) -> dict:
    """Run the real tailor_resume.handler() for one job with BULLET_RAG set
    for this call only. Returns the recovered tailored tex plus bookkeeping.
    Never touches jobs/jobs_raw/user_resumes or a real S3 bucket.
    """
    os.environ["BULLET_RAG"] = "on" if bullet_rag else "off"
    guarded = GuardedDB(real_db)
    mock_boto3 = MagicMock()
    t0 = time.perf_counter()
    try:
        with patch.object(tailor_resume, "get_supabase", return_value=guarded), \
             patch.object(tailor_resume, "boto3", mock_boto3):
            result = tailor_resume.handler({"job_hash": job_hash, "user_id": user_id}, None)
    except tailor_resume.TailorError as exc:
        return {"ok": False, "error": str(exc), "duration_s": time.perf_counter() - t0}
    except Exception as exc:  # noqa: BLE001 - a benchmark must not die on one bad job
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "duration_s": time.perf_counter() - t0}
    duration = time.perf_counter() - t0

    put_call = mock_boto3.client.return_value.put_object.call_args
    tex = put_call.kwargs["Body"].decode("utf-8") if put_call else ""
    tailoring_model = guarded.blocked_updates[-1].get("tailoring_model") if guarded.blocked_updates else None
    return {
        "ok": True,
        "tex": tex,
        "used_fallback": bool(result.get("used_fallback")),
        "tailoring_model": tailoring_model,
        "duration_s": duration,
    }


# ---------------------------------------------------------------------------
# Part 1: retrieval sweep
# ---------------------------------------------------------------------------

def attempt_set_ef_search(db, ef: int) -> tuple[bool, str]:
    """Try the brief's approach to controlling hnsw.ef_search through the
    Supabase REST client. Returns (worked, message)."""
    try:
        db.rpc(
            "set_config",
            {"setting_name": "hnsw.ef_search", "new_value": str(ef), "is_local": False},
        ).execute()
        return True, "ok"
    except Exception as exc:  # noqa: BLE001 - reporting the failure IS the point here
        return False, str(exc)[:400]


def sweep(db, user_id: str, jds: list[str]) -> None:
    print("\n=== Part 1: retrieval sweep ===\n")

    print("Attempting hnsw.ef_search control via db.rpc('set_config', ...) per the brief:")
    ef_controllable = False
    for ef in EF_VALUES:
        ok, msg = attempt_set_ef_search(db, ef)
        ef_controllable = ef_controllable or ok
        print(f"  ef={ef}: {'OK' if ok else 'FAILED'} - {msg}")
    if not ef_controllable:
        print(
            "\nef_search could NOT be controlled through the Supabase REST client: "
            "public.set_config is not an exposed RPC (Postgres's set_config lives in "
            "pg_catalog; only match_jobs_in_company and match_resume_bullets are "
            "exposed under public, per supabase/migrations/20260922000100_pgvector_rpcs.sql). "
            "Even if a public wrapper existed, PostgREST's connection pooling gives no "
            "guarantee that a non-local SET from one request lands on the same pooled "
            "connection as the next RPC call, so this axis is not reliably controllable "
            "through this client regardless. Reporting the k-only sweep below instead of "
            "a 9-row k x ef table -- that table would not reflect 3 genuinely different "
            "ef_search values, just the same value measured 3 times under a false label."
        )

    print(f"\n{len(jds)} job descriptions, resume_bullets corpus size below.")
    corpus_size = db.table("resume_bullets").select("id", count="exact").eq("user_id", user_id).execute().count
    print(f"resume_bullets for this user: {corpus_size} rows (single-user corpus -- see caveat below)\n")

    print("k\tp50_ms\tmean_top_sim\tmean_all_sim\tmean_weakest_sim\tavg_n_rows")
    for k in K_VALUES:
        timings, top_sims, all_sims, weakest_sims, n_rows = [], [], [], [], []
        for jd in jds:
            start = time.perf_counter()
            rows = retrieve_evidence(user_id, jd, k=k)
            timings.append((time.perf_counter() - start) * 1000)
            if rows:
                top_sims.append(rows[0].get("similarity", 0))
                weakest_sims.append(rows[-1].get("similarity", 0))
                all_sims.extend(r.get("similarity", 0) for r in rows)
                n_rows.append(len(rows))
        timings.sort()
        p50 = timings[len(timings) // 2]
        print(
            f"{k}\t{p50:.0f}\t{mean(top_sims):.3f}\t\t{mean(all_sims):.3f}\t\t"
            f"{mean(weakest_sims):.3f}\t\t{mean(n_rows):.1f}"
        )

    print(
        "\nNote: mean_top_sim (the brief's requested metric, rows[0]['similarity']) is "
        "mathematically invariant to k for a fixed corpus and JD -- similar_bullets orders "
        "by similarity descending, so the first row is the same regardless of how many rows "
        "are requested. It cannot by itself distinguish k=4 from k=8 from k=12. mean_all_sim "
        "(average similarity across all k returned rows) and mean_weakest_sim (the k-th, i.e. "
        "worst, row's similarity) are reported alongside it because they DO move with k, and "
        "are what actually answers 'does a bigger k dilute the evidence pool with weak matches.'"
    )


# ---------------------------------------------------------------------------
# Part 2: fabrication delta
# ---------------------------------------------------------------------------

def fabrication_delta(base_skills: str, with_pool: list[str], without_pool: list[str]) -> tuple[float, float]:
    """Mirrors the brief's function exactly: rate = fraction of samples for
    which _check_fabrication returns a non-empty problem list."""

    def rate(samples):
        flagged = sum(1 for s in samples if tailor_resume._check_fabrication(base_skills, s))
        return flagged / max(len(samples), 1)

    without_rate = rate(without_pool)
    with_rate = rate(with_pool)
    print(f"\nfabrication without pool: {without_rate:.1%}  (n={len(without_pool)})")
    print(f"fabrication with pool:    {with_rate:.1%}  (n={len(with_pool)})")
    return without_rate, with_rate


def select_jobs(db, n: int) -> list[dict]:
    """Deterministic sample of n jobs_raw rows with a substantive description.

    handler() reads from jobs_raw (not jobs), so candidates must exist there.
    Sorted by job_hash for a reproducible sample across runs.
    """
    rows = db.table("jobs_raw").select("job_hash,title,company,description") \
        .not_.is_("description", "null").limit(1000).execute().data or []
    candidates = [r for r in rows if len(r.get("description") or "") >= MIN_DESCRIPTION_CHARS]
    candidates.sort(key=lambda r: r["job_hash"])
    return candidates[:n]


def get_base_skills_text(db, user_id: str) -> str:
    resume = db.table("user_resumes").select("tex_content").eq("user_id", user_id) \
        .order("created_at", desc=True).limit(1).execute()
    base_tex = resume.data[0]["tex_content"]
    _, base_body = tailor_resume._split_tex(base_tex)
    match = re.search(r"\\section\*\{(?:Technical )?Skills\}(.*?)\\section\*\{", base_body, re.DOTALL)
    return match.group(1) if match else ""


def run_fabrication_comparison(db, user_id: str, n: int) -> None:
    print("\n=== Part 2: fabrication delta ===\n")
    jobs = select_jobs(db, n)
    print(f"Selected {len(jobs)} jobs (requested {n}) with description >= {MIN_DESCRIPTION_CHARS} chars:")
    for j in jobs:
        print(f"  {j['job_hash']}  {j['title']!r} @ {j['company']!r} ({len(j['description'])} chars)")

    base_skills = get_base_skills_text(db, user_id)
    print(f"\nBase resume Skills section: {len(base_skills)} chars (shared by every sample, both arms)")

    without_pool, with_pool = [], []
    detail = []
    for i, job in enumerate(jobs, 1):
        jh, title, company = job["job_hash"], job["title"], job["company"]
        print(f"\n--- job {i}/{len(jobs)}: {title!r} @ {company!r} ({jh}) ---")

        off = tailor_guarded(db, jh, user_id, bullet_rag=False)
        if off["ok"]:
            problems = tailor_resume._check_fabrication(base_skills, off["tex"])
            without_pool.append(off["tex"])
            print(
                f"  OFF: {off['duration_s']:.1f}s  fallback={off['used_fallback']}  "
                f"model={off['tailoring_model']}  fabrication={problems or 'none'}"
            )
        else:
            print(f"  OFF: FAILED - {off['error']}")
        detail.append({"job_hash": jh, "title": title, "company": company, "arm": "off", **off})

        on = tailor_guarded(db, jh, user_id, bullet_rag=True)
        if on["ok"]:
            problems = tailor_resume._check_fabrication(base_skills, on["tex"])
            with_pool.append(on["tex"])
            print(
                f"  ON:  {on['duration_s']:.1f}s  fallback={on['used_fallback']}  "
                f"model={on['tailoring_model']}  fabrication={problems or 'none'}"
            )
        else:
            print(f"  ON:  FAILED - {on['error']}")
        detail.append({"job_hash": jh, "title": title, "company": company, "arm": "on", **on})

    n_off_ok = sum(1 for d in detail if d["arm"] == "off" and d["ok"])
    n_on_ok = sum(1 for d in detail if d["arm"] == "on" and d["ok"])
    print(f"\nCompleted: {n_off_ok}/{len(jobs)} OFF, {n_on_ok}/{len(jobs)} ON")
    fallback_off = sum(1 for d in detail if d["arm"] == "off" and d.get("used_fallback"))
    fallback_on = sum(1 for d in detail if d["arm"] == "on" and d.get("used_fallback"))
    print(f"Fell back to base resume (validation failure): {fallback_off} OFF, {fallback_on} ON")

    fabrication_delta(base_skills, with_pool, without_pool)


# ---------------------------------------------------------------------------
# Fail-fast gate + main
# ---------------------------------------------------------------------------

def fail_fast_check(db, user_id: str) -> None:
    """Per Task 18: before any bulk work, confirm retrieval actually returns
    something plausible. A zero result here means something upstream is
    broken and the rest of this script would be measuring nothing."""
    existing = db.table("resume_bullets").select("id", count="exact").eq("user_id", user_id).execute().count
    if not existing:
        resume = db.table("user_resumes").select("id,tex_content").eq("user_id", user_id) \
            .order("created_at", desc=True).limit(1).execute()
        if not resume.data:
            print(f"FATAL: no user_resumes row for {user_id} -- cannot index anything. Stopping.")
            sys.exit(1)
        row = resume.data[0]
        n = index_bullets(user_id, row["tex_content"], row["id"])
        print(f"Indexed {n} bullets from user_resumes.id={row['id']} for user {user_id}")
    else:
        print(f"resume_bullets already has {existing} rows for user {user_id}, skipping index_bullets")

    sample_jd = db.table("jobs_raw").select("description").not_.is_("description", "null") \
        .limit(1).execute().data[0]["description"]
    rows = retrieve_evidence(user_id, sample_jd, k=8)
    print(f"\nFail-fast check: retrieve_evidence returned {len(rows)} rows for a sample JD.")
    for r in rows[:5]:
        print(f"  sim={r.get('similarity'):.3f} [{r.get('section')}] {r.get('text')[:100]}")
    if not rows:
        print(
            "\nFATAL: retrieve_evidence returned nothing. Something upstream is broken "
            "(embedding, RPC, or RLS) -- stopping rather than measuring a no-op."
        )
        sys.exit(1)


def main() -> None:
    user_id = os.environ.get("BENCH_USER_ID", DEFAULT_USER_ID)
    n = int(os.environ.get("BENCH_N", "10"))
    db = ai_helper.get_supabase()

    fail_fast_check(db, user_id)

    if not os.environ.get("BENCH_SKIP_SWEEP"):
        jds = [r["description"] for r in
               db.table("jobs").select("description").not_.is_("description", "null")
               .limit(20).execute().data]
        sweep(db, user_id, jds)

    if not os.environ.get("BENCH_SKIP_FABRICATION"):
        run_fabrication_comparison(db, user_id, n)


if __name__ == "__main__":
    main()
