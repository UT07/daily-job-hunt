r"""The fabrication guard could not see the first entry of any `\item`.

`_plain` unwrapped one level of LaTeX macro and then DELETED the remaining
delimiters, so

    \item \textbf{Primary:} Java, Python

became the single text `item Primary: Java, Python`, and `_claims` split that
on `[,&/()\n]+` and compared each field for EQUALITY against the blocklist.
The first field was `item Primary: Java` -- not equal to "java" -- so a
fabricated technology in the leading position of any `\item` was invisible.

Not a hypothetical shape. The real 2026-09-28 `user_resumes.tex_content` row
writes its entire Technical Skills section as seven `\item \textbf{Category:}
...` lines, and both committed archetype resumes (`resumes/fullstack.tex`,
`resumes/sre_devops.tex`) do the same, so in production the leading technology
of every category sat outside the guard's view. `tests/unit/
realistic_resume_body.py` on branch fix/output-quality-echo-and-empty works
around it with a deliberate newline before the last `\item` so its fabrication
tests keep testing something; that workaround can be removed once this lands.

The fix makes the two sides of the comparison use ONE matcher (`_mentions`),
which is what CLAUDE.md rule 14 asks for: the claim side and the support side
must count the same things. They did not -- the base resume was searched with
an alphanumeric-boundary regex while the tailored text was cut into fields and
matched for equality -- and the weaker side was the one deciding what counted
as a claim.

Severity is "block" (see check_output), so these tests assert CONTROL FLOW as
well as detection: CLAUDE.md rule 13 is that a test asserting a detector
returns a violation passes identically whether the violation stops anything.
The violation has to route the council to `repair`.

Measured before shipping, per CLAUDE.md rule 16, with
`scripts/measure_fabrication_claims.py`. On the real tailored documents
reachable from this repository (4, recovered from git history under output/,
plus the 2 committed archetype resumes as the union baseline): the fix newly
exposes 33 leading `\item` entries, 0 of which are blocklisted, so 0 new
flags and 0 false positives -- with the harness's own self-check confirming
the two rules DO diverge on that position (a planted Kotlin in the leading
entry: frozen rule 0/4, live rule 4/4), so the zero is a real zero and not a
dead instrument. That denominator is 4, not the 740 the gate wants; see the
PR description.
"""
from unittest.mock import patch

import pytest

from agents import graph as graph_mod
from agents import nodes
from guardrails import output_guards as og

# Abridged from the real 2026-09-28 corpus row: seven categories, each an
# `\item \textbf{Category:}` line whose leading entry is a real technology.
# Deliberately NO separator before the leading entries -- that is the shape
# the guard could not read, and inserting one would make every test below
# pass against a document production never produces.
REAL_SKILLS = (
    r"\begin{itemize}"
    r"\item \textbf{Languages \& Frameworks:} Python (FastAPI, SQLAlchemy, boto3), "
    r"TypeScript/JavaScript (React, Node.js/Express), SQL (PostgreSQL), Bash"
    r"\item \textbf{Containers \& Orchestration:} Docker, Kubernetes (EKS, k3s), Helm, ECR"
    r"\item \textbf{Cloud \& Serverless:} AWS (Lambda, Step Functions, S3, IAM), Supabase"
    r"\item \textbf{IaC \& Automation:} Terraform, AWS SAM, CloudFormation, Ansible"
    r"\item \textbf{CI/CD:} GitHub Actions, Jenkins, CodePipeline"
    r"\item \textbf{Testing \& QA:} pytest, Jest, Vitest, Playwright"
    r"\item \textbf{Observability:} Prometheus, Grafana, CloudWatch, X-Ray"
    r"\end{itemize}"
)

# The leading entry of each `\item` above, in order -- the seven positions the
# frozen rule could not see.
LEADING_ENTRIES = [
    "Python", "Docker", "AWS", "Terraform", "GitHub Actions", "pytest", "Prometheus",
]

BASE_UNION = (
    r"\section*{Technical Skills}" + REAL_SKILLS + r"\section*{Experience}"
)


