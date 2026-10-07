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


# ---------------------------------------------------------------------------
# Forced page breaks (strip_forced_breaks)
# ---------------------------------------------------------------------------
# Found by auditing 272 live resumes on 2026-10-07: 14 of the 15 that overran
# the two-page budget carried a model-emitted `\clearpage`, and the fit loop
# could never recover any of them because it was pulling CONTENT levers against
# a LAYOUT defect. One document, after every lever had been pulled, compiled to
# per-page text lengths of [4010, 87, 3654] -- page 2 holding 87 characters,
# blank -- from a single `\clearpage` between Experience and Featured Projects.
# Removing that macro: [4010, 3741]. Two pages.

class TestStripForcedBreaks:
    def test_a_clearpage_between_sections_is_removed(self):
        from shared.fit_to_pages import strip_forced_breaks
        tex = "\\end{itemize}\n\n\\clearpage\n\n\\section*{Featured Projects}"
        out, n = strip_forced_breaks(tex)
        assert n == 1
        assert "clearpage" not in out
        assert "\\section*{Featured Projects}" in out, "the section itself must survive"

    @pytest.mark.parametrize("macro", ["clearpage", "cleardoublepage", "newpage", "pagebreak"])
    def test_every_forced_break_macro_is_covered(self, macro):
        """All four, because matching three of them fixes most documents and
        leaves a failure mode that looks identical to the one just fixed."""
        from shared.fit_to_pages import strip_forced_breaks
        out, n = strip_forced_breaks(f"before\n\\{macro}\nafter")
        assert (n, out) == (1, "before\nafter")

    def test_indentation_does_not_hide_a_break(self):
        from shared.fit_to_pages import strip_forced_breaks
        assert strip_forced_breaks("a\n    \\newpage   \nb")[1] == 1

    def test_a_macro_that_merely_starts_with_newpage_is_left_alone(self):
        """`\\b` in the pattern, asserted. Without it this silently truncates
        any user macro whose name begins with one of the four."""
        from shared.fit_to_pages import strip_forced_breaks
        out, n = strip_forced_breaks("a\n\\newpagestyle{x}\nb")
        assert (n, out) == (0, "a\n\\newpagestyle{x}\nb")

    def test_an_inline_break_is_not_touched(self):
        """Only whole lines. A regex loose enough to catch a mid-paragraph
        `\\pagebreak` is loose enough to corrupt a line it did not understand,
        and the templates do not produce one."""
        from shared.fit_to_pages import strip_forced_breaks
        assert strip_forced_breaks("text \\pagebreak more text")[1] == 0

    def test_a_document_without_one_is_returned_unchanged(self):
        from shared.fit_to_pages import strip_forced_breaks
        tex = "\\section*{Summary}\nSome prose.\n"
        assert strip_forced_breaks(tex) == (tex, 0)

    def test_normalise_separators_strips_them_and_says_so(self):
        """It has to run in the pre-compile normalisation, not in
        `reduction_plan`: the reduction levers are pulled only when a document
        overruns, and a forced break has to go whether or not it does -- 73 of
        the 272 carried one while still fitting."""
        from shared.fit_to_pages import normalise_separators
        out, actions = normalise_separators("a\n\\clearpage\nb")
        assert "clearpage" not in out
        assert any("forced page break" in a for a in actions), actions

    def test_removing_a_break_is_reported_as_a_correction_not_a_reduction(self):
        """The distinction the two functions encode. Everything in
        `normalise_separators` is free; everything in `reduction_plan` costs the
        candidate something. A break is pure cost with no benefit, so it is a
        correction -- and nothing in `reduction_plan` should duplicate it.
        """
        from shared.fit_to_pages import reduction_plan
        tex = "a\n\\clearpage\nb"
        for name, step in reduction_plan(tex, None):
            out, _ = step(tex)
            assert "clearpage" in out, (
                f"reduction step {name!r} removes forced breaks; that belongs in "
                "normalise_separators, which runs unconditionally")


