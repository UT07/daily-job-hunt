"""Ingest must not lose the corpus.

The master a user uploads is everything about them — three pages or ten — and
its one job on ingest is to lose nothing. The previous parser could not do
that, for two compounding reasons:

  1. The prompt interpolated text[:6000]. On a real 8,490-char resume PDF,
     70.7% reached the model; on the 16,953-char master uploaded 2026-09-28,
     35.4% did. Education sat at offset 7,540 and Certifications at 8,274, so
     neither was ever sent.

  2. The JSON schema had no "projects" key. The adapter's
     parsed.get("projects") therefore always returned [], the renderer emitted
     an empty \\section*{Featured Projects} anyway, and the template's
     \\clearpage in front of it produced a blank page. Five projects became
     zero structurally — no truncation needed.

Measured end to end before the fix: an 8,490-char PDF became a compilable
two-page document holding 772 characters. 9.1% retention, and every existing
gate green — is_latex_document passed, sections_have_content passed (any ONE
of five non-empty is enough), check_section_completeness passed because it
matches header NAMES and the renderer emits all six unconditionally.

The fixture here is synthetic on purpose. The real PDF carries a phone number
and email address, and output/ was just untracked from this public repo for
exactly that reason; committing it back as a test fixture would undo that. The
99.0%-retention figure quoted in the commit was measured against the real file
locally.
"""
import sys
from unittest.mock import MagicMock

sys.path.insert(0, ".")
from resume_parser import (  # noqa: E402
    MAX_SECTION_CHARS,
    _heading_of,
    parse_resume_sections,
    segment_sections,
)

RESUME = """Jane Q. Candidate
Dublin, Ireland | +353 000000000 | jane@example.com

Summary
Platform engineer with eight years across payments and observability.
Led the migration of our skills matrix onto a self-service portal.

Technical Skills
Languages: Python, Go, TypeScript
Cloud: AWS, GCP

Experience
Acme Payments, Dublin, Jan 2022 - Present, Staff Engineer
• Cut p99 checkout latency by 43%.
• Ran the on-call rotation for 14 services.
Globex, Remote, Jun 2019 - Dec 2021, Senior Engineer
• Built the ledger reconciliation pipeline.
Initech, Austin, Aug 2017 - May 2019, Engineer
• Maintained the billing cron fleet.

Featured Projects
Kestrel — Jan 2024 — Rust, WASM
• A browser-native log parser.
Tessera — Mar 2023 — Python
• Tiling window manager for terminals.
Orrery — 2022 — Go
• Distributed clock skew visualiser.

Education
The University of Texas at Arlington, Arlington, TX, Aug 2013 - May 2017
BS Software Engineering

Certifications
AWS Solutions Architect — Professional
CKA: Certified Kubernetes Administrator
"""


# --- segmentation is deterministic and lossless -----------------------------

def test_every_section_is_found():
    segs = segment_sections(RESUME)
    assert set(segs) == {"_header", "summary", "skills", "experience",
                         "projects", "education", "certifications"}


def test_no_content_line_is_lost():
    """Every non-heading line survives into some section.

    Stronger than a retention ratio, and size-independent: headings are a fixed
    cost, so a 1KB fixture retains ~91% where the real 8,490-char PDF retains
    99.0%. A threshold would be measuring the fixture, not the parser.
    """
    segs = segment_sections(RESUME)
    landed = "\n".join(segs.values())
    lost = [
        line for line in RESUME.splitlines()
        if line.strip() and _heading_of(line) is None and line not in landed
    ]
    assert not lost, f"dropped: {lost}"


def test_only_headings_are_consumed():
    """The bytes that do not survive are exactly the heading lines."""
    segs = segment_sections(RESUME)
    retained = sum(len(v) for v in segs.values())
    headings = sum(len(ln) for ln in RESUME.splitlines() if _heading_of(ln))
    # Plus newlines and blank-line whitespace, which strip() removes per section.
    assert len(RESUME) - retained < headings + 40


def test_content_lands_in_the_right_section():
    segs = segment_sections(RESUME)
    assert "Arlington" in segs["education"]      # the role that went missing
    assert "Acme" in segs["experience"]
    assert "Kestrel" in segs["projects"]
    assert "Initech" in segs["experience"]       # the THIRD employer, not just two


def test_a_sentence_mentioning_a_heading_word_does_not_split_the_document():
    """'Led the migration of our skills matrix onto...' contains 'skills'.

    A naive contains-check would cut the resume in half here and file
    everything after it under Skills.
    """
    segs = segment_sections(RESUME)
    assert "skills matrix" in segs["summary"]
    assert "Languages: Python" in segs["skills"]


