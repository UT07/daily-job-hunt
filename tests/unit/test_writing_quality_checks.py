"""Two output-quality defects the user reported on 2026-10-05.

The resume read, verbatim, as a bullet under Education:

    • coursework, transferred. Algorithms & Data Structures, Operating
      Systems, Computer Networks, Information Security.

The parser is told "coursework is everything else the entry lists", so it keeps
the source resume's own heading INSIDE the value, and the heading then renders
as though it were the first module.

And the bullets read as job descriptions rather than achievements. The user
supplied the Yale Office of Career Strategy formula — ACTION VERB + task at
scale + QUANTIFIED RESULT — and the part of it that is countable is the opener:
"Responsible for", "Worked on", "Assisted in" occupy the verb slot without
being verbs. Per CLAUDE.md rule 4, the countable part gets counted.
"""
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
for p in (str(_ROOT), str(_ROOT / "lambdas" / "pipeline")):
    if p not in sys.path:
        sys.path.insert(0, p)

from guardrails.output_guards import check_weak_bullet_openers  # noqa: E402
from parse_sections import _label_coursework  # noqa: E402


class TestWeakOpeners:
    def test_the_reported_shapes_are_flagged(self):
        for weak in ("Responsible for maintaining the CI pipeline.",
                     "Worked on the migration to Aurora.",
                     "Assisted in the rollout of observability tooling.",
                     "Duties included on-call rotation."):
            assert check_weak_bullet_openers(f"\\item {weak}"), weak

    def test_an_action_verb_opener_is_not_flagged(self):
        strong = ("Cut p99 checkout latency 40% by sharding the session store.",
                  "Designed a serverless platform spanning 34 Lambda functions.",
                  "Led a 12-person team through a zero-downtime migration.")
        for s in strong:
            assert check_weak_bullet_openers(f"\\item {s}") == [], s

    def test_position_is_the_whole_point(self):
        """Mid-sentence is clumsy; OPENING that way spends the strongest word.

        This is why it is not another entry in _BANNED_PHRASES, which searches
        the whole document for a substring and would flag both identically.
        """
        assert check_weak_bullet_openers(
            "\\item Led the team and was responsible for the migration.") == []

    def test_latex_markup_around_the_opener_does_not_hide_it(self):
        assert check_weak_bullet_openers("\\item \\textbf{Worked on} Aurora.")

    def test_each_bullet_is_judged_separately(self):
        tex = ("\\item Responsible for the pipeline.\n"
               "\\item Cut build time 60%.\n"
               "\\item Assisted in the rollout.")
        assert len(check_weak_bullet_openers(tex)) == 2

    def test_a_clean_document_yields_nothing(self):
        assert check_weak_bullet_openers("") == []


class TestCourseworkLabel:
    def test_the_reported_value_renders_as_a_label(self):
        out = _label_coursework(
            "coursework, transferred. Algorithms \\& Data Structures, Operating Systems.")
        assert out.startswith("\\textbf{Coursework, transferred:}")
        assert "Algorithms \\& Data Structures" in out

    def test_the_qualifier_is_kept_not_dropped(self):
        """"transferred" says these are transferred credits. Deleting the
        heading wholesale would quietly change what the resume claims."""
        assert "transferred" in _label_coursework("coursework, transferred. Compilers")

    def test_an_already_labelled_value_is_not_double_labelled(self):
        out = _label_coursework("Relevant coursework: Distributed Systems")
        assert out == "\\textbf{Relevant coursework:} Distributed Systems"
        assert out.count("textbf") == 1

    def test_a_bare_list_gets_a_label(self):
        assert _label_coursework("Distributed Systems, Compilers") == \
            "\\textbf{Relevant coursework:} Distributed Systems, Compilers"

    def test_empty_stays_empty(self):
        """No label on nothing — the caller omits the whole itemize block."""
        assert _label_coursework("") == ""
