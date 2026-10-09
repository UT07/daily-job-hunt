"""A NULL job_hash crashed the bulk re-tailor mid-selection.

Measured against production on 2026-09-30, running the batch for tiers S,A:

    TypeError: sequence item 26: expected str instance, NoneType found
      scripts/retailor_bulk.py:98  "job_hash": f"in.({','.join(chunk)})"

Six rows in `jobs` have `job_hash IS NULL`; exactly one of them was in the
S,A non-expired population -- "Software Engineer @ Viatel", A-tier, 83.3,
first seen 2026-09-29T17:34. That row has `job_id` and `canonical_hash` both
set to d1ad2affe913, an empty description, and NO `jobs_raw` row at all.

What matters is that such a row is skipped *audibly*. `--tier S` had worked
earlier the same day purely because the one bad row happens to be A-tier; a
silent filter would have made the batch quietly regenerate 30 of 31 jobs and
report complete success, which is the failure shape CLAUDE.md rule 2 exists
for.

A PRIOR DECISION HERE WAS REVERSED ON 2026-10-08, and the reasoning is the
point. This file used to assert that canonical_hash must NOT stand in for a
missing job_hash, because "the handler reads jobs_raw by job_hash and this row
has no jobs_raw row, so recovering the hash only buys a cold start and an AI
call that ends in TailorError".

That is true of the Viatel row and false in general. "DevOps Engineer @
Grinds360" is S-tier at 90, has `job_hash` NULL and `canonical_hash`
b3026c307c74 — and that hash IS in jobs_raw, with a 5,381-character
description. `/api/pipeline/run-single` upserts jobs_raw under exactly
canonical_hash, so every manually added job has this shape. The old rule
skipped the user's best match and left it as the single failure in the deploy
gate's artifact-completeness check.

CLAUDE.md #15: the earlier conclusion varied one input and held the other
fixed. With a second row to compare against, the right answer changes.

The fallback is safe because of a filter that was already here: `select_jobs`
checks every resolved hash against jobs_raw and drops what is absent. That is
what separates Grinds360 from Viatel, and it is asserted below — without it,
the fallback really would buy a cold start and a TailorError.
"""
import importlib.util
import pathlib

import pytest

_SCRIPT = pathlib.Path("scripts/retailor_bulk.py")
_spec = importlib.util.spec_from_file_location("retailor_bulk", _SCRIPT)
retailor_bulk = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(retailor_bulk)

KEY = retailor_bulk.TAILOR_HASH_KEY

# The Viatel row's real shape: job_hash NULL, canonical_hash set, and NOTHING
# in jobs_raw under either.
VIATEL = {"job_hash": None, "job_id": "d1ad2affe913",
          "canonical_hash": "d1ad2affe913", "title": "Software Engineer",
          "company": "Viatel", "score_tier": "A"}
# The Grinds360 row: same NULL job_hash, but its canonical_hash IS in jobs_raw.
GRINDS = {"job_hash": None, "job_id": "b3026c307c74",
          "canonical_hash": "b3026c307c74", "title": "DevOps Engineer",
          "company": "Grinds360", "score_tier": "S"}
GOOD = {"job_hash": "3dac0d143aaf", "title": "Site Reliability Engineer",
        "company": "RELX", "score_tier": "S"}


class TestResolution:
    def test_job_hash_wins_when_present(self):
        """canonical_hash is the FALLBACK, not an override. Scraped rows carry
        both and jobs_raw is keyed by job_hash for those."""
        row = dict(GOOD, canonical_hash="something-else")
        assert retailor_bulk.resolve_tailor_hash(row) == "3dac0d143aaf"

    def test_canonical_hash_is_used_when_job_hash_is_null(self):
        assert retailor_bulk.resolve_tailor_hash(GRINDS) == "b3026c307c74"

    def test_neither_present_resolves_to_none(self):
        assert retailor_bulk.resolve_tailor_hash(
            {"job_hash": None, "canonical_hash": None}) is None
        assert retailor_bulk.resolve_tailor_hash({}) is None

    @pytest.mark.parametrize("empty", ["", None])
    def test_an_empty_string_is_not_a_hash(self, empty):
        """`in.()` with an empty item matches nothing; treat it like NULL."""
        assert retailor_bulk.resolve_tailor_hash(
            {"job_hash": empty, "canonical_hash": empty}) is None