def resume(skills: str) -> str:
    """A six-section tailored body: every structural check must pass clean, so
    a failing result can only be about fabrication."""
    return (
        r"\section*{Summary} Backend engineer."
        r"\section*{Technical Skills}" + skills +
        r"\section*{Experience} \textbf{Engineer} built services."
        r"\section*{Featured Projects} A project."
        r"\section*{Education} A degree."
        r"\section*{Certifications} A cert."
    )


# ---------------------------------------------------------------------------
# The reported case.
# ---------------------------------------------------------------------------


def test_the_leading_entry_of_an_item_is_visible():
    """Verbatim from the bug report: this returned [] and should flag Java."""
    tex = (
        r"\section*{Technical Skills}\begin{itemize}"
        r"\item \textbf{Primary:} Java, Python"
        r"\end{itemize}\section*{Experience}"
    )
    assert any("Java" in v for v in og.check_fabrication("Python, AWS", tex))


@pytest.mark.parametrize("position", range(len(LEADING_ENTRIES)))
def test_every_item_has_its_leading_entry_checked(position):
    r"""One `\item` fixed by accident is not the same as every `\item` read.

    The frozen rule's blind spot was per-`\item`, so a fix that only handles
    the first one (or only the ones preceded by a newline) would pass a
    single-case test. Each of the seven real categories gets its leading
    technology replaced by an unsupported one in turn.
    """
    entry = LEADING_ENTRIES[position]
    skills = REAL_SKILLS.replace(f"}} {entry}", "} Kotlin", 1) if position == 0 else (
        REAL_SKILLS.replace(f":}} {entry}", ":} Kotlin", 1)
    )
    assert skills != REAL_SKILLS, f"fixture no longer contains '{entry}'"
    violations = og.check_fabrication(BASE_UNION, resume(skills))
    assert any("Kotlin" in v for v in violations), (position, entry, violations)


def test_an_unmodified_real_skills_section_is_clean():
    """The other side of the parametrised test: the real shape, unmodified and
    compared against itself, must flag nothing. If it does, the widening
    invented a claim rather than exposing one."""
    assert og.check_fabrication(BASE_UNION, resume(REAL_SKILLS)) == []


def test_a_line_break_cannot_smuggle_a_claim_past_the_guard():
    r"""`_plain` used to DELETE delimiters, which glued words together:
    `AWS\\Java` collapsed to `AWSJava`, and "java" preceded by "S" fails the
    boundary test. Every `_plain` replacement is now a space, so the function
    can only ever add word boundaries."""
    skills = REAL_SKILLS.replace("Bash", r"Bash\\Java", 1)
    assert any("Java" in v for v in og.check_fabrication(BASE_UNION, resume(skills)))


# ---------------------------------------------------------------------------
# Control flow. Rule 13: a detector that fires and stops nothing is disarmed.
# ---------------------------------------------------------------------------


def test_a_leading_entry_fabrication_routes_the_council_to_repair():
    """The assertion the "warn" incident proved was missing.

    `check_fabrication` correctly detected a Rust claim in CI run 36651253369
    and the resume shipped anyway, because the violation carried severity
    "warn" and `GuardResult.passed` is `not any(severity == "block")`. So
    detection is not the property that matters here -- routing is. This drives
    the real value through the real chain: check_output -> to_dict() ->
    quality_gate.
    """
    tex = resume(REAL_SKILLS.replace(":} Python", ":} Kotlin", 1))
    result = og.check_output(tex, "tailor", base_skills_text=BASE_UNION)

    assert og.check_brace_balance(tex) is True
    assert not og.check_required_sections(tex), "structural checks must be clean"
    assert {v.rule for v in result.violations if v.severity == "block"} == {"fabrication"}
    assert result.passed is False
    assert nodes.quality_gate({"guard_report": result.to_dict()}) == "repair"


