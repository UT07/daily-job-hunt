"""Unit tests for lambdas/pipeline/parse_sections.py.

Tests cover:
- _strip_latex helper (special chars, commands, backslash-backslash)
- parse_resume_sections: header, summary, skills, experience, projects,
  education, certifications
- rebuild_tex_from_sections: roundtrip compilability signal (structure check)
- parse_cover_letter_sections: section extraction
- analyze_sections_vs_jd: keyword matching + coverage scores
"""

import sys
from pathlib import Path

# Ensure project root is on the import path for utils/
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lambdas.pipeline.parse_sections import (
    _strip_latex,
    analyze_sections_vs_jd,
    parse_cover_letter_sections,
    parse_resume_sections,
    rebuild_tex_from_sections,
)


# ---------------------------------------------------------------------------
# Sample fixture — minimal but structurally complete LaTeX resume
# ---------------------------------------------------------------------------

_SAMPLE_TEX = r"""\documentclass[10pt,a4paper]{article}
\usepackage[utf8]{inputenc}
\newcommand{\jobentry}[4]{%
  \textbf{#1} -- #2 \hfill \textit{#3}\\[-0.15em]
  \textit{#4}\\[-0.25em]
}
\newcommand{\projectentry}[3]{%
  \textbf{#1} \hfill \textit{#2}\\[-0.15em]
  \textit{#3}\\[-0.25em]
}
\newcommand{\projectentryurl}[5]{%
  \textbf{#1} \hfill \textit{#2}\\[-0.15em]
  {\small\href{#3}{\texttt{#4}}} \textbar\ \textit{#5}\\[-0.25em]
}
\begin{document}
\begin{center}
{\Large \textbf{Jane Doe}}\\[0.04em]
{\normalsize Senior Software Engineer (Python, AWS)}\\[0.08em]
Dublin, Ireland \textbar\ +353 123456789 \textbar\ \href{mailto:jane@example.com}{jane@example.com}\\[0.08em]
\href{https://github.com/janedoe}{github.com/janedoe} \textbar\
\href{https://www.linkedin.com/in/janedoe/}{linkedin.com/in/janedoe}
\end{center}
\vspace{0.06em}

\section*{Summary}
Experienced engineer with 5 years building cloud-native applications.

\section*{Technical Skills}
\begin{itemize}
  \item \textbf{Programming \& Scripting:} Python, TypeScript, Bash
  \item \textbf{Cloud \& Infra:} AWS (Lambda, S3, ECS), Docker, Terraform
\end{itemize}

\section*{Experience}
\jobentry{Acme Corp}{New York, NY}{Jan 2020 -- Present}{\textbf{\textit{Software Engineer}}}
\begin{itemize}
  \item Built scalable APIs serving 1M+ requests per day.
  \item Reduced latency by 40\% through caching.
\end{itemize}

\jobentry{Beta Inc}{San Francisco, CA}{Jun 2018 -- Dec 2019}{\textbf{\textit{Junior Developer}}}
\begin{itemize}
  \item Maintained CI/CD pipelines with Jenkins.
\end{itemize}

\section*{Featured Projects}
\projectentryurl{My Project}{Jan 2023 -- Present}{https://github.com/janedoe/proj}{github.com/janedoe/proj}{React, FastAPI, PostgreSQL}
\begin{itemize}
  \item Built a full-stack web app with 500 daily active users.
\end{itemize}

\projectentry{Side Project}{Mar 2022}{Python, AWS Lambda}
\begin{itemize}
  \item Automated data ingestion from 3 external APIs.
\end{itemize}

\section*{Education}
\textbf{Trinity College Dublin}, Dublin, Ireland \hfill \textit{Sep 2016 -- May 2020}\\[-0.15em]
\textbf{\textit{BSc Computer Science}}\\[-0.25em]

\section*{Certifications}
\begin{itemize}
  \item \href{https://example.com}{\textbf{\textit{AWS Certified Developer Associate}}} \hfill \textit{Issued Jan 2023}
\end{itemize}
\end{document}
"""