# ---------------------------------------------------------------------------
# Free levers before content (tighten_list_spacing / tighten_section_spacing)
# ---------------------------------------------------------------------------
# Until 2026-10-08 `reduction_plan` spent bullets down to the floor before
# trying any typographic lever. "Cheapest first" has to mean cheapest TO THE
# CANDIDATE, and whitespace is the only thing a résumé loses for free.
#
# Measured on the three documents that still overran after every lever had been
# pulled — page 3 holding 39, 147 and 383 characters, a spillover rather than a
# page. Closing up list spacing alone brought two of them to exactly two pages:
#
#   7d7533eaa322   [4151, 3831,  39]  ->  [4337, 3684]
#   ad871c2c1134   [4048, 3868, 147]  ->  [4173, 3890]
#   9be139f8581f   [3913, 4124, 383]  ->  [4196, 4077, 147]

TEMPLATE_SPACING = (
    "\\setlist[itemize]{leftmargin=*, itemsep=1.8pt, topsep=2.5pt, parsep=0pt, partopsep=0pt}\n"
    "\\titlespacing*{\\section}{0pt}{0.60em}{0.35em}\n"
)


class TestFreeLevers:
    def test_list_spacing_is_closed_up(self):
        from shared.fit_to_pages import tighten_list_spacing
        out, changed = tighten_list_spacing(TEMPLATE_SPACING)
        assert changed
        assert "itemsep=0pt, topsep=1pt" in out
        assert "leftmargin=*" in out, "the rest of the setlist must survive"
        assert "parsep=0pt" in out

    def test_section_spacing_is_closed_up(self):
        from shared.fit_to_pages import tighten_section_spacing
        out, changed = tighten_section_spacing(TEMPLATE_SPACING)
        assert changed
        assert "{0pt}{0.35em}{0.20em}" in out

    def test_a_document_without_those_knobs_is_unchanged(self):
        """Both must be no-ops on a template that does not carry them, so a
        step that cannot apply costs no compile — `fit` skips measuring when a
        step changes nothing."""
        from shared.fit_to_pages import tighten_list_spacing, tighten_section_spacing
        plain = "\\section*{Summary}\nProse.\n"
        assert tighten_list_spacing(plain) == (plain, False)
        assert tighten_section_spacing(plain) == (plain, False)

    def test_applying_twice_changes_nothing_further(self):
        """Idempotent, because `fit` may re-enter the plan and a lever that
        keeps 'changing' the document would spend a compile every round."""
        from shared.fit_to_pages import tighten_list_spacing
        once, _ = tighten_list_spacing(TEMPLATE_SPACING)
        twice, changed = tighten_list_spacing(once)
        assert (twice, changed) == (once, False)


class TestLeverOrder:
    """The ordering IS the fix, so it is asserted rather than left to reading."""

    def test_every_free_lever_comes_before_every_content_lever(self):
        from shared.fit_to_pages import reduction_plan
        names = [n for n, _ in reduction_plan(TEMPLATE_SPACING, None)]
        free = {"margins", "list-spacing", "section-spacing"}
        last_free = max(i for i, n in enumerate(names) if n in free)
        first_content = min(i for i, n in enumerate(names)
                            if n.startswith("bullets") or n.startswith("skills"))
        assert last_free < first_content, (
            f"a content lever is pulled before a free one: {names}. Whitespace "
            "costs the candidate nothing and a dropped bullet costs them a "
            "line of their own history")

    def test_the_plan_still_ends_with_the_content_levers_it_had(self):
        """Adding free levers must not have displaced any existing one."""
        from shared.fit_to_pages import reduction_plan
        names = [n for n, _ in reduction_plan(TEMPLATE_SPACING, None)]
        assert "margins" in names
        assert any(n.startswith("bullets<=") for n in names)
        assert any(n.startswith("skills<=") for n in names)