def test_the_repair_prompt_names_the_claim_it_must_fix():
    """`repair_node` folds violation text verbatim into the retry prompt --
    that is the whole reason "block" is worth two extra rounds. Repeating the
    rule at a model that already ignored it is not actionable (rule 4); naming
    the token is."""
    tex = resume(REAL_SKILLS.replace(":} Docker", ":} Kotlin", 1))
    report = og.check_output(tex, "tailor", base_skills_text=BASE_UNION).to_dict()
    repaired = nodes.repair_node({"prompt": "tailor this", "guard_report": report})
    assert "Kotlin" in repaired["prompt"]
    assert repaired["repair_attempts"] == 1


def test_the_compiled_graph_repairs_a_leading_entry_fabrication():
    r"""`quality_gate` in isolation is not the whole path.

    The routing test above calls check_output and quality_gate directly. This
    one drives the real compiled council graph, so it also covers
    guard_output_node recomputing the report from the winner's content and the
    graph's own edges carrying THIS violation -- the wiring that, before it
    existed, left `guard_report` unset and made quality_gate finalize every
    run regardless of what the guard found.

    The only defect in the generated content is a blocklisted technology in the
    leading entry of an `\item`. Before the fix that content was
    indistinguishable from clean output to this graph: 0 repair rounds.
    """
    tex = resume(REAL_SKILLS.replace(":} Terraform", ":} Rust", 1))
    candidate = {"content": tex, "provider": "groq/a", "model": "m1"}
    provider = {"name": "groq/a", "model": "openai/gpt-oss-120b"}
    calls = {"n": 0}

    def _always_same(*args, **kwargs):
        calls["n"] += 1
        return candidate

    with patch.object(nodes, "select_generators", return_value=[provider]), \
         patch.object(nodes, "call_one", side_effect=_always_same):
        final = graph_mod.build_council_graph().invoke(
            {
                "task": "tailor",
                "prompt": "tailor this resume",
                "system": "s",
                "task_description": "desc",
                "n_generators": 1,
                "temperature": 0.3,
                "candidates": [],
                "repair_attempts": 0,
                "base_skills": BASE_UNION,
                "trace_id": "test-leading-entry-fabrication",
            },
            config={"configurable": {"thread_id": "test-leading-entry-fabrication"}},
        )

    assert final["guard_report"]["passed"] is False
    assert any("Rust" in v for v in final["guard_report"]["violations"])
    assert final["repair_attempts"] == 2, (
        "a fabrication the model never fixes must drive exactly two repair "
        f"rounds; got {final['repair_attempts']} (0 means the graph never saw "
        "the violation at all, which is the state this fix ends)"
    )
    assert calls["n"] == 3, f"1 initial + 2 repairs, got {calls['n']}"


def test_the_guard_still_finalizes_a_clean_real_resume():
    """The cost side. A widened claim-extractor that routes every real resume
    to repair would spend two extra LLM rounds on all of them, which is what
    rule 16 measures and what the single-row baseline in rule 15 caused."""
    result = og.check_output(resume(REAL_SKILLS), "tailor", base_skills_text=BASE_UNION)
    assert result.passed is True
    assert nodes.quality_gate({"guard_report": result.to_dict()}) == "finalize"


# ---------------------------------------------------------------------------
# Over-correction. Every assertion here fails if the widening goes too far.
# ---------------------------------------------------------------------------


def test_a_longer_word_is_not_a_claim():
    """The false-positive mechanism the boundary exists to stop.

    Dropping the boundaries -- plain substring search, the obvious way to make
    a claim visible wherever it sits -- reads "scalable" as Scala, "robust" as
    Rust and "Javadoc" as Java. Rule 16 measured what a detector with that
    error rate costs: 75.4% of real resumes flagged, ~52% of them wrongly.
    """
    for word in ("highly scalable services", "robust pipelines", "Javadoc tooling",
                 "Springboard mentoring", "guardrails"):
        skills = REAL_SKILLS.replace("Bash", f"Bash, {word}", 1)
        assert og.check_fabrication(BASE_UNION, resume(skills)) == [], word