_SAMPLE_COVER_LETTER_TEX = r"""\documentclass[10pt,a4paper]{article}
\usepackage[hidelinks]{hyperref}
\pagestyle{empty}
\begin{document}

\begin{center}
{\Large \textbf{Jane Doe}}\\[0.3em]
Dublin, Ireland \textbar\ +353 123456789 \textbar\ \href{mailto:jane@example.com}{jane@example.com}
\end{center}

\vspace{0.5em}
\hrule
\vspace{1em}

\today

\vspace{0.8em}

Acme Corp Hiring Team\\
Re: Senior Engineer

\vspace{0.8em}

Acme builds developer tools that genuinely matter. I want to join as a Senior Engineer and bring three years of distributed-systems experience to your platform team.

Over the past three years I built a real-time data pipeline processing five million events per day at sub-100ms latency. I designed the schema and cache invalidation strategy that allowed the team to ship five major features without downtime.

I also led the migration of a monolithic service to twelve microservices, cutting infrastructure costs by thirty percent. The project is now open source and used by two other teams internally.

Best regards,\\
Jane Doe

\end{document}
"""


# ---------------------------------------------------------------------------
# _strip_latex tests
# ---------------------------------------------------------------------------

def test_strip_latex_removes_textbf():
    assert _strip_latex(r"\textbf{Hello}") == "Hello"


def test_strip_latex_removes_textit():
    assert _strip_latex(r"\textit{World}") == "World"


def test_strip_latex_resolves_href():
    result = _strip_latex(r"\href{https://example.com}{Click here}")
    assert result == "Click here"
    assert "https://example.com" not in result


def test_strip_latex_handles_textbar():
    result = _strip_latex(r"Dublin \textbar\ London")
    assert "|" in result
    assert "textbar" not in result


def test_strip_latex_handles_double_backslash_with_spacing():
    # \\[0.08em] is a LaTeX line break + vertical space
    result = _strip_latex(r"line one\\[0.08em]line two")
    assert "[0.08em]" not in result
    assert "line one" in result
    assert "line two" in result


def test_strip_latex_handles_ampersand_escape():
    result = _strip_latex(r"Programming \& Scripting")
    assert "&" in result
    assert "\\" not in result


def test_strip_latex_handles_percent_escape():
    result = _strip_latex(r"reduced by 40\%")
    assert "%" in result


def test_strip_latex_removes_lone_braces():
    result = _strip_latex("{plain text}")
    assert "{" not in result
    assert "}" not in result
    assert "plain text" in result


def test_strip_latex_collapses_whitespace():
    result = _strip_latex("  too    many   spaces  ")
    assert "  " not in result.strip()


# ---------------------------------------------------------------------------
# parse_resume_sections tests
# ---------------------------------------------------------------------------

def test_parse_header_name():
    sections = parse_resume_sections(_SAMPLE_TEX)
    assert sections["header"]["name"] == "Jane Doe"


def test_parse_header_title():
    sections = parse_resume_sections(_SAMPLE_TEX)
    assert "Senior Software Engineer" in sections["header"]["title"]
    assert "Python" in sections["header"]["title"]


def test_parse_header_contact_contains_email():
    sections = parse_resume_sections(_SAMPLE_TEX)
    assert "jane@example.com" in sections["header"]["contact"]


def test_parse_header_contact_contains_location():
    sections = parse_resume_sections(_SAMPLE_TEX)
    assert "Dublin" in sections["header"]["contact"]


def test_parse_summary_plain_text():
    sections = parse_resume_sections(_SAMPLE_TEX)
    summary = sections["summary"]
    assert isinstance(summary, str)
    assert "engineer" in summary.lower() or "Engineer" in summary
    assert "\\" not in summary  # no raw LaTeX commands


