r"""PDF -> LaTeX conversion never worked: two parsers, two shapes.

The upload path calls resume_parser.parse_resume_sections (PDF text -> AI ->
structured fields) and hands the result to
parse_sections.rebuild_tex_from_sections, which was written for
parse_sections.parse_resume_sections (LaTeX -> structured fields). The shapes
differ, so the render raised

    'str' object has no attribute 'get'

...which _sections_to_tex catches and logs, returning "". The upload then falls
back to storing extracted PLAIN TEXT in tex_content. That text is not
tailorable, so pick_latest_tailorable skips it and the pipeline silently keeps
using an older resume.

Measured 2026-09-29: the master uploaded on 2026-09-28 was stored as 16,953
chars of plain text, and every resume generated since was built from a
2026-04-05 template instead.

    resume_parser                        rebuild_tex_from_sections
    -----------------------------------  ------------------------------------
    name/title_line/email/phone (flat)   header: {name, title, contact}
    skills: ["Cloud: AWS, GCP", ...]     skills: [{category, items}]
    experience: [{company, role,         experience: [{company, title,
                  dates, bullets: str}]                 dates, bullets: list}]
    certifications: ["AWS SA Pro"]       certifications: [{name, date}]
    education: [{school, degree, dates}] education: [{school, degree, dates}]  (already matches)
"""
import sys

sys.path.insert(0, ".")
from shared.resume_format import adapt_parsed_resume_sections  # noqa: E402

PARSED = {
    "name": "Utkarsh Singh",
    "title_line": "Software Engineer (DevOps/SRE, MLOps)",
    "email": "u@example.com",
    "phone": "+353 000000",
    "location": "Dublin, Ireland",
    "summary": "Senior SRE with 3+ years.",
    "skills": [
        "Languages & Frameworks: Python, Go",
        "Cloud: AWS, GCP",
        "no colon here",
    ],
    "experience": [
        {"company": "Yuno Energy", "role": "Energy Consultant",
         "dates": "Jun 2026 - Present",
         "bullets": "• Built the field app\n• Ran the cluster"},
    ],
    "education": [{"school": "TCD", "degree": "MSc", "dates": "2024"}],
    "certifications": ["AWS Certified Solutions Architect, Professional"],
    "years_of_experience": 3,
}


def test_header_is_nested_with_a_contact_line():
    out = adapt_parsed_resume_sections(PARSED)
    assert out["header"]["name"] == "Utkarsh Singh"
    assert out["header"]["title"] == "Software Engineer (DevOps/SRE, MLOps)"
    for bit in ("u@example.com", "+353 000000", "Dublin, Ireland"):
        assert bit in out["header"]["contact"]


def test_skill_strings_split_on_the_first_colon():
    out = adapt_parsed_resume_sections(PARSED)
    assert {"category": "Languages & Frameworks", "items": "Python, Go"} in out["skills"]
    assert {"category": "Cloud", "items": "AWS, GCP"} in out["skills"]


def test_a_skill_line_with_no_colon_keeps_its_text():
    """Dropping it would silently delete a skills row from the resume."""
    out = adapt_parsed_resume_sections(PARSED)
    assert {"category": "", "items": "no colon here"} in out["skills"]


def test_experience_role_becomes_title():
    out = adapt_parsed_resume_sections(PARSED)
    assert out["experience"][0]["title"] == "Energy Consultant"
    assert out["experience"][0]["company"] == "Yuno Energy"


def test_bullet_string_becomes_a_list_without_the_bullet_glyph():
    out = adapt_parsed_resume_sections(PARSED)
    assert out["experience"][0]["bullets"] == ["Built the field app", "Ran the cluster"]


def test_certification_strings_become_named_objects():
    out = adapt_parsed_resume_sections(PARSED)
    assert out["certifications"] == [
        {"name": "AWS Certified Solutions Architect, Professional", "date": ""}
    ]


def test_education_passes_through_because_it_already_matches():
    out = adapt_parsed_resume_sections(PARSED)
    assert out["education"] == [{"school": "TCD", "degree": "MSc", "dates": "2024"}]


def test_summary_is_carried_over():
    assert adapt_parsed_resume_sections(PARSED)["summary"] == "Senior SRE with 3+ years."


def test_an_already_correct_shape_is_returned_unchanged():
    """The adapter must be safe to call on parse_sections output too."""
    native = {
        "header": {"name": "A", "title": "B", "contact": "c"},
        "summary": "s",
        "skills": [{"category": "Cloud", "items": "AWS"}],
        "experience": [{"company": "X", "title": "Y", "dates": "Z", "bullets": ["b"]}],
        "projects": [], "education": [], "certifications": [],
    }
    assert adapt_parsed_resume_sections(native) == native


def test_missing_and_empty_fields_do_not_raise():
    out = adapt_parsed_resume_sections({"name": "Solo"})
    assert out["header"]["name"] == "Solo"
    assert out["skills"] == [] and out["experience"] == []