def test_punctuated_and_multiword_tokens_survive_the_widening():
    """Over-splitting is the other way to overshoot: add "." or whitespace to
    the separators and "vue.js" and "spring boot" stop being findable as
    themselves."""
    for name in ("Vue.js", "Spring Boot"):
        skills = REAL_SKILLS.replace(":} Python", ":} " + name, 1)
        violations = og.check_fabrication(BASE_UNION, resume(skills))
        assert any(name.lower() in v.lower() for v in violations), (name, violations)


def test_spring_is_not_also_reported_for_a_spring_boot_claim():
    """One claim, one line in the repair prompt. "spring" is a blocklist token
    inside another blocklist token; reporting both describes one problem
    twice."""
    skills = REAL_SKILLS.replace(":} Python", ":} Spring Boot", 1)
    violations = og.check_fabrication(BASE_UNION, resume(skills))
    assert len(violations) == 1, violations
    assert "Spring Boot" in violations[0]


def test_the_region_scope_is_unchanged():
    """The widening is about reading the inspected regions correctly, NOT about
    inspecting more of the document. A whole-document fabrication detector was
    measured and refused: it fires on 533 of 707 real resumes (75.4%) with a
    hand-adjudicated ~52% false-positive rate, most often over the candidate's
    own degree (rule 16). A Java claim in Experience must stay invisible."""
    tex = resume(REAL_SKILLS).replace(
        r"\section*{Experience} \textbf{Engineer} built services.",
        r"\section*{Experience} \textbf{Engineer} built a Java service in Rust.",
    )
    assert og.check_fabrication(BASE_UNION, tex) == []


def test_the_three_refused_exculpations_stay_refused():
    """`check_fabrication`'s docstring refuses three tempting ways to clear a
    claim, each measured: substring containment (clears "scala" via
    "scalable"), common-English-word suppression (69% of real fabrications ARE
    dictionary words), and "the claim is in the job description" (92% of real
    fabrications are, because lifting the JD's requirement IS the failure
    mode). A wrong exculpation produces a miss, and a miss is the outcome this
    check exists to prevent."""
    # substring containment: the base talks about scalable systems, not Scala.
    scala = resume(REAL_SKILLS.replace(":} Python", ":} Scala", 1))
    assert og.check_fabrication(BASE_UNION + " highly scalable systems", scala)
    # dictionary words: "Rust", "Swift", "Dart", "Ruby" are all English words.
    for word in ("Rust", "Swift", "Dart", "Ruby"):
        skills = REAL_SKILLS.replace(":} Python", ":} " + word, 1)
        assert og.check_fabrication(BASE_UNION, resume(skills)), word
    # in the job description: the baseline is the candidate's resumes, and
    # check_fabrication is never handed the JD to exonerate against.
    php = resume(REAL_SKILLS.replace(":} Python", ":} PHP", 1))
    assert og.check_fabrication(BASE_UNION, php)


# ---------------------------------------------------------------------------
# Rule 14, structurally: the next divergence must fail CI, not review.
# ---------------------------------------------------------------------------


def test_the_claim_side_and_the_support_side_share_one_matcher():
    """Both sides are `_mentions`, asserted by identity rather than by
    behaviour, so re-introducing a second matcher fails here instead of
    quietly making one side weaker than the other again."""
    assert og._supported_by_base("java", r"\item \textbf{Primary:} Java") is True
    assert "java" in og._claims(r"\item \textbf{Primary:} Java")


@pytest.mark.parametrize("skill", sorted(og._KNOWN_FABRICATIONS))
def test_no_document_fabricates_against_itself(skill):
    """The invariant that holds only while the two sides agree.

    If a text asserts a skill, the SAME text as baseline must support it.
    Anything the claim side counts that the support side does not is, by
    construction, a false positive -- so this fails for every asymmetry an
    over-correction could introduce, for every token in the blocklist, without
    needing to guess which shape the next one arrives in.
    """
    skills = REAL_SKILLS.replace(":} Python", ":} " + skill.title(), 1)
    tex = resume(skills)
    assert og.check_fabrication(tex, tex) == []
    assert og.check_fabrication(skills, tex) == []