def test_parse_skills_count():
    sections = parse_resume_sections(_SAMPLE_TEX)
    assert len(sections["skills"]) == 2


def test_parse_skills_categories():
    sections = parse_resume_sections(_SAMPLE_TEX)
    categories = [s["category"] for s in sections["skills"]]
    assert any("Programming" in c for c in categories)
    assert any("Cloud" in c for c in categories)


def test_parse_skills_no_latex_in_items():
    sections = parse_resume_sections(_SAMPLE_TEX)
    for s in sections["skills"]:
        assert "\\" not in s["items"], f"LaTeX found in skill items: {s['items']}"


def test_parse_experience_count():
    sections = parse_resume_sections(_SAMPLE_TEX)
    assert len(sections["experience"]) == 2


def test_parse_experience_company_names():
    sections = parse_resume_sections(_SAMPLE_TEX)
    companies = [e["company"] for e in sections["experience"]]
    assert "Acme Corp" in companies
    assert "Beta Inc" in companies


def test_parse_experience_titles():
    sections = parse_resume_sections(_SAMPLE_TEX)
    exp = {e["company"]: e for e in sections["experience"]}
    assert "Software Engineer" in exp["Acme Corp"]["title"]
    assert "Junior Developer" in exp["Beta Inc"]["title"]


def test_parse_experience_dates():
    sections = parse_resume_sections(_SAMPLE_TEX)
    acme = next(e for e in sections["experience"] if e["company"] == "Acme Corp")
    assert "Jan 2020" in acme["dates"]


def test_parse_experience_bullets():
    sections = parse_resume_sections(_SAMPLE_TEX)
    acme = next(e for e in sections["experience"] if e["company"] == "Acme Corp")
    assert len(acme["bullets"]) == 2
    assert any("API" in b or "api" in b.lower() for b in acme["bullets"])


def test_parse_experience_bullets_no_latex():
    sections = parse_resume_sections(_SAMPLE_TEX)
    for entry in sections["experience"]:
        for bullet in entry["bullets"]:
            assert "\\" not in bullet, f"LaTeX in bullet: {bullet}"


def test_parse_projects_count():
    sections = parse_resume_sections(_SAMPLE_TEX)
    assert len(sections["projects"]) == 2


def test_parse_projects_url_project_name():
    sections = parse_resume_sections(_SAMPLE_TEX)
    names = [p["name"] for p in sections["projects"]]
    assert any("My Project" in n for n in names)


def test_parse_projects_plain_project_name():
    sections = parse_resume_sections(_SAMPLE_TEX)
    names = [p["name"] for p in sections["projects"]]
    assert any("Side Project" in n for n in names)


def test_parse_projects_tech():
    sections = parse_resume_sections(_SAMPLE_TEX)
    proj = next(p for p in sections["projects"] if "My Project" in p["name"])
    assert "FastAPI" in proj["tech"] or "React" in proj["tech"]


def test_parse_projects_bullets():
    sections = parse_resume_sections(_SAMPLE_TEX)
    proj = next(p for p in sections["projects"] if "My Project" in p["name"])
    assert len(proj["bullets"]) == 1
    assert "full-stack" in proj["bullets"][0].lower() or "Full-stack" in proj["bullets"][0]


def test_parse_education_count():
    sections = parse_resume_sections(_SAMPLE_TEX)
    assert len(sections["education"]) == 1


def test_parse_education_school():
    sections = parse_resume_sections(_SAMPLE_TEX)
    assert "Trinity College" in sections["education"][0]["school"]


def test_parse_education_degree():
    sections = parse_resume_sections(_SAMPLE_TEX)
    assert "Computer Science" in sections["education"][0]["degree"]


def test_parse_education_dates():
    sections = parse_resume_sections(_SAMPLE_TEX)
    assert "2016" in sections["education"][0]["dates"]


def test_parse_certifications_count():
    sections = parse_resume_sections(_SAMPLE_TEX)
    assert len(sections["certifications"]) == 1


