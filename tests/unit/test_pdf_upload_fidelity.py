r"""What a PDF upload must carry across, measured against a real master.

Users upload PDFs. They do not have LaTeX, and telling them to produce it is not
a product. So the PDF -> sections -> LaTeX path has to preserve the document, and
`conversion_is_faithful` is the gate that decides whether the result may replace
the user's base resume.

Measured 2026-09-30 on a real 16,953-char master PDF, BEFORE this change:

    doc anchor recall          0.967   (floor 0.90)  -> passes
    education section recall   0.74    (floor 0.88)  -> FAILS
    conversion_is_faithful     False                 -> UPLOAD REFUSED

So the upload was rejected and the user's old resume stayed, with no visible
reason. The document was otherwise intact: 5/5 employers with bullets, 5/5
projects, 3/3 certifications, all six section headers. Four classes of content
were dropped, and each was dropped by a PROMPT that never asked for it, not by
the segmentation (which captured 99.6% of the document):

    1. github / linkedin / website   -> in the `_header` segment, not in the schema
    2. per-project repository URLs    -> in the `projects` segment, not in the schema
       (`\projectentryurl` has always been READABLE by _parse_projects; nothing
       ever wrote it, so every project link was lost on upload)
    3. Languages, "Right to work: Stamp 1G" -> in the `certifications` segment
       under an "Additional" heading, not in the schema
    4. education coursework -- the named modules -- 26% of that section's
       anchors, and the single reason the gate refused

AFTER: doc recall 0.998, every section above its floor, conversion_is_faithful
True, 2 anchors missing (both fragments of a location string).

These tests work on the renderer and the adapter, which are deterministic. The
prompts themselves are asserted only for the field NAMES they request, because
what a model returns is not a unit-testable property.
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "lambdas" / "pipeline"))

from parse_sections import rebuild_tex_from_sections  # noqa: E402
from shared.resume_format import adapt_parsed_resume_sections  # noqa: E402

_BASE = (ROOT / "resumes" / "sre_devops.tex").read_text()


def _render(parsed: dict) -> str:
    return rebuild_tex_from_sections(adapt_parsed_resume_sections(parsed), _BASE)


def _min_parsed(**over):
    base = {
        "name": "A Candidate", "title_line": "Engineer",
        "email": "a@b.com", "phone": "+1", "location": "Dublin",
        "summary": "Summary.", "skills": ["Cloud: AWS"],
        "experience": [{"company": "Acme", "role": "SRE", "dates": "2024", "bullets": ["Did a thing"]}],
        "projects": [], "education": [], "certifications": [],
    }
    base.update(over)
    return base


# --- 1. contact links -------------------------------------------------------


def test_github_linkedin_and_website_reach_the_header():
    tex = _render(_min_parsed(github="github.com/UT07",
                              linkedin="linkedin.com/in/someone",
                              website="example.dev"))
    for needle in ("github.com/UT07", "linkedin.com/in/someone", "example.dev"):
        assert needle in tex, needle


def test_absent_links_do_not_leave_empty_separators():
    """A blank field must not render " \\textbar\\  \\textbar\\ "."""
    tex = _render(_min_parsed())
    header = tex.split(r"\begin{center}", 1)[1].split(r"\end{center}", 1)[0]
    assert r"\textbar\  \textbar" not in header, header


# --- 2. project URLs --------------------------------------------------------


def test_a_project_with_a_url_uses_the_five_arg_macro():
    tex = _render(_min_parsed(projects=[{
        "name": "Thing", "dates": "2025", "tech": "Python",
        "url": "https://github.com/UT07/thing", "bullets": ["Built it"],
    }]))
    body = tex.split(r"\begin{document}", 1)[1]
    assert r"\projectentryurl{" in body
    assert "https://github.com/UT07/thing" in body, "href must keep the scheme"
    assert "{github.com/UT07/thing}" in body, "displayed text drops the scheme"


def test_a_project_without_a_url_uses_the_three_arg_macro():
    tex = _render(_min_parsed(projects=[{
        "name": "Thing", "dates": "2025", "tech": "Python", "bullets": ["Built it"],
    }]))
    body = tex.split(r"\begin{document}", 1)[1]
    assert r"\projectentryurl{" not in body
    assert r"\projectentry{" in body


def test_every_project_keeps_its_own_url():
    """Five projects, five different links — no cross-contamination."""
    projects = [
        {"name": f"P{i}", "dates": "2025", "tech": "T",
         "url": f"https://github.com/UT07/p{i}", "bullets": ["b"]}
        for i in range(5)
    ]
    body = _render(_min_parsed(projects=projects)).split(r"\begin{document}", 1)[1]
    for i in range(5):
        assert f"https://github.com/UT07/p{i}" in body, i


# --- 3. the Additional block ------------------------------------------------


def test_languages_and_work_authorisation_survive():
    tex = _render(_min_parsed(additional=[
        "Languages: English (fluent), Hindi (native), Spanish (basic).",
        "Right to work: Stamp 1G, full-time eligible.",
    ]))
    assert "Hindi (native)" in tex
    assert "Stamp 1G" in tex, "work authorisation is load-bearing for an IE job search"


def test_the_additional_block_does_not_add_a_seventh_section():
    """The tailoring prompt requires exactly six \\section* headers and
    check_required_sections counts them. Preserving content must not break a
    structural guard."""
    tex = _render(_min_parsed(additional=["Languages: English."]))
    body = tex.split(r"\begin{document}", 1)[1]
    assert len(re.findall(re.escape("\\section*"), body)) == 6, \
        re.findall(re.escape("\\section*") + r"\{([^}]*)\}", body)


def test_no_additional_lines_emits_nothing():
    body = _render(_min_parsed()).split(r"\begin{document}", 1)[1]
    assert "Additional" not in body


# --- 4. education coursework: the reason the gate refused -------------------

_COURSEWORK = ("Cloud Architectures, Cloud DevOpsSec, Scalable Cloud Programming, "
               "Cloud Machine Learning, Data Governance/Compliance/Ethics, "
               "Research in Computing.")


def test_education_coursework_is_rendered():
    tex = _render(_min_parsed(education=[{
        "school": "National College of Ireland", "degree": "MSc Cloud Computing",
        "dates": "Sep 2024 - Jan 2026", "coursework": _COURSEWORK,
    }]))
    for module in ("Cloud Architectures", "Cloud DevOpsSec", "Research in Computing"):
        assert module in tex, module


def test_dropping_coursework_is_what_fails_the_fidelity_gate():
    """The regression, end to end through the real gate.

    Same education entry rendered with and without `coursework`, measured by
    `conversion_is_faithful` against source text that contains the modules. With
    it, the section clears its floor; without it, the conversion is refused —
    which is exactly what happened to a real master upload.
    """
    from shared.resume_verify import conversion_is_faithful

    entry = {"school": "National College of Ireland", "degree": "MSc Cloud Computing",
             "dates": "Sep 2024 - Jan 2026", "coursework": _COURSEWORK}
    source = (
        "Summary\nSummary.\n"
        "Technical Skills\nCloud: AWS\n"
        "Experience\nAcme SRE 2024\nDid a thing\n"
        "Education\nNational College of Ireland Sep 2024 - Jan 2026\n"
        f"MSc Cloud Computing\nCoursework: {_COURSEWORK}\n"
    )
    with_cw, _ = conversion_is_faithful(
        source, _render(_min_parsed(education=[entry])), output_is_latex=True)
    without = dict(entry); without.pop("coursework")
    no_cw, why = conversion_is_faithful(
        source, _render(_min_parsed(education=[without])), output_is_latex=True)

    assert with_cw is True, "coursework preserved must be faithful"
    assert no_cw is False, "dropping the modules must be refused, not quietly accepted"
    # The refusal must NAME the lost modules, not merely report a percentage --
    # that string is what reaches the uploader. Which floor trips (document or
    # section) depends on how much of the document the fixture is, so it is
    # deliberately not asserted: on the real 16,953-char master it was the
    # education section at 74%, and on this small fixture the same loss is a
    # larger share of the whole. Both are refusals for the same reason.
    lowered = str(why).lower()
    assert "devopssec" in lowered and "scalable" in lowered, why


# --- 5. the prompts ask for these fields -----------------------------------


def test_the_parser_asks_for_every_field_the_renderer_can_use():
    """A renderer that can emit a field the prompt never requests is dead code,
    and a prompt asking for one the renderer ignores is a dropped field. Both
    were true here."""
    src = (ROOT / "resume_parser.py").read_text()
    prompts = src.split("_SECTION_PROMPTS", 1)[1].split("_VERBATIM", 1)[0]
    # Matched as a JSON SHAPE declaration, not as a bare token. The first
    # version of this test searched for '"coursework"' anywhere in the block and
    # passed after the field was renamed out of the schema, because the prompt's
    # own prose explains what "coursework" means. A guard that its own
    # documentation satisfies is not a guard.
    for field in ("github", "linkedin", "website", "url", "additional", "coursework"):
        declared = re.search(rf'"{field}":\s*(""|\[)', prompts)
        assert declared, (
            f'"{field}" is rendered but the prompt never declares it in a JSON '
            "shape, so no model is asked to return it"
        )
