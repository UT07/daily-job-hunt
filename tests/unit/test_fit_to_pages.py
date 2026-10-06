"""A four-page resume against a policy of two.

compile_latex measured the PDF from the day shared/page_check landed, logged
the violation, and uploaded the document anyway. The same shape the composition
check had before #181: the instrument was right and nothing was wired to it.

Measured on the live corpus, composed to the user's 3+3 policy and compiled
with tectonic:

    full content, margins 0.60/0.70in   4 pages  [4040, 4178, 3958, 80]
    full content, margins 0.50/0.60in   3 pages  [4264, 4481, 3511]
    3 bullets/entry, 6 skills, tight    3 pages  [4075, 3996, 147]
    3 bullets/entry, 5 skills, tight    2 pages  [4176, 3830]

Two pages is therefore NOT reachable by layout and content must go -- and the
last 147 characters cost a whole skills row, so the ORDER the levers are pulled
in decides what the candidate loses. `measure` is injected because the decision
procedure is what these tests are about; the real compile is exercised once at
the bottom, because a page count is precisely the thing a mock cannot tell you.
"""
import re
import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.fit_to_pages import (  # noqa: E402
    TIGHT_MARGINS,
    cap_items,
    fit,
    normalise_separators,
    tighten_margins,
)

PREAMBLE = (
    "\\documentclass[10pt,a4paper]{article}\n"
    "\\usepackage[top=0.60in,bottom=0.60in,left=0.70in,right=0.70in]{geometry}\n"
    "\\newcommand{\\jobentry}[4]{%\n  \\textbf{#1} -- #2 \\hfill \\textit{#3}\\\\\n}\n"
    "\\begin{document}\n"
)


def _bullets(n, tag):
    return "\n".join(f"  \\item {tag} bullet {i}." for i in range(n))


def _doc(entry_bullets=8, skills=9):
    return (
        PREAMBLE
        + "\\section*{Technical Skills}\n\\begin{itemize}\n"
        + _bullets(skills, "skill") + "\n\\end{itemize}\n\n"
        + "\\section*{Experience}\n"
        + "\\jobentry{Yuno Energy}{}{Jun 2026 – Present}{Engineer}\n"
        + "\\begin{itemize}\n" + _bullets(entry_bullets, "job") + "\n\\end{itemize}\n"
        + "\\end{document}\n"
    )


def _entry_bullet_count(tex):
    block = tex.split("\\section*{Experience}")[1]
    return len(re.findall(r"\\item", block))


def _skills_count(tex):
    block = tex.split("\\section*{Technical Skills}")[1].split("\\end{itemize}")[0]
    return len(re.findall(r"\\item", block))


class TestLevers:
    def test_margins_change_nothing_about_content(self):
        out, changed = tighten_margins(_doc())
        assert changed and TIGHT_MARGINS in out
        assert _entry_bullet_count(out) == _entry_bullet_count(_doc())
        assert _skills_count(out) == _skills_count(_doc())

    def test_margins_are_idempotent(self):
        once, _ = tighten_margins(_doc())
        twice, changed = tighten_margins(once)
        assert twice == once and changed is False

    def test_capping_entries_leaves_the_skills_list_alone(self):
        out, removed = cap_items(_doc(), entry_max=3)
        assert removed == 5
        assert _entry_bullet_count(out) == 3
        assert _skills_count(out) == 9, "the skills list was trimmed by the entry cap"

    def test_capping_skills_leaves_entries_alone(self):
        out, removed = cap_items(_doc(), skills_max=5)
        assert removed == 4
        assert _skills_count(out) == 5
        assert _entry_bullet_count(out) == 8


