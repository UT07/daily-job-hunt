"""A NULL job_hash crashed the bulk re-tailor mid-selection.

Measured against production on 2026-09-30, running the batch for tiers S,A:

    TypeError: sequence item 26: expected str instance, NoneType found
      scripts/retailor_bulk.py:98  "job_hash": f"in.({','.join(chunk)})"

Six rows in `jobs` have `job_hash IS NULL`; exactly one of them was in the
S,A non-expired population -- "Software Engineer @ Viatel", A-tier, 83.3,
first seen 2026-09-29T17:34. That row has `job_id` and `canonical_hash` both
set to d1ad2affe913, an empty description, and NO `jobs_raw` row at all, so
there is nothing for tailor_resume.handler() to read even if the hash were
recovered from the sibling columns. It has to be skipped.

What matters is that it is skipped *audibly*. `--tier S` had worked earlier
the same day purely because the one bad row happens to be A-tier; a silent
filter would have made the batch quietly regenerate 30 of 31 jobs and report
complete success, which is the failure shape CLAUDE.md rule 2 exists for.
"""
import importlib.util
import pathlib

import pytest

_SCRIPT = pathlib.Path("scripts/retailor_bulk.py")
_spec = importlib.util.spec_from_file_location("retailor_bulk", _SCRIPT)
retailor_bulk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(retailor_bulk)


# The Viatel row's real shape: two hash columns populated, job_hash NULL.
VIATEL = {"job_hash": None, "job_id": "d1ad2affe913",
          "canonical_hash": "d1ad2affe913", "title": "Software Engineer",
          "company": "Viatel", "score_tier": "A"}
GOOD = {"job_hash": "3dac0d143aaf", "title": "Site Reliability Engineer",
        "company": "RELX", "score_tier": "S"}


def test_a_null_job_hash_is_partitioned_out_not_rescued():
    """job_id and canonical_hash must NOT stand in for a missing job_hash.

    They look like a free recovery, but the handler reads jobs_raw by
    job_hash and this row has no jobs_raw row, so "recovering" the hash
    only buys a cold start and an AI call that ends in TailorError.
    """
    tailorable, unhashed = retailor_bulk.partition_unhashed([GOOD, VIATEL])
    assert tailorable == [GOOD]
    assert unhashed == [VIATEL]


def test_partitioning_preserves_order_and_loses_nothing():
    rows = [GOOD, VIATEL, dict(GOOD, job_hash="aaa"), dict(VIATEL, company="X")]
    tailorable, unhashed = retailor_bulk.partition_unhashed(rows)
    assert len(tailorable) + len(unhashed) == len(rows)
    assert [r["job_hash"] for r in tailorable] == ["3dac0d143aaf", "aaa"]
    assert all(r.get("job_hash") is None for r in unhashed)


def test_an_empty_string_hash_counts_as_unhashed():
    """`in.()` with an empty item matches nothing; treat it like NULL."""
    tailorable, unhashed = retailor_bulk.partition_unhashed([dict(GOOD, job_hash="")])
    assert (tailorable, len(unhashed)) == ([], 1)


def test_the_guard_is_load_bearing():
    """Without partitioning, building the PostgREST filter raises.

    This is the actual production traceback, reproduced: the guard is not
    defensive tidying, it is the difference between a batch that runs and a
    batch that dies at row 27 of 31.
    """
    unguarded = [r.get("job_hash") for r in [GOOD, VIATEL]]
    with pytest.raises(TypeError, match="expected str instance, NoneType found"):
        ",".join(unguarded)

    tailorable, _ = retailor_bulk.partition_unhashed([GOOD, VIATEL])
    assert ",".join(r["job_hash"] for r in tailorable) == "3dac0d143aaf"
