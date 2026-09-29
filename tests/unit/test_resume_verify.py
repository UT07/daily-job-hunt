"""Measure whether the words survived a conversion, without asking a model.

Every existing gate on the upload path is structural and all of them passed
while 91% of a resume went missing:

    is_latex_document           markers present
    sections_have_content       any ONE of five sections non-empty
    check_section_completeness  matches section header NAMES, and the renderer
                                emits all six unconditionally

Measured 2026-09-29: an 8,490-char PDF became a compilable two-page document
holding 772 characters of content, with all three green.

The thresholds in resume_verify are calibrated, not chosen. Against a real
resume PDF and the exact .tex it was compiled from — a perfectly faithful pair:

    whole document                          97.8%
    worst single section (projects)         95.8%

and against realistic damage to the same source:

    one of two employers deleted            94.1% doc / 80.8% experience
    education deleted (Arlington)           84.4% doc
    projects section deleted                70.0% doc
    the measured 772-char production case   41.1% doc

DOC_RECALL_FLOOR = 0.90 sits below every faithful reading and above every
section-scale loss. SECTION_FLOOR = 0.88 sits between the worst faithful
section (95.8%) and the damaged one (80.8%), because deleting one employer
barely moves the whole-document number — which is the loss a single global
threshold cannot see.
"""
import sys

sys.path.insert(0, ".")
from shared.resume_verify import (  # noqa: E402
    DOC_RECALL_FLOOR,
    SECTION_FLOOR,
    anchor_recall,
    conversion_is_faithful,
    describe_loss,
    extract_anchors,
)

SOURCE = """Jane Q. Candidate
Dublin, Ireland | jane@example.com

Summary
Platform engineer. Cut p99 latency by 43% across 14 services.

Technical Skills
Languages: Python, Go
Cloud: AWS, GCP, Kubernetes

Experience
Acme Payments, Dublin, Jan 2022 - Present, Staff Engineer
• Reduced checkout latency 43% for 2.1M monthly users.
Globex, Remote, Jun 2019 - Dec 2021, Senior Engineer
• Built the ledger reconciliation pipeline on Kafka.

Education
The University of Texas at Arlington, Aug 2013 - May 2017
BS Software Engineering
"""


# --- anchors ----------------------------------------------------------------

def test_proper_nouns_years_and_quantities_are_anchors():
    a = extract_anchors(SOURCE)
    for token in ("arlington", "kubernetes", "globex", "kafka", "2019", "43%"):
        assert token in a, f"{token} missing from {sorted(a)[:30]}"


def test_stopwords_and_month_names_are_not_anchors():
    """Sentence-initial words and dates would inflate recall without carrying
    any identity, so a lost employer would move the number less."""
    a = extract_anchors(SOURCE)
    for token in ("the", "built", "summary", "experience", "jan", "present"):
        assert token not in a


def test_dotted_and_slashed_names_stay_whole():
    a = extract_anchors("We used Node.js with CI/CD at AT&T and React-Native.")
    assert {"node.js", "ci/cd", "at&t", "react-native"} <= a


def test_latex_commands_are_not_mistaken_for_content():
    """Otherwise \\textbf and \\begin{itemize} count as anchors on one side and
    absent on the other, and every comparison is noise."""
    a = extract_anchors(r"\textbf{Kubernetes} \begin{itemize}\item Kafka\end{itemize}",
                        is_latex=True)
    assert "kubernetes" in a and "kafka" in a
    assert not {"textbf", "itemize", "begin", "item"} & a


def test_bare_small_integers_are_ignored():
    """Bullet numbering and page numbers are not claims."""
    a = extract_anchors("1. First 2. Second 3. Third")
    assert not {"1", "2", "3"} & a


# --- recall -----------------------------------------------------------------

def test_identical_text_is_total_recall():
    recall, missing = anchor_recall(SOURCE, SOURCE, output_is_latex=False)
    assert recall == 1.0 and missing == []


def test_empty_source_does_not_invent_a_failure():
    assert anchor_recall("", "anything") == (1.0, [])


def test_missing_content_is_named_not_just_counted():
    """'Arlington, Globex' tells someone what to check. '0.62' does not."""
    without = SOURCE.replace("The University of Texas at Arlington", "")
    _, missing = anchor_recall(SOURCE, without, output_is_latex=False)
    assert "arlington" in missing
    assert "Arlington" in describe_loss(SOURCE, without, output_is_latex=False)


def test_describe_loss_is_quiet_when_nothing_is_lost():
    assert "preserved" in describe_loss(SOURCE, SOURCE, output_is_latex=False)


# --- the gate ---------------------------------------------------------------

def test_a_faithful_conversion_passes():
    ok, why = conversion_is_faithful(SOURCE, SOURCE, output_is_latex=False)
    assert ok, why


def test_a_gutted_conversion_is_blocked():
    stub = "Jane Q. Candidate\nPlatform engineer.\n"
    ok, why = conversion_is_faithful(SOURCE, stub, output_is_latex=False)
    assert not ok
    assert "survived" in why


def test_a_dropped_employer_is_blocked_by_the_section_floor():
    """The case a whole-document threshold cannot see.

    On the real resume this moved the document number only 97.8% -> 94.1%
    while taking the experience section to 80.8%.
    """
    without_globex = SOURCE.replace(
        "Globex, Remote, Jun 2019 - Dec 2021, Senior Engineer\n"
        "• Built the ledger reconciliation pipeline on Kafka.\n", "")
    ok, why = conversion_is_faithful(SOURCE, without_globex, output_is_latex=False)
    assert not ok, why
    # Either floor may be the one that fires: on this small fixture the
    # employer is a large enough share to trip the document floor, while on the
    # real resume it only trips the section floor. What matters is that it is
    # blocked and the message names what went missing.
    assert "Globex" in why and "Kafka" in why


def test_an_empty_source_cannot_block_anything():
    assert conversion_is_faithful("", "")[0]
    assert conversion_is_faithful("   \n ", "anything")[0]


def test_the_floors_are_ordered_and_plausible():
    """A section floor above the document floor would make the document check
    dead code; both above ~0.98 would reject faithful conversions, since the
    measured faithful pair scores 97.8%."""
    assert SECTION_FLOOR < DOC_RECALL_FLOOR
    assert 0.8 < SECTION_FLOOR < 0.95
    assert 0.85 < DOC_RECALL_FLOOR < 0.98


def test_the_gate_is_wired_into_the_upload():
    """A verifier nothing calls protects nothing."""
    import inspect

    import app
    src = inspect.getsource(app.upload_resume)
    assert "conversion_is_faithful" in src
    assert "422" in src, "a lossy conversion must be refused, not stored"
    assert "is_latex_document" in src, (
        "the refusal must be conditional on there being a working resume to "
        "protect — a first-time user with no resume must not be left with none"
    )