def test_parse_certifications_name():
    sections = parse_resume_sections(_SAMPLE_TEX)
    assert "AWS Certified Developer" in sections["certifications"][0]["name"]


def test_parse_certifications_date():
    sections = parse_resume_sections(_SAMPLE_TEX)
    assert "Jan 2023" in sections["certifications"][0]["date"]


def test_parse_returns_all_keys():
    sections = parse_resume_sections(_SAMPLE_TEX)
    for key in ("header", "summary", "skills", "experience", "projects", "education", "certifications"):
        assert key in sections, f"Missing key: {key}"


def test_parse_empty_string_returns_empty_structure():
    sections = parse_resume_sections("")
    assert sections["header"]["name"] == ""
    assert sections["summary"] == ""
    assert sections["skills"] == []


# ---------------------------------------------------------------------------
# rebuild_tex_from_sections tests
# ---------------------------------------------------------------------------

def test_rebuild_produces_compilable_structure():
    sections = parse_resume_sections(_SAMPLE_TEX)
    rebuilt = rebuild_tex_from_sections(sections, _SAMPLE_TEX)
    assert r"\begin{document}" in rebuilt
    assert r"\end{document}" in rebuilt


def test_rebuild_preserves_preamble():
    sections = parse_resume_sections(_SAMPLE_TEX)
    rebuilt = rebuild_tex_from_sections(sections, _SAMPLE_TEX)
    assert r"\documentclass" in rebuilt
    assert r"\newcommand{\jobentry}" in rebuilt


def test_rebuild_contains_all_sections():
    sections = parse_resume_sections(_SAMPLE_TEX)
    rebuilt = rebuild_tex_from_sections(sections, _SAMPLE_TEX)
    for heading in (r"\section*{Summary}", r"\section*{Technical Skills}",
                    r"\section*{Experience}", r"\section*{Featured Projects}",
                    r"\section*{Education}", r"\section*{Certifications}"):
        assert heading in rebuilt, f"Missing section: {heading}"


def test_rebuild_preserves_name():
    sections = parse_resume_sections(_SAMPLE_TEX)
    rebuilt = rebuild_tex_from_sections(sections, _SAMPLE_TEX)
    assert "Jane Doe" in rebuilt


def test_rebuild_preserves_summary_text():
    sections = parse_resume_sections(_SAMPLE_TEX)
    rebuilt = rebuild_tex_from_sections(sections, _SAMPLE_TEX)
    assert "cloud-native" in rebuilt or "engineer" in rebuilt.lower()


def test_rebuild_preserves_experience_companies():
    sections = parse_resume_sections(_SAMPLE_TEX)
    rebuilt = rebuild_tex_from_sections(sections, _SAMPLE_TEX)
    assert r"\jobentry{Acme Corp}" in rebuilt
    assert r"\jobentry{Beta Inc}" in rebuilt


def test_rebuild_preserves_bullet_text():
    sections = parse_resume_sections(_SAMPLE_TEX)
    rebuilt = rebuild_tex_from_sections(sections, _SAMPLE_TEX)
    assert "requests per day" in rebuilt


def test_rebuild_no_base_tex_still_produces_document():
    sections = parse_resume_sections(_SAMPLE_TEX)
    rebuilt = rebuild_tex_from_sections(sections, "")  # no preamble
    assert r"\begin{document}" in rebuilt
    assert r"\end{document}" in rebuilt


def test_rebuild_edited_summary_appears_in_output():
    sections = parse_resume_sections(_SAMPLE_TEX)
    sections["summary"] = "Edited summary text for testing."
    rebuilt = rebuild_tex_from_sections(sections, _SAMPLE_TEX)
    assert "Edited summary text for testing." in rebuilt


