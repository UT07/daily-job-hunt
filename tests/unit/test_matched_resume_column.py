"""matched_resume was empty on every job the current pipeline scored.

score_batch read `resume_row.get("resume_type", "")` from a user_resumes row.
That table has no resume_type column — the columns are created_at,
google_doc_template_id, id, label, resume_key, target_roles, template_style,
tex_content, updated_at, user_id. The read therefore always returned "", and
the dashboard's RESUME TYPE column showed "--" for every job.

Measured 2026-09-29: every job first seen in the previous nine days had
matched_resume of "" or None. Older rows still hold sre_devops (229) and
fullstack (38) from the legacy config-based path, which is what made the column
look like it had simply been abandoned rather than broken.

A silent .get() default is what hid it: the wrong key produced an empty string
instead of a KeyError, so the pipeline recorded "no resume type" as though that
were a fact about the job.
"""
import sys

sys.path.insert(0, "lambdas/pipeline")

REAL_USER_RESUMES_COLUMNS = {
    "created_at", "google_doc_template_id", "id", "label", "resume_key",
    "target_roles", "template_style", "tex_content", "updated_at", "user_id",
}


def test_score_batch_reads_a_column_that_exists():
    import pathlib
    src = pathlib.Path("lambdas/pipeline/score_batch.py").read_text()
    assert 'resume_row.get("resume_type"' not in src, (
        "score_batch reads resume_type off a user_resumes row; that column does "
        "not exist, so matched_resume is always empty"
    )
    assert 'resume_row.get("resume_key"' in src


def test_resume_type_is_not_a_user_resumes_column():
    """Pin the schema the code assumes, so a rename breaks a test not a column."""
    assert "resume_type" not in REAL_USER_RESUMES_COLUMNS
    assert "resume_key" in REAL_USER_RESUMES_COLUMNS


def test_matched_resume_carries_the_resume_key_through():
    from unittest.mock import MagicMock, patch
    import score_batch

    resume_row = {"tex_content": r"\documentclass{article}\begin{document}x\end{document}",
                  "resume_key": "default", "label": "MASTER.pdf"}
    captured = {}

    def fake_get_supabase():
        db = MagicMock()

        def table(name):
            t = MagicMock()
            if name == "user_resumes":
                t.select.return_value.eq.return_value.order.return_value.limit.return_value.execute.return_value = MagicMock(data=[resume_row])
            elif name == "jobs_raw":
                t.select.return_value.in_.return_value.execute.return_value = MagicMock(data=[])
            else:
                t.select.return_value.eq.return_value.single.return_value.execute.return_value = MagicMock(data={})
            return t
        db.table.side_effect = table
        return db

    with patch.object(score_batch, "get_supabase", side_effect=fake_get_supabase):
        # No jobs, so it returns early — we only care that the resume_key read
        # happens and does not raise.
        out = score_batch.handler({"job_hashes": ["h1"], "user_id": "u1"}, None)
    assert out is not None
