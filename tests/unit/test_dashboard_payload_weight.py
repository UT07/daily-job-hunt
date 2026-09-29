"""The dashboard shipped a 768-dimension embedding to the browser, per job.

Measured against production on 2026-09-29, 100 rows of `jobs`:

    total                     1987 KB
      embedding                931 KB   46.8%   <- never read by the frontend
      description              460 KB   23.2%
      linkedin_contacts        121 KB    6.1%
      requirement_map           97 KB    4.9%

`embedding` is the pgvector column used for semantic dedup and bullet
retrieval. It is server-side data. Nothing in web/src references it, yet
select("*") sent it across Supabase -> Lambda -> API Gateway -> browser on
every page load, and the browser then parsed it as JSON.

Same rows without it measured 0.162s and 183 KB, against 0.423s and 1987 KB.
"""
import pathlib
import re

SRC = pathlib.Path("db_client.py").read_text()


def _get_jobs_source() -> str:
    i = SRC.index("def get_jobs(")
    j = SRC.index("\n    def ", i + 10)
    return SRC[i:j]


def test_the_list_query_does_not_select_star():
    body = _get_jobs_source()
    assert 'select("*"' not in body, (
        "get_jobs uses select(*), which ships the pgvector embedding column "
        "(47% of the payload) to the browser on every page load"
    )


def test_the_list_query_excludes_the_embedding_column():
    body = _get_jobs_source()
    assert "JOB_LIST_COLUMNS" in body, "get_jobs should select an explicit column list"
    cols = re.search(r"JOB_LIST_COLUMNS\s*=\s*\(([^)]*)\)", SRC, re.S)
    assert cols, "JOB_LIST_COLUMNS not found"
    listed = {c.strip() for c in cols.group(1).replace('"', "").replace("\n", "").split(",") if c.strip()}
    assert "embedding" not in listed, "embedding is back in the dashboard payload"


def test_every_field_the_list_views_render_is_still_selected():
    """Trimming columns is only safe if the UI's reads are covered.

    These are the fields web/src/components/JobTable.jsx, pages/Dashboard.jsx,
    pages/Artifacts.jsx and components/PastJobsSection.jsx actually read off a
    job, plus the two keys _refresh_s3_urls needs to re-sign URLs.
    """
    cols = re.search(r"JOB_LIST_COLUMNS\s*=\s*\(([^)]*)\)", SRC, re.S)
    listed = {c.strip() for c in cols.group(1).replace('"', "").replace("\n", "").split(",") if c.strip()}
    needed = {
        "application_status", "apply_url", "archetype", "ats_score", "company",
        "cover_letter_s3_url", "description", "first_seen",
        "hiring_manager_score", "is_expired", "job_hash", "job_id",
        "key_matches", "level_fit", "linkedin_contacts", "location",
        "match_score", "matched_resume", "posted_date", "remote",
        "resume_doc_url", "resume_s3_url", "score_tier", "source",
        "tailoring_model", "tech_recruiter_score", "title",
        # _refresh_s3_urls re-signs from these
        "resume_s3_key",
    }
    missing = sorted(needed - listed)
    assert not missing, f"the list view reads these but they are no longer selected: {missing}"


def test_both_s3_key_columns_are_selected():
    """_refresh_s3_urls re-signs from resume_s3_key AND cover_letter_s3_key.

    A column the list omits is a column _refresh_s3_urls sees as None, so the
    URL is never re-signed and the link dies after 7 days. This regressed
    immediately: the explicit column list was introduced before
    cover_letter_s3_key existed, and adding the column later did not add it
    here — so the re-signing fix never reached the dashboard at all.
    """
    cols = re.search(r"JOB_LIST_COLUMNS\s*=\s*\(([^)]*)\)", SRC, re.S)
    listed = {c.strip() for c in cols.group(1).replace('"', "").replace("\n", "").split(",") if c.strip()}
    for key in ("resume_s3_key", "cover_letter_s3_key"):
        assert key in listed, f"{key} is not selected; _refresh_s3_urls cannot re-sign it"