def test_nothing_is_truncated_at_six_thousand_characters():
    """The specific regression. A long corpus must segment in full."""
    long_resume = RESUME.replace(
        "• Maintained the billing cron fleet.",
        "\n".join(f"• Achievement number {i} with enough words to take up room." for i in range(400)),
    )
    assert len(long_resume) > 20_000
    segs = segment_sections(long_resume)
    assert "Arlington" in segs["education"], "Education fell off the end again"
    assert "Kestrel" in segs["projects"]
    assert len(segs["experience"]) > 15_000


def test_unknown_headings_do_not_drop_their_content():
    text = RESUME + "\nVolunteering\nMentored twelve apprentices.\n"
    joined = "\n".join(segment_sections(text).values())
    assert "Mentored twelve apprentices" in joined


def test_empty_input():
    assert segment_sections("") == {}
    assert parse_resume_sections("") == {"raw_text": ""}


# --- the no-AI path is now useful -------------------------------------------

def test_without_an_ai_client_it_still_returns_structure():
    """It used to return {"raw_text": ...} and nothing else."""
    out = parse_resume_sections(RESUME)
    assert out["summary"].startswith("Platform engineer")
    assert isinstance(out["skills"], list) and len(out["skills"]) == 2
    assert out["certifications"] == [
        "AWS Solutions Architect — Professional",
        "CKA: Certified Kubernetes Administrator",
    ]
    assert "experience" in out["_sections_found"]


# --- the AI path ------------------------------------------------------------

def _client(responses):
    """An ai_client whose .complete returns the next canned response."""
    c = MagicMock()
    c.complete.side_effect = list(responses)
    return c


def test_projects_are_requested_and_returned():
    """The schema gap. 'projects' did not appear anywhere in the old parser."""
    from resume_parser import _SECTION_PROMPTS
    assert "projects" in _SECTION_PROMPTS
    assert '"projects"' in _SECTION_PROMPTS["projects"]


def test_each_structured_section_gets_its_own_call():
    """One call per section, so one failure cannot cost the others — and so a
    17k-char corpus never hits Groq's 8k tokens/minute ceiling in one request.
    """
    c = _client(['{}'] * 6)
    parse_resume_sections(RESUME, ai_client=c)
    assert c.complete.call_count == 6, [k["prompt"][:40] for k in
                                        (call.kwargs for call in c.complete.call_args_list)]


def test_one_failing_section_does_not_lose_the_others():
    c = MagicMock()
    c.complete.side_effect = [
        '{"name": "Jane Q. Candidate"}',
        RuntimeError("provider 503"),                  # experience dies
        '{"projects": [{"name": "Kestrel"}]}',
        '{"education": [{"school": "UTA"}]}',
        '{"skills": ["Languages: Python"]}',
        '{"certifications": ["CKA"]}',
    ]
    out = parse_resume_sections(RESUME, ai_client=c)
    assert out["name"] == "Jane Q. Candidate"
    assert out["projects"][0]["name"] == "Kestrel"
    assert out["education"][0]["school"] == "UTA"
    assert not out.get("experience")


def test_valid_json_of_the_wrong_type_does_not_escape():
    """The old code returned whatever json.loads gave it.

    A model answering with a bare list or string meant parse_resume_sections
    returned a list or a str against its own -> Dict annotation, and app.py
    crashed on sections.get(...) with AttributeError.
    """
    out = parse_resume_sections(RESUME, ai_client=_client(['[1,2,3]', '"hello"', 'null',
                                                           '{}', '{}', '{}']))
    assert isinstance(out, dict)


def test_malformed_json_does_not_raise():
    out = parse_resume_sections(RESUME, ai_client=_client(['not json'] * 6))
    assert isinstance(out, dict)
    assert out["raw_text"] == RESUME


def test_the_skills_prompt_demands_a_list():
    """adapt_parsed_resume_sections ITERATES the skills field. Asked for prose,
    a 33-character string became 33 entries and rendered as one-character
    bullets."""
    from resume_parser import _SECTION_PROMPTS
    prompt = _SECTION_PROMPTS["skills"]
    assert "LIST OF STRINGS" in prompt
    assert "Never a single string" in prompt


def test_an_oversized_section_is_capped_and_says_so(caplog):
    """A bound is fine; a SILENT bound is what caused this whole defect."""
    import logging
    huge = RESUME.replace("• Maintained the billing cron fleet.", "x" * (MAX_SECTION_CHARS + 500))
    c = _client(['{}'] * 6)
    with caplog.at_level(logging.WARNING):
        parse_resume_sections(huge, ai_client=c)
    assert any("is NOT parsed" in r.getMessage() for r in caplog.records), \
        [r.getMessage() for r in caplog.records]


def test_the_cap_is_far_above_a_realistic_section():
    """12k per SECTION, against a 6k cap that applied to the whole document."""
    assert MAX_SECTION_CHARS >= 12_000
    for body in segment_sections(RESUME).values():
        assert len(body) < MAX_SECTION_CHARS
