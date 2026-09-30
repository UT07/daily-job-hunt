"""Measure whether the words survived a conversion, without asking a model.

Every existing gate on the upload path is structural and all of them passed
while 91% of a resume went missing:

    is_latex_document           markers present
    sections_have_content       any ONE of five sections non-empty
    check_section_completeness  matches section header NAMES, and the renderer
                                emits all six unconditionally

Measured 2026-09-29: an 8,490-char PDF became a compilable two-page document
holding 772 characters of content, with all three green.

The thresholds in resume_verify are calibrated, not chosen, and were re-derived
on 2026-09-30 over 27 real (PDF text, .tex) pairs after two extractor defects
turned out to bias the instrument. Both floors held. The measurement, and why
each floor is where it is, is in resume_verify.py beside the constants — this
file tests the extractor's behaviour, which is what the floors are measured
THROUGH.

The two defects are what most of the tests below are about, and both made the
instrument report LESS loss than had occurred:

    the anchor set was not closed under punctuation splitting, so
    "TypeScript/JavaScript" and "TypeScript and JavaScript" scored 0.0%;

    a capital initial was required, so a lowercase source token produced no
    anchor and an output that kept none of it scored 1.000.

Several tests guard against OVER-correcting — half a compound is still loss, and
generic nouns inside real names ("New York", "Data Engineering") are not prose.
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


def test_environment_and_package_names_are_not_content():
    """A command's ARGUMENT can be structure too. Dropping the [A-Z] rule made
    'itemize', 'geometry' and 'tabular' ordinary words, so a re-render with a
    different preamble would have scored them as content lost."""
    a = extract_anchors(
        r"\documentclass[11pt]{article}\usepackage[margin=1in]{geometry}"
        r"\begin{tabular}{ll}Clover\end{tabular}", is_latex=True)
    assert "clover" in a
    assert not {"article", "geometry", "tabular", "margin"} & a


def test_escaping_a_literal_is_not_losing_it():
    r"""& $ _ # must be escaped in LaTeX, and _TEX_BRACES deletes them because
    LaTeX also uses them for alignment, math mode and subscripts. So correctly
    writing AT\&T read as losing AT&T entirely."""
    src = "Worked at AT&T on the order_service, saving $2.4M"
    out = r"Worked at AT\&T on the order\_service, saving \$2.4M"
    recall, missing = anchor_recall(src, out)
    assert recall == 1.0, f"missing {missing}"


def test_bare_small_integers_are_ignored():
    """Bullet numbering and page numbers are not claims."""
    a = extract_anchors("1. First 2. Second 3. Third")
    assert not {"1", "2", "3"} & a


# --- the anchor set must be closed under punctuation splitting ---------------
#
# _PROPER greedily absorbs . - / & +, so a compound and its spelled-out form
# produced DISJOINT anchor sets and the two scored as total loss. Measured on
# main @ ab7ae3f:
#
#     extract_anchors("Built with TypeScript/JavaScript")     {'typescript/javascript'}
#     extract_anchors("Built with TypeScript and JavaScript") {'typescript','javascript'}
#     anchor_recall(first, second)  ->  0.000, missing ['typescript/javascript']

def test_a_compound_and_its_spelled_out_form_are_not_total_loss():
    """Identical content written two ways. On main this measured recall 0.000."""
    slashed = "Built with TypeScript/JavaScript at Acme Corp"
    spelled = "Built with TypeScript and JavaScript at Acme Corp"
    assert anchor_recall(slashed, spelled, output_is_latex=False)[0] == 1.0
    assert anchor_recall(spelled, slashed, output_is_latex=False)[0] == 1.0


def test_every_part_of_a_compound_must_survive():
    """The fix must not become an excuse: dropping HALF of a compound is loss.

    Otherwise "closed under splitting" would mean "any one part will do", and
    losing JavaScript entirely would read as a faithful conversion.
    """
    recall, missing = anchor_recall("Built with TypeScript/JavaScript",
                                    "Built with TypeScript", output_is_latex=False)
    assert recall < 1.0
    assert "typescript/javascript" in missing


def test_re_escaped_compounds_survive_a_latex_round_trip():
    """CI/CD -> CI / CD, Node.js -> Node.js, AT&T -> AT\\&T are re-renderings."""
    src = "Ran CI/CD for Node.js at AT&T using React-Native"
    out = r"Ran CI / CD for Node.js at AT\&T using React Native"
    assert anchor_recall(src, out)[0] == 1.0


# --- a lowercase source token must still be measurable ----------------------
#
# The [A-Z] initial requirement meant a lowercase source token yielded NO
# anchor, so the source asked for nothing and any output satisfied it. Measured
# on main @ ab7ae3f:
#
#     extract_anchors("we use kubernetes daily")                      set()
#     anchor_recall("we use kubernetes daily", "Kubernetes operator")  1.000
#     anchor_recall("we use kubernetes daily", "nothing at all")       1.000
#
# The second line is the defect: free credit, not a false alarm. A gate that
# scores an output which kept nothing identically to one that kept everything
# is not a status (CLAUDE.md rule 2).

def test_a_lowercase_source_token_is_an_anchor():
    assert "kubernetes" in extract_anchors("we use kubernetes daily")


def test_losing_a_lowercase_token_is_visible():
    src = "we use kubernetes daily"
    recall, missing = anchor_recall(src, "nothing here at all", output_is_latex=False)
    assert recall < 1.0, "an output that kept nothing must not score 1.000"
    assert "kubernetes" in missing


def test_case_is_not_content():
    """A renderer may change capitalisation; that is not loss in either
    direction. Before the fix the lowercase source asked for nothing and the
    uppercase source asked for something the lowercase output could not give."""
    for src, out in (("we use kubernetes daily", "Kubernetes used daily"),
                     ("We use Kubernetes daily", "kubernetes used daily")):
        assert anchor_recall(src, out, output_is_latex=False)[0] == 1.0, (src, out)


# --- prose verbs are not proper nouns ---------------------------------------

def test_capitalised_bullet_verbs_are_not_anchors():
    """A bullet opens with a capitalised verb, and _STOPWORDS omitted most of
    them, so they were extracted as proper nouns. Measured over 707 real
    resumes: 868 of 3116 residual instances (27.9%) -- 'orchestrated' in 168
    resumes, 'skilled' 62, 'collaborated' 49, 'scripting' 31, 'holds' 27,
    'optimized' 27, 'engineered' 22, 'applied' 21, 'enforced' 19,
    'configured' 16, 'secured' 13, 'instrumented' 8.

    They matter twice over: they inflate the denominator, so a real loss moves
    the number less, and they are exactly the words a faithful conversion is
    allowed to rephrase.
    """
    for verb in ("Orchestrated", "Skilled", "Collaborated", "Scripting",
                 "Holds", "Optimized", "Engineered", "Applied", "Enforced",
                 "Configured", "Secured", "Instrumented"):
        anchors = extract_anchors(f"{verb} the Kubernetes control plane")
        assert verb.lower() not in anchors, f"{verb} counted as a proper noun"
        assert "kubernetes" in anchors, "the real anchor must survive the filter"


def test_generic_nouns_inside_real_names_are_kept():
    """The first stopword list I trialled swept in generic nouns as well as
    verbs. Audited against 27 real resumes those are parts of real identities --
    'New York', 'Data Engineering Intern', 'AWS Certified Solutions Architect' --
    and dropping them would delete anchors that DO carry identity."""
    a = extract_anchors("Data Engineering Intern, New York. AWS Certified "
                        "Solutions Architect.")
    for token in ("data", "engineering", "new", "york", "certified"):
        assert token in a, f"{token} is part of a real name and must stay"


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


def test_the_message_leads_with_names_not_numbers():
    """The shown list is truncated, so its ORDER decides whether the message is
    usable. Measured against the real 2026-09-28 degradation, alphabetical order
    made the first twelve '$0.006, 10+, 10,700+, 10ms, 149, 15+, 18%, 19,,
    2019, 2021, 2023, 3+' -- a correct refusal nobody can act on.
    """
    gutted = "Jane Q. Candidate\n"
    message = describe_loss(SOURCE, gutted, limit=6, output_is_latex=False)
    shown = message.split("Missing: ", 1)[1].split(" (+")[0].split(", ")
    assert any(t[:1].isupper() for t in shown), message
    # every capitalised token must come before every uncapitalised one
    ranks = [t[:1].isupper() for t in shown]
    assert ranks == sorted(ranks, reverse=True), shown
    assert "Globex" in shown or "Acme" in shown, shown


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
