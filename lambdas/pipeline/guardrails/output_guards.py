"""Output-side guards for AI-generated resume/cover-letter content.

Companion to `guardrails.input_guards`: where the input side defends
against untrusted job-description text reaching a prompt, this module
checks what the model handed back before it is allowed to ship — LaTeX
structural integrity, banned filler phrases, formatting preservation, and a
narrow fabrication check.

These checks used to live inline in `tailor_resume.py` (and were called
directly from its `handler()`). They are consolidated here so both
`tailor_resume.py` and `score_batch.py` can share one policy-gated entry
point (`check_output`) instead of each re-implementing the wiring. The
individual `_check_*` names are still re-exported from `tailor_resume.py`
for backward compatibility with existing callers and test patch targets —
see that module's import block.

IMPORTANT — scope limitation of `check_fabrication`:
This guard does NOT detect fabrication across a tailored document. It only
scans the header subtitle and the resume's Skills section (matched via a
`\\section*{...Skills}` regex) for a hardcoded list of 17 skill keywords
(Java, Vue.js, Angular, Rust, ...) that do not appear in the candidate's base
resume. Two things narrow it further: (1) the tailoring system prompt already
constrains the
model to reorder-only within Skills, so the window this guard watches is
one the model is instructed not to touch anyway, and (2) it has no view of
the Experience, Projects, Summary or Certifications sections, where an
invented metric, employer, or accomplishment would actually land. A
retrieval-sweep benchmark (Task 18, see `scripts/bench_retrieval.py`)
measured this directly: 0.0% flagged with the evidence pool off vs 0.0%
with it on, n=10 per arm — not because fabrication doesn't happen, but
because this instrument cannot see where it would. Do not read a clean
`check_output` result as "the document contains no fabricated content";
read it as "the Skills section blocklist found nothing," which is a much
narrower claim. A whole-document fabrication detector is separate,
not-yet-built work — and deliberately so: measured over 707 real resumes it
fires on 533 (75.4%) with a hand-adjudicated ~52% false-positive rate, most
often over the candidate's own degree (CLAUDE.md rule 16).

Within those two regions, though, the guard must read ALL of the text it is
looking at, and for a long time it did not: `_plain` + field-equality
matching hid the leading entry of every `\\item`, which is where the real
corpus row puts a technology on each of its seven category lines. See
`_mentions` for what that missed, and
tests/unit/test_fabrication_claim_extraction.py for the fix's measurement.
"Narrow scope" is a decision about which regions to inspect; it was never a
licence to read those regions partially.
"""
import re

from guardrails.policy import policy_for
from guardrails.types import GuardResult, Violation

_REQUIRED_SECTIONS = ["experience", "skills", "education", "projects", "certifications"]


def check_header_present(tex: str, markers: list[str]) -> list[str]:
    """Return list of header markers missing from the tex. Empty = all present
    (or no markers configured, in which case the check is a no-op)."""
    if not markers:
        return []
    return [m for m in markers if m not in tex]


def check_required_sections(tex: str) -> list[str]:
    """Return the list of required section keywords NOT found in any \\section heading.

    Uses substring matching so "Work Experience" satisfies "experience" and
    "Technical Skills" satisfies "skills". Matches the downstream compiler gate.
    """
    heads = [h.lower() for h in re.findall(r"\\section\*?\{([^}]*)\}", tex)]
    return [s for s in _REQUIRED_SECTIONS if not any(s in h for h in heads)]


_BANNED_PHRASES = [
    "highly motivated", "extensive experience", "proven track record",
    "passionate about", "self-motivated", "team player", "detail-oriented",
    "results-driven", "strong background in", "experienced professional",
    "seasoned professional", "leveraging", "utilizing", "showcasing",
    "demonstrating proficiency", "directly transferable to", "aligned with",
    "outcomes relevant to", "i am excited", "excited to join",
    "results-oriented", "spearheaded", "facilitated", "synergies",
    "robust", "seamless", "cutting-edge", "innovative",
    "in today's fast-paced world", "demonstrated ability to",
]


def check_banned_phrases(tex: str) -> list[str]:
    """Check for banned filler phrases in the tailored body."""
    tex_lower = tex.lower()
    return [f"banned_phrase: '{p}'" for p in _BANNED_PHRASES if p in tex_lower]


def check_brace_balance(tex: str) -> bool:
    """Return True if {/} are balanced (ignoring \\{ and \\})."""
    depth = 0
    i = 0
    while i < len(tex):
        if tex[i] == "\\" and i + 1 < len(tex) and tex[i + 1] in "{}":
            i += 2
            continue
        if tex[i] == "{":
            depth += 1
        elif tex[i] == "}":
            depth -= 1
            if depth < 0:
                return False
        i += 1
    return depth == 0