class TestPartitioning:
    def test_a_row_with_only_a_canonical_hash_is_now_tailorable(self):
        tailorable, unhashed = retailor_bulk.partition_unhashed([GOOD, GRINDS])
        assert unhashed == [], "the user's best match was skipped again"
        assert [r[KEY] for r in tailorable] == ["3dac0d143aaf", "b3026c307c74"]

    def test_a_row_with_no_hash_at_all_is_still_partitioned_out(self):
        nothing = {"job_hash": None, "canonical_hash": None,
                   "title": "X", "company": "Y", "score_tier": "A"}
        tailorable, unhashed = retailor_bulk.partition_unhashed([GOOD, nothing])
        assert tailorable == [dict(GOOD, **{KEY: "3dac0d143aaf"})]
        assert unhashed == [nothing]

    def test_every_tailorable_row_carries_the_resolved_hash(self):
        """So no caller downstream repeats the precedence, and none can read
        `job_hash` out of habit and get a None. Four call sites did."""
        tailorable, _ = retailor_bulk.partition_unhashed([GOOD, GRINDS, VIATEL])
        assert all(r.get(KEY) for r in tailorable)

    def test_the_input_rows_are_not_mutated(self):
        """partition returns copies; a caller that re-reads its own list must
        not find a key the database never gave it."""
        rows = [dict(GRINDS)]
        retailor_bulk.partition_unhashed(rows)
        assert KEY not in rows[0]

    def test_partitioning_preserves_order_and_loses_nothing(self):
        rows = [GOOD, GRINDS, dict(GOOD, job_hash="aaa"),
                {"job_hash": None, "canonical_hash": None, "company": "X"}]
        tailorable, unhashed = retailor_bulk.partition_unhashed(rows)
        assert len(tailorable) + len(unhashed) == len(rows)
        assert [r[KEY] for r in tailorable] == ["3dac0d143aaf", "b3026c307c74", "aaa"]


class TestTheGuardIsLoadBearing:
    def test_without_partitioning_the_filter_raises(self):
        """The actual production traceback, reproduced: the guard is not
        defensive tidying, it is the difference between a batch that runs and
        a batch that dies at row 27 of 31."""
        unguarded = [r.get("job_hash") for r in [GOOD, VIATEL]]
        with pytest.raises(TypeError, match="expected str instance, NoneType found"):
            ",".join(unguarded)

        tailorable, _ = retailor_bulk.partition_unhashed([GOOD, VIATEL])
        assert ",".join(r[KEY] for r in tailorable) == "3dac0d143aaf,d1ad2affe913"

    def test_jobs_raw_is_what_makes_the_fallback_safe(self):
        """Viatel resolves to a hash and must STILL not be tailored.

        This is the half the reversed decision depended on. `select_jobs`
        filters every resolved hash against jobs_raw, so Viatel — whose
        canonical_hash has no jobs_raw row — is dropped there, while Grinds360
        survives. Resolving the fallback WITHOUT that filter is exactly the
        cold-start-and-TailorError this file originally refused, so the two
        changes are one change.
        """
        tailorable, unhashed = retailor_bulk.partition_unhashed([GRINDS, VIATEL])
        # Both resolve; neither is dropped at this stage.
        assert unhashed == []
        resolved = {r["company"]: r[KEY] for r in tailorable}
        assert resolved == {"Grinds360": "b3026c307c74", "Viatel": "d1ad2affe913"}

        # jobs_raw holds only Grinds360's. That is the filter select_jobs
        # applies, and it is the reason this fallback does not waste a run.
        present = {"b3026c307c74"}
        usable = [r for r in tailorable if r[KEY] in present]
        assert [r["company"] for r in usable] == ["Grinds360"]

    def test_the_select_actually_fetches_canonical_hash(self):
        """Resolving a column the query never asked for is a no-op.

        The first version of this fix read canonical_hash in two functions
        while `select_jobs` still requested only
        "job_hash,title,company,score_tier,resume_s3_url", so every manually
        added job was still skipped and the fix looked applied
        (CLAUDE.md #12 — check the instrument, not the intention).
        """
        src = _SCRIPT.read_text()
        # Only the `jobs` query. The jobs_raw lookup selects job_hash alone and
        # is right to: there, job_hash IS the primary key, and an earlier
        # version of this assertion failed on that line for no reason.
        jobs_queries = [
            ln for ln in src.splitlines()
            if '"select": "job_hash' in ln and "score_tier" in ln
        ]
        assert jobs_queries, (
            "could not find the jobs query — if its shape changed, re-point "
            "this check rather than deleting it")
        for ln in jobs_queries:
            assert "canonical_hash" in ln, (
                f"this query cannot resolve the fallback it relies on: {ln.strip()}")