def test_rebuild_edited_bullet_appears_in_output():
    sections = parse_resume_sections(_SAMPLE_TEX)
    sections["experience"][0]["bullets"][0] = "Custom edited bullet point."
    rebuilt = rebuild_tex_from_sections(sections, _SAMPLE_TEX)
    assert "Custom edited bullet point." in rebuilt


def test_rebuild_brace_balance():
    """Rebuilt .tex must have balanced { }."""
    sections = parse_resume_sections(_SAMPLE_TEX)
    rebuilt = rebuild_tex_from_sections(sections, _SAMPLE_TEX)
    depth = 0
    i = 0
    while i < len(rebuilt):
        if rebuilt[i] == "\\" and i + 1 < len(rebuilt) and rebuilt[i + 1] in "{}":
            i += 2
            continue
        if rebuilt[i] == "{":
            depth += 1
        elif rebuilt[i] == "}":
            depth -= 1
        i += 1
    assert depth == 0, f"Unbalanced braces in rebuilt .tex (depth={depth})"


# ---------------------------------------------------------------------------
# parse_cover_letter_sections tests
# ---------------------------------------------------------------------------

def test_parse_cover_letter_returns_all_keys():
    sections = parse_cover_letter_sections(_SAMPLE_COVER_LETTER_TEX)
    for key in ("greeting", "opening", "body1", "body2", "closing"):
        assert key in sections, f"Missing cover letter key: {key}"


def test_parse_cover_letter_greeting_contains_company():
    sections = parse_cover_letter_sections(_SAMPLE_COVER_LETTER_TEX)
    assert "Acme" in sections["greeting"] or "Re:" in sections["greeting"]


def test_parse_cover_letter_opening_is_substantial():
    sections = parse_cover_letter_sections(_SAMPLE_COVER_LETTER_TEX)
    assert len(sections["opening"].split()) >= 10


def test_parse_cover_letter_no_latex_commands():
    sections = parse_cover_letter_sections(_SAMPLE_COVER_LETTER_TEX)
    for key, val in sections.items():
        assert "\\" not in val, f"LaTeX command in cover letter section [{key}]: {val}"


def test_parse_cover_letter_empty_tex():
    sections = parse_cover_letter_sections("")
    for key in ("greeting", "opening", "body1", "body2", "closing"):
        assert sections[key] == ""


# ---------------------------------------------------------------------------
# analyze_sections_vs_jd tests
# ---------------------------------------------------------------------------

_SAMPLE_JD = """
We are looking for a Software Engineer with strong Python and AWS experience.
You will build microservices on Kubernetes and work with PostgreSQL databases.
CI/CD experience with GitHub Actions is required. Docker knowledge is a plus.
"""


def test_analyze_returns_jd_keywords():
    sections = parse_resume_sections(_SAMPLE_TEX)
    result = analyze_sections_vs_jd(sections, _SAMPLE_JD)
    assert "jd_keywords" in result
    assert len(result["jd_keywords"]) > 0


def test_analyze_returns_sections_key():
    sections = parse_resume_sections(_SAMPLE_TEX)
    result = analyze_sections_vs_jd(sections, _SAMPLE_JD)
    assert "sections" in result


def test_analyze_returns_per_section_analysis():
    sections = parse_resume_sections(_SAMPLE_TEX)
    result = analyze_sections_vs_jd(sections, _SAMPLE_JD)
    for sec in ("summary", "skills", "experience", "projects", "education", "certifications"):
        assert sec in result["sections"], f"Missing section in analysis: {sec}"


def test_analyze_coverage_score_range():
    sections = parse_resume_sections(_SAMPLE_TEX)
    result = analyze_sections_vs_jd(sections, _SAMPLE_JD)
    for sec, data in result["sections"].items():
        score = data["coverage_score"]
        assert 0 <= score <= 100, f"Coverage score out of range [{sec}]: {score}"


