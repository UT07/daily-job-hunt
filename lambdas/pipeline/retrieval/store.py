"""pgvector read/write helpers.

Similarity is computed server-side by pgvector for indexed queries; the local
cosine() exists for threshold tuning and tests, where pulling rows is cheaper
than a round trip.

Import resolution for `ai_helper` mirrors embeddings.py and
lambdas/pipeline/agents/_ai_helper.py: this package lives inside the pipeline
Lambdas' own CodeUri (template.yaml: CodeUri: lambdas/pipeline/), which
SAM/CFN flattens into /var/task at deploy time, making `ai_helper` a flat
sibling module there -- identical to the shape tests/conftest.py creates by
putting lambdas/pipeline on sys.path. The container-image Lambda
(Dockerfile.lambda) instead ships the whole lambdas/ package tree, where only
the qualified `lambdas.pipeline` import resolves. Try the flat import first
since it covers both tests and the deployed zip Lambda; fall back to the
qualified import only for the container-image shape. Never spell this as
`from lambdas.pipeline.ai_helper import ...` -- that form cannot resolve once
CodeUri flattens this directory in the zip-based pipeline Lambdas.
"""
import json
import logging
import math

try:
    import ai_helper  # flat import -- resolves under pytest and in the zip Lambda
except ImportError:
    from lambdas.pipeline import ai_helper  # container-image shape only

logger = logging.getLogger()


def _db():
    return ai_helper.get_supabase()


def _as_vector(v: list[float] | str) -> list[float]:
    """Coerce a value that may be PostgREST's wire form of a `vector` column.

    Confirmed live (Task 15 threshold tuning): `.table("jobs").select(...)`
    on a pgvector column does not come back as a JSON array the way every
    other column type does -- Postgres has no native JSON cast for `vector`,
    so PostgREST falls back to its text output, e.g. `"[0.001,-0.02,...]"` as
    one big string. That text happens to already be valid JSON-array syntax,
    so json.loads round-trips it exactly. RPC-returned similarities
    (similar_jobs_in_company, similar_bullets) never hit this: pgvector's
    `<=>` operator runs server-side there and returns a plain float, not a
    vector column. Only a caller that pulls raw embedding columns directly
    (this module's own docstring: "threshold tuning ... pulling rows is
    cheaper than a round trip") ever sees the string form.
    """
    return json.loads(v) if isinstance(v, str) else v


def cosine(a: list[float] | str, b: list[float] | str) -> float:
    """Cosine similarity between two vectors.

    Accepts either a plain list of floats or PostgREST's stringified `vector`
    column form (see _as_vector) -- both a hand-built query vector and a row
    pulled straight from `jobs.embedding` are valid inputs.

    Returns 0.0 for a zero-magnitude vector instead of raising -- an
    all-zero embedding is a real possibility from a degenerate input (e.g.
    empty or whitespace-only text reaching the embedding client), and that's
    a "no signal" case, not one worth crashing the caller over.
    """
    a = _as_vector(a)
    b = _as_vector(b)
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def upsert_job_embedding(job_hash: str, vector: list[float]) -> None:
    """Write (or overwrite) a job's description embedding."""
    _db().table("jobs").update({"embedding": vector}).eq("job_hash", job_hash).execute()


def similar_jobs_in_company(company: str, vector: list[float], threshold: float) -> list[dict]:
    """Jobs at the same company whose description embedding is near `vector`.

    Scoped by company because two genuinely different roles at different
    employers can have near-identical descriptions. The RPC already applies
    p_threshold server-side; the >= re-check here is cheap insurance against
    a future RPC change silently dropping that filter.
    """
    rows = _db().rpc(
        "match_jobs_in_company",
        {"p_company": company, "p_embedding": vector, "p_threshold": threshold},
    ).execute().data or []
    return [r for r in rows if r.get("similarity", 0) >= threshold]


def similar_bullets(user_id: str, vector: list[float], k: int = 8) -> list[dict]:
    """Top-k resume bullets for this user, nearest first."""
    rows = _db().rpc(
        "match_resume_bullets",
        {"p_user_id": user_id, "p_embedding": vector, "p_k": k},
    ).execute().data or []
    return rows[:k]