def check_textbf_preservation(base_body: str, tailored_body: str) -> list[str]:
    r"""Check that \textbf formatting is preserved from base resume."""
    base_count = len(re.findall(r"\\textbf\{", base_body))
    tailored_count = len(re.findall(r"\\textbf\{", tailored_body))
    if base_count == 0:
        return []
    ratio = tailored_count / base_count
    if ratio < 0.5:
        return [
            f"textbf_stripped: base has {base_count} \\textbf, tailored has {tailored_count} "
            f"({ratio:.0%} preserved, need >=50%)"
        ]
    return []


_KNOWN_FABRICATIONS = {
    "java", "vue.js", "angular", "ruby", "php", "scala", "rust",
    "kotlin", "swift", "dart", "flutter", "spring", "hibernate",
    "django", "rails", "laravel", "spring boot",
}

# The header subtitle: the {\normalsize ...} line directly under the candidate's
# name, which this template fills with a parenthesised technology list --
#     {\normalsize Software Engineer (SRE, Data Platform, Mobile, Rust/TypeScript, AWS K8s)}
# Measured on 120 real production resumes, 19 of 26 unsupported technology claims
# live on THIS line, and the Skills-section regex below cannot reach it: it spans
# \section*{Technical Skills} to the next \section*{, and the header sits before
# the first section. The deployed guard caught 7 of 21 affected resumes; the
# other 14 were all here. It is also the most prominent line on the page -- the
# first thing a recruiter reads after the name.
_HEADER_SUBTITLE_RE = re.compile(r"\{\\normalsize\s+(.*?)\}\s*\\\\", re.DOTALL)

_SKILLS_SECTION_RE = re.compile(
    r"\\section\*\{(?:Technical )?Skills\}(.*?)\\section\*\{", re.DOTALL
)


def _mentions(skill: str, text: str) -> bool:
    """Is `skill` asserted anywhere in `text`, by word boundary?

    The ONE matcher, used for both sides of the fabrication comparison: the
    claim side (`_claims`) and the support side (`_supported_by_base`). They
    were different before -- the base was searched with this boundary regex
    while the tailored text was split on `[,&/()\\n]+` and compared for field
    EQUALITY -- and the asymmetry is what CLAUDE.md rule 14 is about: when a
    comparison decides whether to accept output, both sides must count the
    same things. Field equality counted fewer, so the guard could see a claim
    in the base resume that it could not see in the resume being checked.

    What that cost, concretely. `_plain` turns

        \\item \\textbf{Primary:} Java, Python

    into `item Primary: Java, Python`, whose first field is `item Primary:
    Java` -- not equal to any blocklist token, so the FIRST entry of every
    `\\item` was invisible. That is not a hypothetical shape: the real
    2026-09-28 `user_resumes.tex_content` row writes its whole Technical
    Skills section as seven `\\item \\textbf{Category:} ...` lines, so in
    production the leading technology of every category sat outside the
    guard's view. Field equality also missed `Java;`, `Java.`,
    `Java-based` and `AWS\\\\Java` for the same reason -- anything whose
    field was not the bare token.

    The boundary is alphanumeric-only, not \\b: "vue.js" and "spring boot"
    contain a dot and a space, and "rust" must still match "Rust,",
    "(Rust/TypeScript)" and "\\textbf{Rust}". What must not match is a longer
    alphanumeric word that merely contains it -- "javascript" must never
    stand in for "java", "scalable" never for "scala", "robust" never for
    "rust". That is the same containment the docstring of `check_fabrication`
    refuses as an exoneration, refused here in the other direction too.

    Case folding happens HERE, on both arguments, rather than being each
    caller's job. It was the caller's job for the base side (`base_lower`) and
    the matcher's job for the claim side, and that is one more way for the two
    sides to stop counting the same things: a caller that forgets makes the
    boundary regex -- all lowercase -- match nothing, every claim unsupported,
    and every tailored resume a blocking fabrication. A test caught exactly
    that while this was being written.
    """
    return re.search(
        rf"(?<![a-z0-9]){re.escape(skill.lower())}(?![a-z0-9])", text.lower()
    ) is not None


def _claims(text: str) -> set[str]:
    """Blocklisted technologies asserted in `text`.

    Longest match wins when one blocklist token contains another -- today only
    "spring" inside "spring boot". Reporting both would put two lines in the
    repair prompt for one claim, and the repo already cares about that (see
    the dedupe across regions in `check_fabrication`). This is a report
    dedupe, NOT the substring exoneration `check_fabrication`'s docstring
    refuses: the claim is still reported, under its more specific name, and
    the severity is unchanged. It also cannot hide anything, because
    `_mentions` is symmetric -- a base resume containing "spring boot"
    supports "spring" too, so dropping the shorter token can never turn an
    unsupported claim into a supported one.
    """
    found = {skill for skill in _KNOWN_FABRICATIONS if _mentions(skill, text)}
    return {s for s in found if not any(s != t and s in t for t in found)}