def test_analyze_keywords_matched_are_subset_of_jd_keywords():
    sections = parse_resume_sections(_SAMPLE_TEX)
    result = analyze_sections_vs_jd(sections, _SAMPLE_JD)
    jd_kws_lower = {k.lower() for k in result["jd_keywords"]}
    for sec, data in result["sections"].items():
        for kw in data["keywords_matched"]:
            assert kw.lower() in jd_kws_lower, f"Matched keyword not in JD: {kw}"


def test_analyze_matched_plus_missing_equals_total():
    sections = parse_resume_sections(_SAMPLE_TEX)
    result = analyze_sections_vs_jd(sections, _SAMPLE_JD)
    total = len(result["jd_keywords"])
    for sec, data in result["sections"].items():
        assert len(data["keywords_matched"]) + len(data["keywords_missing"]) == total


def test_analyze_overall_coverage_is_present():
    sections = parse_resume_sections(_SAMPLE_TEX)
    result = analyze_sections_vs_jd(sections, _SAMPLE_JD)
    assert "overall_coverage" in result
    assert 0 <= result["overall_coverage"] <= 100


def test_analyze_skills_matches_python():
    """Skills section explicitly lists Python — should be matched."""
    sections = parse_resume_sections(_SAMPLE_TEX)
    result = analyze_sections_vs_jd(sections, _SAMPLE_JD)
    skills_analysis = result["sections"]["skills"]
    matched_lower = [k.lower() for k in skills_analysis["keywords_matched"]]
    assert "python" in matched_lower


def test_analyze_empty_jd_returns_empty():
    sections = parse_resume_sections(_SAMPLE_TEX)
    result = analyze_sections_vs_jd(sections, "")
    assert result == {}


def test_analyze_empty_sections_returns_zero_coverage():
    result = analyze_sections_vs_jd({}, _SAMPLE_JD)
    if result:  # may return empty dict if jd_keywords is empty
        for sec_data in result.get("sections", {}).values():
            assert sec_data["coverage_score"] == 0


# ---------------------------------------------------------------------------
# Real .tex file integration test
# ---------------------------------------------------------------------------

def test_parse_real_resume_round_trip():
    """Parse the actual sre_devops.tex and verify structure without crashing."""
    real_tex_path = Path(__file__).resolve().parents[2] / "resumes" / "sre_devops.tex"
    if not real_tex_path.exists():
        return  # Skip if file not present in test environment
    tex = real_tex_path.read_text()
    sections = parse_resume_sections(tex)

    assert sections["header"]["name"] == "Utkarsh Singh"
    assert "Site Reliability" in sections["header"]["title"]
    assert "254utkarsh@gmail.com" in sections["header"]["contact"]
    assert len(sections["skills"]) >= 6
    assert len(sections["experience"]) == 2
    assert sections["experience"][0]["company"] == "Clover IT Services"
    assert len(sections["experience"][0]["bullets"]) >= 7
    assert len(sections["projects"]) == 4
    assert any("Purrrfect" in p["name"] for p in sections["projects"])
    assert len(sections["education"]) == 3
    assert any("National College" in e["school"] for e in sections["education"])
    assert len(sections["certifications"]) == 3
    assert any("SAP-C02" in c["name"] for c in sections["certifications"])


def test_rebuild_real_resume_has_required_sections():
    """Rebuild the real resume and verify all 6 sections are present."""
    real_tex_path = Path(__file__).resolve().parents[2] / "resumes" / "sre_devops.tex"
    if not real_tex_path.exists():
        return
    tex = real_tex_path.read_text()
    sections = parse_resume_sections(tex)
    rebuilt = rebuild_tex_from_sections(sections, tex)

    for heading in (r"\section*{Summary}", r"\section*{Technical Skills}",
                    r"\section*{Experience}", r"\section*{Featured Projects}",
                    r"\section*{Education}", r"\section*{Certifications}"):
        assert heading in rebuilt

    assert "Utkarsh Singh" in rebuilt
    assert r"\jobentry{Clover IT Services}" in rebuilt