class TestFit:
    def test_a_document_already_within_budget_is_untouched(self):
        doc = _doc()
        out, actions, fits = fit(doc, lambda _t: 2)
        assert (out, actions, fits) == (doc, [], True)

    def test_margins_are_tried_before_any_content_is_cut(self):
        seen = []

        def measure(t):
            seen.append(t)
            return 2 if TIGHT_MARGINS in t else 4

        out, actions, fits = fit(_doc(), measure)
        assert fits
        assert len(actions) == 1 and "margins" in actions[0]
        assert _entry_bullet_count(out) == 8, "content was cut before margins were tried"
        assert _skills_count(out) == 9

    def test_skills_are_cut_last(self):
        """They are ATS keyword matches, so they are the most expensive thing
        to lose and go after every bullet lever is exhausted."""
        def measure(t):
            return 2 if _entry_bullet_count(t) <= 3 else 4

        out, actions, fits = fit(_doc(), measure)
        assert fits
        assert _skills_count(out) == 9, "skills were cut while bullets remained"
        assert any("bullets" in a for a in actions)

    def test_an_unfittable_document_comes_back_untouched(self):
        """A half-trimmed resume that still overflows is strictly worse than
        the untrimmed one: the same page count, less of the candidate on it."""
        doc = _doc()
        out, actions, fits = fit(doc, lambda _t: 9)
        assert fits is False
        assert out == doc
        assert actions == []

    def test_a_step_that_changes_nothing_costs_no_compile(self):
        """Every compile is ~5s of a bounded Lambda, so a lever that cannot
        remove anything must not spend one.

        The document has TWO entry bullets, so every `bullets<=N` step for N>=3
        is a no-op, and the skills steps are the first that can change it. The
        first version of this test used an 8-bullet document where margins
        succeeded immediately -- it never reached a no-op step at all, and
        passed identically with the skip removed.
        """
        calls = {"n": 0}

        def measure(t):
            calls["n"] += 1
            return 2 if _skills_count(t) <= 7 else 4

        _out, actions, fits = fit(_doc(entry_bullets=2, skills=9), measure)
        assert fits
        # initial + margins + skills<=7. The four bullet steps remove nothing
        # from a 2-bullet entry and must be skipped silently.
        assert calls["n"] == 3, f"{calls['n']} compiles, actions={actions}"

    def test_skills_are_never_cut_below_the_floor(self):
        """Below MIN_SKILLS the section stops being a keyword surface at all,
        and an ATS scores on exactly those keywords."""
        from shared.fit_to_pages import MIN_SKILLS
        seen = []

        def measure(t):
            seen.append(_skills_count(t))
            return 9                      # nothing ever fits

        fit(_doc(entry_bullets=2, skills=9), measure)
        assert min(seen) >= MIN_SKILLS, f"cut to {min(seen)}, floor is {MIN_SKILLS}"

    def test_entry_bullets_are_never_cut_below_the_floor(self):
        from shared.fit_to_pages import MIN_BULLETS
        seen = []

        def measure(t):
            seen.append(_entry_bullet_count(t))
            return 9

        fit(_doc(entry_bullets=8, skills=5), measure)
        assert min(seen) >= MIN_BULLETS, f"cut to {min(seen)}, floor is {MIN_BULLETS}"

    def test_the_budget_comes_from_the_policy(self):
        out, actions, fits = fit(_doc(), lambda _t: 3, policy={"pages": 3})
        assert fits and actions == [] and out == _doc()


class TestSeparators:
    def test_the_empty_location_dash_is_suppressed(self):
        out, actions = normalise_separators(_doc())
        assert "\\ifx\\relax#2\\relax" in out
        assert any("location separator" in a for a in actions)

    def test_date_dashes_become_hyphens(self):
        out, _ = normalise_separators(_doc())
        assert "Jun 2026 - Present" in out
        assert "–" not in out.split("\\begin{document}")[1]

    def test_an_en_dash_inside_a_name_is_left_alone(self):
        """Replacing every dash would rewrite the candidate's own titles."""
        doc = _doc().replace("\\end{document}",
                             "\\projectentryurl{NaukriBaba – AI Platform}{2026}{u}{u}{t}\n\\end{document}")
        out, _ = normalise_separators(doc)
        assert "NaukriBaba – AI Platform" in out


@pytest.mark.skipif(
    not any((Path(p) / "tectonic").exists()
            for p in ("/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/opt/bin")),
    reason="tectonic not installed",
)
def test_the_fitted_document_still_compiles(tmp_path):
    """Rule 5: the levers cut LaTeX, so one real compile decides whether they
    cut it into something that still builds. Every unit test above would pass
    identically against a trimmer that produced invalid TeX."""
    import subprocess
    out, _ = cap_items(_doc(), entry_max=2, skills_max=3)
    out, _ = tighten_margins(out)
    out, _ = normalise_separators(out)
    tex = tmp_path / "f.tex"
    tex.write_text(out)
    r = subprocess.run(["tectonic", "-X", "compile", str(tex), "--outdir", str(tmp_path)],
                       capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-800:]
    assert (tmp_path / "f.pdf").exists()