def _supported_by_base(skill: str, base_tex: str) -> bool:
    """Is `skill` present in the base resume, by word boundary not substring?

    Both halves of this were measured, and they are only correct together.

    Substring containment silently exonerated the most-fabricated token in the
    blocklist: the corpus Skills section reads "TypeScript/JavaScript (React,
    ...)", "java" is a substring of "javascript", and so a standalone Java claim
    could never be flagged. 482 of 707 real outputs list Java as a bare comma
    item; none was flaggable.

    But word boundaries alone make it far worse, because the baseline matters
    more than the matching. Measured over 140 real resumes:

        baseline = production row only,  substring       23/140 (16.4%)
        baseline = production row only,  word boundary   97/140 (69.3%)
        baseline = union of ALL rows,    word boundary   23/140 (16.4%)

    The 74-resume gap is Java, and it is not fabrication: Java is in the
    candidate's 2026-04-05 corpus row and missing only from the degraded
    2026-09-28 row production now tailors from. Against one row, word boundaries
    turn this guard into a corpus-drift alarm that blocks 69% of resumes and
    spends two repair rounds on each, over content the candidate actually has.

    So the caller must pass the union of every user_resumes row -- see
    shared.resume_format.BaseResume.all_tex, which exists for this. "Has the
    candidate ever claimed this?" is a question about the whole profile; asking
    it of one revision convicts them of their own deleted history.

    Matching itself is `_mentions`, shared verbatim with `_claims` -- see there
    for why the boundary is alphanumeric-only, why the two sides must not have
    separate matchers, and why case folding belongs to the matcher rather than
    to this function's caller.
    """
    return _mentions(skill, base_tex)


def _plain(fragment: str) -> str:
    r"""Unwrap one level of LaTeX macro, then turn delimiters into separators.

    Separators, not deletions. Deleting them glued neighbouring words into one
    alphanumeric run, which then failed `_mentions`' boundary test and hid the
    claim: `AWS\\Java` collapsed to `AWSJava`, and "java" preceded by "S" does
    not match. Every replacement here is a space for that reason -- this
    function may only ever ADD word boundaries, never remove one, or it
    becomes a way for the model's own line breaks to smuggle a claim past the
    guard.
    """
    out = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r" \1 ", fragment)
    return re.sub(r"[{}\\]", " ", out)


def _fabrication_regions(tailored_tex: str) -> list[tuple[str, str]]:
    """(region name, plain text) for every region this guard inspects.

    Regions rather than one blob so a violation can say WHERE the claim is. A
    repair round that is told "in the header subtitle" can fix the right line;
    "somewhere in the document" invites the model to rewrite the whole thing.
    """
    regions = []
    header = _HEADER_SUBTITLE_RE.search(tailored_tex)
    if header:
        regions.append(("header subtitle", _plain(header.group(1))))
    skills = _SKILLS_SECTION_RE.search(tailored_tex)
    if skills:
        regions.append(("Skills section", _plain(skills.group(1))))
    return regions


def check_fabrication(base_skills_text: str, tailored_tex: str) -> list[str]:
    """Blocklisted technologies claimed in the header or Skills but not in base.

    `base_skills_text` is the comparison baseline and is named for the Skills
    section it was originally extracted from; anything the caller passes is
    compared against verbatim.

    Deliberately NOT changed while extending the region: the blocklist stays a
    fixed 17-token vocabulary rather than becoming "any capitalised token absent
    from the corpus". Measured over 707 real resumes, that general form fires on
    533 (75.4%) after every safe normalisation, with a hand-adjudicated ~52%
    false-positive rate -- as a blocking check it would stop three of four
    resumes, most often over the candidate's own degree. Three tempting
    exculpations were also measured and must stay refused: substring containment
    (suppresses 'scala' via 'scalable'), common-English-word suppression (69% of
    real hits ARE dictionary words), and "it is in the job description" -- 92% of
    real hits appear in the JD, because lifting the JD's requirement is the
    failure mode, not an excuse for it.

    What DID change (see `_mentions`): the claim side now uses the same
    boundary matcher as the support side, instead of splitting the region into
    fields and comparing them for equality. That is a widening, so it was
    measured before shipping, per rule 16, by
    `scripts/measure_fabrication_claims.py` -- four arms, because the baseline
    is half of the comparison (rule 15). On the real tailored documents
    reachable from this repository it newly exposes 33 leading `\\item`
    entries across 4 documents, none of them blocklisted, for 0 new flags
    against either the union or the single-row baseline, with the harness's
    self-check confirming both arms diverge on that position. Four is not the
    740 the gate wants; re-run it with `--source loader` wherever the real
    corpus is reachable before treating the false-positive rate as measured.
    """
    errors = []
    seen = set()
    for region, text in _fabrication_regions(tailored_tex):
        for skill in sorted(_claims(text)):
            if skill in seen or _supported_by_base(skill, base_skills_text):
                continue
            seen.add(skill)
            errors.append(f"fabrication: '{skill.title()}' not in base resume ({region})")
    return errors


