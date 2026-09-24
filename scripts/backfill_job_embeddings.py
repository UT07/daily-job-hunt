#!/usr/bin/env python3
"""Embed every scored job that has a usable description.

Batched and cached, so re-running is cheap and interrupted runs resume: the
row selection only looks at `embedding is null`, and embed_batch() caches by
content hash in `ai_cache`, so a crash mid-run (e.g. a Gemini rate limit)
leaves already-embedded rows untouched and picks up the rest on the next run.

Usage:
    source .venv/bin/activate
    python scripts/backfill_job_embeddings.py
"""
import sys
from pathlib import Path

# lambdas/pipeline is this repo's CodeUri root for the pipeline Lambdas, so
# retrieval/*.py resolve `ai_helper` as a flat sibling import there (see the
# docstring in retrieval/embeddings.py). Putting it on sys.path here mirrors
# scripts/rescore_batch.py, which imports lambdas/pipeline modules the same
# way -- never `from lambdas.pipeline...`, which a test now forbids for
# anything under lambdas/pipeline/ and which cannot resolve once CodeUri
# flattens that directory into a zip Lambda's /var/task.
ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lambdas" / "pipeline"))

from ai_helper import get_supabase  # noqa: E402
from retrieval.dedup import MIN_DESCRIPTION_CHARS  # noqa: E402
from retrieval.embeddings import embed_batch  # noqa: E402
from retrieval.store import upsert_job_embedding  # noqa: E402

BATCH = 50

# PostgREST caps a single response at 1000 rows by default (confirmed live:
# selecting all 1,243 null-embedding rows with no .range() silently returned
# only 1000, with no error). Fetch in pages so a dataset already past that
# size on day one doesn't quietly drop the tail -- exactly the kind of gap
# that would undercount "usable" below and leave real rows unbackfilled.
PAGE = 1000


def _fetch_jobs_without_embeddings(db) -> list[dict]:
    rows: list[dict] = []
    offset = 0
    while True:
        page = db.table("jobs").select("job_hash, title, description") \
            .is_("embedding", "null").range(offset, offset + PAGE - 1).execute().data or []
        rows.extend(page)
        if len(page) < PAGE:
            return rows
        offset += PAGE


def main() -> None:
    db = get_supabase()
    rows = _fetch_jobs_without_embeddings(db)
    # job_hash is a nullable FK (db/migrations/003_phase2e_tables.sql: `ADD
    # COLUMN job_hash TEXT REFERENCES jobs_raw(job_hash)`, no NOT NULL). Found
    # live on the first backfill run: 4 of 1,243 rows have job_hash=None, so
    # upsert_job_embedding's `.eq("job_hash", None)` matches zero rows in
    # PostgREST (it is not the same as `IS NULL`) -- the embed API call and
    # cache write happen, but the DB write silently no-ops. Skip them up
    # front instead of burning Gemini quota on writes that can never land;
    # they cannot be targeted for update on job_hash at all until whatever
    # produced a null hash for them is fixed separately.
    no_hash = [r for r in rows if not r.get("job_hash")]
    usable = [
        r for r in rows
        if r.get("job_hash") and len((r.get("description") or "")) >= MIN_DESCRIPTION_CHARS
    ]
    print(
        f"{len(rows)} without embeddings, {len(usable)} with usable descriptions "
        f"({len(no_hash)} skipped for null job_hash, "
        f"{len(rows) - len(usable) - len(no_hash)} skipped for short description)"
    )

    completed = 0
    try:
        for i in range(0, len(usable), BATCH):
            chunk = usable[i:i + BATCH]
            vectors = embed_batch([f"{r['title']}\n\n{r['description']}" for r in chunk])
            for row, vector in zip(chunk, vectors):
                upsert_job_embedding(row["job_hash"], vector)
            completed = min(i + BATCH, len(usable))
            print(f"  {completed}/{len(usable)}")
    except Exception:
        # Do not retry-loop on a rate limit or any other mid-run failure --
        # report progress and let the caller re-run later. Already-embedded
        # rows are skipped next time (embedding is no longer null), and
        # embed_batch's content-hash cache means no wasted API calls either.
        print(f"FAILED after {completed}/{len(usable)} embedded -- safe to re-run, it resumes.")
        raise


if __name__ == "__main__":
    main()
