"""The base-resume fallback shipped the whole corpus, and said so in the log.

Observed in production on 2026-09-30, mid-batch, for job 0ab484ad7687:

    [tailor] validation failed for 0ab484ad7687: prompt echo: ... "Let's" ...
             — falling back to base resume
    [tailor] base-resume fallback for 0ab484ad7687 does not meet the
             composition rules: 5 experience entries, limit is 3;
             5 projects, limit is 3

Both lines are correct and nothing acted on the second one. The user's policy
says three experience entries and three projects; the document written to S3
had five and five. CLAUDE.md rule 13: the check fired, its severity was a log
line, and the mechanism built for it was never told.

Trimming a corpus needs no model, which is the whole point of doing it here.
The entries are already written and already in reverse-chronological order; the
only question is which survive, and that is set arithmetic over the user's own
rules. So it runs on whatever is about to ship and always runs.

Fixtures are the REAL corpus structure, entry names and ordering as read from
production via fetch_tailorable_resume on 2026-09-30 — including the trap that
makes naive truncation wrong. Seattle Kraken sits at position 3 and UT
Arlington IT at position 4, so "keep the first three" drops exactly the entry
the user asked to keep and keeps the one they asked to drop.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

from shared.composition_policy import (  # noqa: E402
    check_output,
    compose_from_corpus,
    count_entries,
    render_for_prompt,
)


def _entry(macro, name, dates, title, bullets):
    items = "\n".join(f"  \\item {b}" for b in bullets)
    return (f"\\{macro}{{{name}}}{{}}{{{dates}}}{{\\textbf{{\\textit{{{title}}}}}}}\n"
            f"\\begin{{itemize}}\n{items}\n\\end{{itemize}}\n")


# Production order, 2026-09-30. Kraken (3) ahead of UTA IT (4) is the trap.
CORPUS = (
    "\\documentclass{article}\n"
    "\\newcommand{\\jobentry}[4]{#1 #3 #4}\n"
    "\\newcommand{\\projectentryurl}[5]{#1}\n"
    "\\begin{document}\n"
    "\\section*{Summary}\nA summary paragraph.\n\n"
    "\\section*{Technical Skills}\n\\begin{itemize}\n  \\item AWS, Python\n\\end{itemize}\n\n"
    "\\section*{Experience}\n"
    + _entry("jobentry", "Yuno Energy", "Jun 2026 – Present", "Energy Consultant", ["a", "b"])
    + "\n"
    + _entry("jobentry", "Clover IT Services", "Jun 2022 – Jul 2024", "SRE", ["c", "d"])
    + "\n"
    + _entry("jobentry", "Seattle Kraken (NHL)", "Jun 2021 – May 2022", "Data Analyst Intern", ["e"])
    + "\n"
    + _entry("jobentry", "Office of IT, University of Texas at Arlington",
             "Jan 2020 – May 2022", "IT Specialist", ["f"])
    + "\n"
    + _entry("jobentry", "Dept. of Computer Science \\& Engineering, UT Arlington",
             "Sep 2020 – May 2022", "Teaching Assistant", ["g"])
    + "\n\\section*{Featured Projects}\n"
    + _entry("projectentryurl", "NaukriBaba – AI Job-Automation Platform (MLOps)",
             "Mar 2026 – Present", "x", ["h"])
    + "\n"
    + _entry("projectentryurl", "Purrrfect Keys – AI Piano Learning App",
             "Jan 2026 – Present", "x", ["i"])
    + "\n"
    + _entry("projectentryurl", "WhatsTheCraic – ML-Powered Event Discovery Platform",
             "Mar 2025 – Feb 2026", "x", ["j"])
    + "\n"
    + _entry("projectentryurl", "Genomic Benchmarking Platform – MSc Thesis",
             "Sep 2025 – Dec 2025", "x", ["k"])
    + "\n"
    + _entry("projectentryurl", "UTWorld – Portfolio with Full-Stack CMS",
             "2024 – Present", "x", ["l"])
    + "\n\\section*{Certifications}\n\\begin{itemize}\n"
      "  \\item AWS Certified Solutions Architect, Professional (SAP-C02)\n"
      "  \\item AWS Certified Developer, Associate (DVA-C01)\n"
      "  \\item AWS Certified Cloud Practitioner (CLF-C01)\n"
      "\\end{itemize}\n\\end{document}\n"
)

POLICY = {
    "max_experience_entries": 3,
    "max_projects": 3,
    "prefer": [
        {"include": "Office of IT, University of Texas at Arlington",
         "over": "Seattle Kraken", "why": "the UT Arlington role is the longer tenure"},
        {"exclude": "Dept. of Computer Science",
         "why": "academic teaching work, not industry experience"},
        {"include": "Purrrfect Keys", "over": "Genomic Benchmarking",
         "why": "a shipped product with real users beats an academic thesis"},
    ],
}


def _names(tex, macro):
    """Entry names in document order, read back out of the LaTeX."""
    from shared.composition_policy import _entry_blocks
    return [b["name"] for b in _entry_blocks(tex, macro)]


def test_the_corpus_starts_non_compliant():
    """Guards the fixture: if this passes trivially the rest proves nothing."""
    assert count_entries(CORPUS) == {"experience": 5, "projects": 5}
    assert check_output(CORPUS, POLICY) == ["5 experience entries, limit is 3",
                                            "5 projects, limit is 3"]


def test_trimming_yields_exactly_the_entries_the_user_asked_for():
    out, actions = compose_from_corpus(CORPUS, POLICY)
    assert check_output(out, POLICY) == []
    assert _names(out, "experience") == [
        "Yuno Energy",
        "Clover IT Services",
        "Office of IT, University of Texas at Arlington",
    ]
    assert _names(out, "projects") == [
        "NaukriBaba – AI Job-Automation Platform (MLOps)",
        "Purrrfect Keys – AI Piano Learning App",
        "WhatsTheCraic – ML-Powered Event Discovery Platform",
    ]
    assert len(actions) == 4


def test_naive_truncation_would_have_been_wrong():
    """The reason a cap alone cannot do this job.

    Kraken is third in the corpus and UT Arlington IT is fourth, so keeping
    the first three keeps the entry the user rejected and drops the one they
    asked for. The preference has to be applied BEFORE the cap.
    """
    first_three = _names(CORPUS, "experience")[:3]
    assert "Seattle Kraken (NHL)" in first_three
    assert "Office of IT, University of Texas at Arlington" not in first_three

    kept = _names(compose_from_corpus(CORPUS, POLICY)[0], "experience")
    assert "Seattle Kraken (NHL)" not in kept
    assert "Office of IT, University of Texas at Arlington" in kept


def test_chronological_order_survives_selection():
    """Selection reorders nothing: ATS reads position as recency."""
    out, _ = compose_from_corpus(CORPUS, POLICY)
    kept = _names(out, "experience")
    original = _names(CORPUS, "experience")
    assert kept == [n for n in original if n in kept]
    assert kept[0] == "Yuno Energy"


def test_removal_leaves_no_orphaned_bullet_list():
    """A dropped entry takes its whole itemize with it."""
    out, _ = compose_from_corpus(CORPUS, POLICY)
    assert out.count("\\begin{itemize}") == out.count("\\end{itemize}")
    for gone in ("Seattle Kraken", "Teaching Assistant", "MSc Thesis", "UTWorld"):
        assert gone not in out
    # and the bullets belonging to dropped entries went with them
    for orphan in ("\\item e", "\\item g", "\\item k", "\\item l"):
        assert orphan not in out


def test_sections_and_preamble_are_untouched():
    """Trimming entries must not cost a section, a macro or the certifications."""
    out, _ = compose_from_corpus(CORPUS, POLICY)
    for section in ("Summary", "Technical Skills", "Experience",
                    "Featured Projects", "Certifications"):
        assert f"\\section*{{{section}}}" in out
    assert "\\newcommand{\\jobentry}[4]" in out      # definition never matched
    assert out.count("AWS Certified") == 3
    assert out.rstrip().endswith("\\end{document}")


def test_a_macro_definition_is_never_counted_as_an_entry():
    """\\newcommand{\\jobentry}[4]{...} is a definition, not an entry."""
    only_defs = ("\\newcommand{\\jobentry}[4]{#1}\n"
                 "\\newcommand{\\projectentryurl}[5]{#1}\n")
    assert count_entries(only_defs) == {"experience": 0, "projects": 0}
    out, actions = compose_from_corpus(only_defs, POLICY)
    assert (out, actions) == (only_defs, [])


def test_a_compliant_document_is_returned_unchanged():
    """No-op means no-op: byte-identical, empty action list."""
    out, _ = compose_from_corpus(CORPUS, POLICY)
    again, actions = compose_from_corpus(out, POLICY)
    assert again == out
    assert actions == []


def test_a_preference_is_ignored_when_its_winner_is_absent():
    """Dropping Kraken because UTA IT is preferred only makes sense if UTA IT
    survived. Otherwise the resume loses an entry for a reason nobody asked
    for -- the caps are limits, not quotas."""
    policy = {"max_experience_entries": 9, "max_projects": 9,
              "prefer": [{"include": "Nonexistent Role", "over": "Seattle Kraken"}]}
    out, actions = compose_from_corpus(CORPUS, policy)
    assert "Seattle Kraken (NHL)" in out
    assert actions == []


def test_an_exclusion_applies_even_under_a_generous_cap():
    """"Never include this" is not a tie-break; it holds regardless of room."""
    policy = {"max_experience_entries": 9, "max_projects": 9,
              "prefer": [{"exclude": "Dept. of Computer Science"}]}
    out, actions = compose_from_corpus(CORPUS, policy)
    assert "Teaching Assistant" not in out
    assert len(actions) == 1
    assert count_entries(out)["experience"] == 4


def test_escaped_ampersands_in_a_name_still_match():
    """The corpus writes "Computer Science \\& Engineering"; a user types "&"."""
    policy = {"max_experience_entries": 9, "max_projects": 9,
              "prefer": [{"exclude": "Computer Science & Engineering"}]}
    out, actions = compose_from_corpus(CORPUS, policy)
    assert len(actions) == 1
    assert "Teaching Assistant" not in out


def test_structured_rules_reach_the_prompt_as_prose():
    """One definition, two consumers. A dict must never render as a dict."""
    rendered = render_for_prompt(POLICY)
    assert "{'include'" not in rendered and '{"include"' not in rendered
    assert 'Include "Office of IT, University of Texas at Arlington" IN ' \
           'PREFERENCE TO "Seattle Kraken"' in rendered
    assert 'NEVER include "Dept. of Computer Science"' in rendered
    assert "the longer tenure" in rendered          # the why is carried through


def test_a_prose_preference_still_renders_and_is_not_executed():
    """Prose that needs judgement stays the model's job."""
    policy = {"max_experience_entries": 9, "max_projects": 9,
              "prefer": ["Lead with whichever role the job most resembles."]}
    assert "Lead with whichever role" in render_for_prompt(policy)
    out, actions = compose_from_corpus(CORPUS, policy)
    assert (out, actions) == (CORPUS, [])


