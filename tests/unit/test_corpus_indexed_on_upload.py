r"""The corpus was a frozen snapshot nothing maintained.

index_bullets() had NO production caller until 2026-09-30 — only tests and
scripts/bench_retrieval.py. Measured on the live table 2026-09-29: 35 bullets,
5,970 characters, every one from source_resume_id 5a57e942, a manual script run
on 2026-09-25 against the APRIL resume.

Meanwhile BULLET_RAG was "on" (template.yaml) and retrieve_evidence WAS being
called from tailor_resume.py:352. So the read path was live and the write path
did not exist: tailoring was grounded in a resume the user had already
replaced, and nothing said so.

Attribution was the second half. extract_bullets tagged each bullet with its
\section* and deliberately skipped the \jobentry/\projectentry header lines, so
the corpus knew a bullet was work experience but not whose. Measured: zero
occurrences of Clover, Kraken, Purrrfect or NaukriBaba across all 35 rows. A
bullet with no employer cannot be placed under the right \jobentry — the model
has to guess.

After the fix, against resumes/fullstack.tex: 35 bullets, 20 attributed to 7
entries (Clover IT Services, Seattle Kraken, and five projects). The 15
unattributed are Skills, Education and Certifications, which have no entry to
attribute to.
"""
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")
sys.path.insert(0, "lambdas/pipeline")

from retrieval.bullets import extract_bullets  # noqa: E402

TEX = r"""
\section*{Experience}
\jobentry{Clover IT Services}{New York}{Jun 2022 -- Jul 2024}{\textbf{Engineer}}
\begin{itemize}
  \item Cut p99 latency by 43\% across 8 services.
  \item Ran the on-call rotation.
\end{itemize}
\jobentry{Seattle Kraken (NHL)}{Seattle}{Jun 2021 -- May 2022}{\textbf{Intern}}
\begin{itemize}
  \item Built the ingest pipeline.
\end{itemize}
\section*{Featured Projects}
\projectentry{Purrrfect Keys}{Jan 2026}{React Native}
\begin{itemize}
  \item An AI piano tutor.
\end{itemize}
\projectentryurl{UTWorld}{2025}{https://x.dev}{x.dev}{Next.js}
\begin{itemize}
  \item A portfolio with a CMS.
\end{itemize}
\section*{Technical Skills}
\begin{itemize}
  \item \textbf{Languages:} Python, Go
\end{itemize}
"""


def test_bullets_carry_the_job_they_belong_to():
    got = {(b["entry"], b["text"][:18]) for b in extract_bullets(TEX)}
    assert ("Clover IT Services", "Cut p99 latency by") in got
    assert ("Seattle Kraken (NHL)", "Built the ingest p") in got


def test_projects_are_attributed_too():
    entries = {b["entry"] for b in extract_bullets(TEX)}
    assert "Purrrfect Keys" in entries
    assert "UTWorld" in entries, "projectentryurl (5-arg) was not matched"


def test_a_new_section_clears_the_entry():
    """Otherwise the last job of Experience is attributed to the first
    project, and a Skills bullet inherits whatever came before it."""
    skills = [b for b in extract_bullets(TEX) if b["section"] == "Technical Skills"]
    assert skills, "no skills bullets found"
    assert all(not b["entry"] for b in skills), (
        f"skills inherited an entry: {[b['entry'] for b in skills]}"
    )


def test_entry_headers_are_not_themselves_bullets():
    """They used to be skipped entirely; now they are matched, so they must be
    consumed as headers rather than emitted as text."""
    texts = [b["text"] for b in extract_bullets(TEX)]
    assert not any("jobentry" in t or "projectentry" in t for t in texts), texts


def test_every_bullet_still_has_its_section():
    for b in extract_bullets(TEX):
        assert b["section"], b


def test_the_real_resume_attributes_every_experience_and_project_bullet():
    import pathlib
    tex = next(pathlib.Path(".").rglob("resumes/fullstack.tex")).read_text()
    items = extract_bullets(tex)
    assert len(items) >= 30, len(items)
    for b in items:
        if b["section"] in ("Experience", "Featured Projects"):
            assert b["entry"], f"unattributed: {b['section']} / {b['text'][:50]}"


# --- indexing replaces rather than accumulates ------------------------------

def test_indexing_clears_the_previous_corpus_first():
    """Appending on every upload would leave retrieval returning bullets from
    resumes the user had already replaced — the opposite of the frozen-corpus
    bug, and just as invisible."""
    import retrieval.bullets as rb
    order = []
    with patch.object(rb, "embed_batch", lambda t: [[0.0] * 768] * len(t)), \
         patch.object(rb, "_delete_user_bullets", lambda uid: order.append("delete")), \
         patch.object(rb, "_insert_bullets", lambda rows: order.append("insert")):
        rb.index_bullets("u1", TEX, "resume-1")
    assert order == ["delete", "insert"], order


def test_nothing_is_deleted_when_there_is_nothing_to_index():
    """An empty or unparseable resume must not wipe a good corpus."""
    import retrieval.bullets as rb
    calls = []
    with patch.object(rb, "_delete_user_bullets", lambda uid: calls.append(uid)), \
         patch.object(rb, "_insert_bullets", lambda rows: calls.append("insert")):
        assert rb.index_bullets("u1", r"\section*{Empty}", "r1") == 0
    assert calls == [], f"wiped the corpus for an empty resume: {calls}"


def test_a_missing_entry_column_does_not_fail_the_insert():
    """PostgREST fails the whole request on an unknown column with PGRST204.
    Indexing without attribution beats failing the user's upload."""
    import retrieval.bullets as rb
    sent = []

    class _Table:
        def insert(self, rows):
            sent.append(rows)
            return self

        def execute(self):
            if any("entry" in r for r in sent[-1]):
                raise RuntimeError("PGRST204: Could not find the 'entry' column "
                                   "of 'resume_bullets' in the schema cache")
            return MagicMock()

    with patch.object(rb.ai_helper, "get_supabase",
                      lambda: MagicMock(table=lambda _: _Table())):
        rb._insert_bullets([{"user_id": "u1", "entry": "Acme", "text": "x"}])
    assert len(sent) == 2, "did not retry without the column"
    assert "entry" not in sent[-1][0], "retry still carried the missing column"


def test_an_unrelated_database_error_is_not_swallowed():
    """Guard the guard: only the missing-column case may be retried."""
    import retrieval.bullets as rb

    class _Table:
        def insert(self, rows):
            return self

        def execute(self):
            raise RuntimeError("connection refused")

    with patch.object(rb.ai_helper, "get_supabase",
                      lambda: MagicMock(table=lambda _: _Table())):
        try:
            rb._insert_bullets([{"user_id": "u1", "text": "x"}])
        except RuntimeError as e:
            assert "connection refused" in str(e)
        else:
            raise AssertionError("a real DB error was swallowed as a missing column")