def check_output(
    tex: str,
    task: str,
    *,
    base_body: str = "",
    base_skills_text: str = "",
    header_markers: list[str] | None = None,
) -> GuardResult:
    """Aggregate the output-side content guards behind one policy-gated call.

    New in this module — the individual `check_*` functions above are a
    verbatim move from `tailor_resume.py`, but this aggregator is new glue
    that did not exist before. It wires `policy_for(task)` to the checks
    that already existed, mirroring how `tailor_resume.handler()` used them
    inline (see git history) so the severities below match that call site's
    actual behaviour rather than being invented fresh:

      - `latex_structure` gates check_brace_balance, check_required_sections
        and check_header_present (only when `header_markers` is non-empty).
        These map to "block" violations because the handler's own logic
        discarded the tailored output entirely on any of these failing
        (fallback to the base resume) — there was no repair path.
      - `banned_phrases` gates check_banned_phrases -> "warn" violations,
        matching the handler's "quality_warnings" treatment (triggers one
        retry with feedback, never a hard fallback).
      - `fabrication` gates check_fabrication -> "warn" violations, same
        quality_warnings treatment. Only runs when `base_skills_text` is
        supplied, matching the handler's own guard (it only ever called
        this check when the base resume's Skills section was found). See
        the module docstring for this check's real, narrow scope — it is
        NOT a whole-document fabrication detector.
      - check_textbf_preservation has no dedicated policy flag in
        `guardrails.policy` today. It runs unconditionally whenever
        `base_body` is supplied (its own required input), as a "warn"
        violation, on the same quality_warnings footing as banned_phrases
        and fabrication. Revisit if/when a flag is added for it.

    Callers not yet wired to this function (e.g. tailor_resume.handler())
    keep calling the re-exported `_check_*` names directly — a future task
    wires check_output itself into the LangGraph council as a graph node.
    """
    policy = policy_for(task)
    violations: list[Violation] = []

    if policy.get("latex_structure"):
        if not check_brace_balance(tex):
            violations.append(Violation("brace_balance", "unbalanced braces in output", "block"))
        for missing_section in check_required_sections(tex):
            violations.append(Violation("required_sections", f"missing section: {missing_section}", "block"))
        if header_markers:
            for missing_marker in check_header_present(tex, header_markers):
                violations.append(Violation("header_present", f"missing header marker: {missing_marker}", "block"))

    if policy.get("banned_phrases"):
        for phrase in check_banned_phrases(tex):
            violations.append(Violation("banned_phrase", phrase, "warn"))

    if policy.get("fabrication") and base_skills_text:
        for fabrication in check_fabrication(base_skills_text, tex):
            # "block", unlike every other non-structural check here. The
            # severity split above is otherwise a compile-integrity axis --
            # unbalanced braces and missing sections break the document, so
            # they block; filler phrases and lost \textbf are cosmetic, so
            # they warn. Fabrication does not break the document, which is
            # how it ended up on the cosmetic side of a distinction that was
            # never about honesty. It belongs with the blocking checks for a
            # different reason: the document is well-formed and false.
            #
            # Measured, not assumed. CI run 36651253369 (eval case
            # 12ed5b1de5e8, tailor, gemini-3.5-flash-lite) emitted a resume
            # listing Rust, which is not in the base resume. This guard
            # caught it. `GuardResult.passed` is `not any(severity ==
            # "block")`, so at "warn" the case reported guards_passed=True,
            # quality_gate read a passing report and routed to finalize, and
            # the resume shipped. The repair loop that exists to fix exactly
            # this was armed, correctly wired, and never told: repair_node
            # folds violation text verbatim into the retry prompt, so at
            # "block" the model is handed "fabrication: 'Rust' not in base
            # resume" and gets two bounded attempts to correct it.
            #
            # This is the one check whose output leaves the system under the
            # user's own name and over their signature. A false claim of
            # language experience on a submitted job application is not a
            # quality regression to log and ship; it is the single outcome
            # this pipeline must never produce. Latency is the cost severity
            # exists to protect (see guardrails/types.py) and two repair
            # rounds is the right price here.
            violations.append(Violation("fabrication", fabrication, "block"))

    if base_body:
        for textbf_issue in check_textbf_preservation(base_body, tex):
            violations.append(Violation("textbf_preservation", textbf_issue, "warn"))

    return GuardResult(violations=violations)