def test_a_latex_command_BETWEEN_needle_words_is_stripped():
    """The one placement where stripping \\textbf actually decides a match.

    Worth stating because the first version of this test did not pin anything.
    _normalise_name is applied to BOTH the corpus name and the policy needle,
    so most mutations to it are symmetric and cannot change an outcome: drop
    the case fold and "Seattle Kraken" becomes "eattle raken" on both sides,
    which still matches. Wrapping the whole name -- \\textbf{Seattle Kraken} --
    is likewise harmless, because the leftover token "textbf" lands outside the
    needle and substring matching steps over it.

    A command BETWEEN the words is different: "Seattle \\textbf{Kraken}"
    normalises to "seattle textbf kraken" unstripped, and "seattle kraken" is
    no longer a substring of it. That asymmetry is what the strip is for.
    """
    tex = CORPUS.replace("{Seattle Kraken (NHL)}", "{Seattle \\textbf{Kraken} (NHL)}")
    policy = {"max_experience_entries": 9, "max_projects": 9,
              "prefer": [{"exclude": "Seattle Kraken"}]}
    out, actions = compose_from_corpus(tex, policy)
    assert len(actions) == 1, "a command between the needle's words broke the match"
    assert "Data Analyst Intern" not in out


def test_prose_only_rules_are_announced_when_the_cap_selects_alone():
    """A silently-wrong trim looks exactly like a working one.

    With prose rules the trimmer can apply caps but not preferences, and on the
    live corpus that reverses the user's choice: Seattle Kraken is third and UT
    Arlington IT is fourth, so position keeps the rejected entry. The trim is
    still performed -- a compliant resume beats a non-compliant one -- but the
    action list has to say the selection was positional, or the log reads as a
    success.
    """
    policy = {"max_experience_entries": 3, "max_projects": 3,
              "prefer": ["Prefer UT Arlington IT over Seattle Kraken."]}
    out, actions = compose_from_corpus(CORPUS, policy)
    assert check_output(out, POLICY) == []                  # still compliant
    assert any("by position only" in a for a in actions)
    # and the warning is honest about the consequence
    assert "Seattle Kraken (NHL)" in out
    assert "Office of IT, University of Texas at Arlington" not in out


def test_no_positional_warning_once_the_rules_are_executable():
    """The same corpus, the same caps, structured rules: no warning, right answer."""
    _, actions = compose_from_corpus(CORPUS, POLICY)
    assert not any("by position only" in a for a in actions)
