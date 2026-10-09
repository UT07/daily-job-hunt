"""The prompt-echo guard blocked one resume in eight for naming a CA.

Measured 2026-10-08 over all 1,333 tailored resumes in S3:

    marker              fires   rate
    planning_voice        177  13.3%     <- every other marker: 0.1-0.2%
    base_resume_ref         2   0.2%
    jd_ref                  1   0.1%
    output_contract_ref     2   0.2%
    ...

175 of those 177 matches were the string "Let's", every one of them inside
"Traefik ingress with Let's Encrypt" in the Technical Skills section. Let's
Encrypt is a certificate authority; on an SRE resume it is a correct and
expected term.

The marker's severity is `block`, so `GuardResult.passed` was False for 13% of
real resumes, and the repair loop was told to remove "Let's" from a skills line
where the only thing it could remove is the technology. It also accounted for
4 of 5 failing tailor cases in the AI eval, and so for most of
guard_pass_rate's fall from 0.92 to 0.84.

CLAUDE.md #16: measure a detector's false-positive rate before shipping it,
not after. And #15: the marker WAS measured at 0/738 when it was written — the
corpus moved underneath it when the skills section grew to include Let's
Encrypt. A comparison has two sides.

A MEASURED NON-FINDING, recorded because the next inference is the obvious one
and it is wrong. 139 of the résumés mentioning Traefik have no "Let's Encrypt"
at all, some rewritten to "Traefik ingress with automated TLS" — which reads
exactly like the repair loop working around the banned string, since
repair_node folds violation text into the retry verbatim. It is not. Grouped
by S3 LastModified:

    date          traefik  intact  lost   lost%
    2026-09-29          7       4     3     43%
    2026-09-30         18      10     8     44%   <- the guard lands
    2026-10-02         12      11     1      8%
    2026-10-07        111      68    43     39%
    2026-10-08        152      84    68     45%

The loss rate before the guard existed is the same as after. Composition
varies that skills line on its own, and the guard is innocent of it. What the
false positive actually cost is repair rounds and a `guards_passed: False` on
valid documents — bad, and bounded. Whether any shipped résumé was degraded by
it is NOT established, and this table is why.
"""
import pytest

from lambdas.pipeline.guardrails.output_guards import check_prompt_echo


# Verbatim from users/.../resumes/ff228cfef3df_tailored.tex, which is one of
# the 175. Shortened only at the ends.
REAL_SKILLS_LINE = (
    r"\item \textbf{Containers \& Orchestration:} Docker (multi-stage, "
    r"multi-arch buildx), Kubernetes (EKS, k3s), Helm, Kustomize, HPA, "
    r"PodDisruptionBudgets, liveness/readiness probes, CronJobs, Traefik "
    r"ingress with Let's Encrypt, Docker Compose, ECR, blue/green and canary "
    r"rollouts"
)

# The two genuine leaks the same scan found, also verbatim. These must stay
# caught: they are the model's planning voice shipped into a real resume.
REAL_LEAK_1 = (
    "bold as is and modify surrounding text but not remove any \\textbf{}. "
    "Let's examine base resume sections to see what bold formatting exists."
)
REAL_LEAK_2 = (
    "- reduced release lead time by 85 - sustained 99.9 Let's craft: "
    "IT Support / Sysadmin with \\textbf{3+ years} of experience"
)


def _planning(tex):
    """Violations from the planning_voice marker, matched EXACTLY.

    This was `"planning_voice" in v`, and a mutation renaming the marker to
    `planning_voice_disabled` survived the whole suite — the substring still
    matched, so a test for a deleted guard passed. The same substring trap
    CLAUDE.md #15 describes, one layer up.
    """
    return [v for v in check_prompt_echo(tex)
            if v.startswith("prompt_echo: planning_voice ")]


def test_the_real_skills_line_is_not_prompt_echo():
    assert _planning(REAL_SKILLS_LINE) == [], (
        "Let's Encrypt read as the model's planning voice — this fired on 175 "
        "of 1,333 real resumes and the violation BLOCKS")


@pytest.mark.parametrize("tex", [
    r"Traefik ingress with Let's Encrypt",
    r"certbot + Let's Encrypt for TLS renewal",
    r"Let's Encrypt wildcard certificates via DNS-01",
    r"ACME/Let's Encrypt automation",
])
def test_the_ca_as_it_is_actually_written_is_allowed(tex):
    """Title case, which is how all 190 real occurrences are written.

    An earlier version of this test also asserted "LET'S ENCRYPT" and
    "lets encrypt" were allowed. Both were cases I invented, neither occurs in
    the corpus, and asserting them forced a case-INSENSITIVE exclusion that
    also cleared "Let's encrypt the database at rest" — genuine planning voice.
    The measurement is the authority, not my guess at what might show up.
    """
    assert _planning(tex) == [], tex


@pytest.mark.parametrize("tex", [
    "Let's encrypt the database at rest",
    "Let's encrypt everything in transit",
])
def test_lowercase_encrypt_is_still_planning_voice(tex):
    """The exclusion is deliberately narrow.

    Of the 190 product-name occurrences, every one is "Let's Encrypt" with a
    capital E; every lowercase form in the corpus is the model talking to
    itself (rewrite, look, examine, go, check, list, craft, reorder, put,
    reword). A case-insensitive exclusion measured identically on real data —
    both fire on the same 2 documents — so the cased one is free and exempts
    less.
    """
    assert _planning(tex), f"cleared genuine planning voice: {tex!r}"


# ---- the detections that must survive the fix ----

def test_the_two_real_leaks_are_still_caught():
    for leak in (REAL_LEAK_1, REAL_LEAK_2):
        assert _planning(leak), (
            f"a genuine planning-voice leak stopped being detected: {leak[:70]!r}")


@pytest.mark.parametrize("tex", [
    "Let's start with the base resume",
    "Let's craft: Senior SRE with 5 years",
    "Lets examine the job description first",   # the apostrophe is optional
    "We need to keep exactly three projects",
    "we are given the following bullets",
    "We must not invent any skills",
])
def test_planning_voice_is_still_detected(tex):
    assert _planning(tex), f"missed planning voice: {tex!r}"


def test_the_exclusion_is_specific_to_the_ca():
    """`Let's` followed by anything else is still planning voice.

    A blanket exoneration of "Let's" would have been the smaller change and
    would have lost both real detections. The lookahead is deliberately
    narrowed to the one word that made it a false positive.
    """
    assert _planning("Let's encrypt the database at rest"), (
        "a lookahead this loose would clear 'Let's' before any word")
    assert _planning("Let's Encryption is not a product name")


def test_a_resume_naming_the_ca_and_also_leaking_is_still_caught():
    """One must not mask the other: the real documents contain long skills
    lines, so an early allowed match cannot be allowed to end the search."""
    both = REAL_SKILLS_LINE + "\n\n" + REAL_LEAK_1
    assert _planning(both), (
        "the allowed Let's Encrypt match swallowed the genuine leak that "
        "followed it")
