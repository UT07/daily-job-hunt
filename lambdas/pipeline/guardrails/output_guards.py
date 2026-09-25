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
scans the resume's Skills section (matched via a `\\section*{...Skills}`
regex) for a hardcoded list of ~15 skill keywords (Java, Vue.js, Angular,
Rust, ...) that do not appear in the candidate's base resume. Two things
narrow it further: (1) the tailoring system prompt already constrains the
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
not-yet-built work.
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


def check_fabrication(base_skills_text: str, tailored_tex: str) -> list[str]:
    """Check if tailored resume mentions skills not present in base."""
    _KNOWN_FABRICATIONS = {
        "java", "vue.js", "angular", "ruby", "php", "scala", "rust",
        "kotlin", "swift", "dart", "flutter", "spring", "hibernate",
        "django", "rails", "laravel", "spring boot",
    }
    base_lower = base_skills_text.lower()
    errors = []
    skills_match = re.search(
        r"\\section\*\{(?:Technical )?Skills\}(.*?)\\section\*\{",
        tailored_tex, re.DOTALL,
    )
    if not skills_match:
        return []
    clean = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", skills_match.group(1))
    clean = re.sub(r"[{}\\]", "", clean)
    for item in re.split(r"[,&\n]+", clean):
        skill = item.strip().lower()
        if skill and skill in _KNOWN_FABRICATIONS and skill not in base_lower:
            errors.append(f"fabrication: '{skill.title()}' not in base resume")
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
            violations.append(Violation("fabrication", fabrication, "warn"))

    if base_body:
        for textbf_issue in check_textbf_preservation(base_body, tex):
            violations.append(Violation("textbf_preservation", textbf_issue, "warn"))

    return GuardResult(violations=violations)
