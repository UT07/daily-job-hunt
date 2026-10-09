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

Two checks here are NOT about honesty and are much cheaper to be sure of
than fabrication, because neither needs a baseline to compare against —
`check_prompt_echo` and `check_near_empty`. Both were found in shipped
production resumes on 2026-09-30 while measuring 740 real artifacts, and
both are documented at their own definitions with the measured
false-positive rate and the denominator.
"""
import re
from typing import NamedTuple

from guardrails.policy import policy_for
from guardrails.types import GuardResult, Violation

# `shared` is a repo-root package that reaches zip Lambdas through the
# committed symlink lambdas/pipeline/shared -> ../../shared (copied in as real
# files by `sam build`, so it lands at /var/task/shared in the function's own
# artifact, not in the layer) and the container image via Dockerfile.lambda's
# `COPY shared/`, both pinned by tests/unit/test_deploy_path_parity.py. This is the first `shared.*` import
# inside the guardrails package; it is safe in every deploy path for the same
# reason tailor_resume.py's module-level `from shared.composition_policy
# import ...` is, and `extract_anchors` itself needs nothing but `re`.
from shared.resume_verify import extract_anchors

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


# Openers that describe a job description rather than a person. Position is the
# whole point, which is why this is not another entry in _BANNED_PHRASES: "was
# responsible for the migration" mid-sentence is clumsy, but a bullet that
# OPENS this way has given up its strongest word. Yale Office of Career
# Strategy's formula is ACTION VERB + task at scale + quantified result, and
# every phrase here occupies the verb slot without being one.
_WEAK_OPENERS = (
    "responsible for", "worked on", "helped with", "helped to", "assisted in",
    "assisted with", "duties included", "involved in", "tasked with",
    "participated in", "contributed to", "in charge of",
)

_BULLET = re.compile(r"\\item\s+(.{0,48})", re.DOTALL)


# A whole bullet, to the next \item or the end of its list — unlike _BULLET
# above, which deliberately truncates because it only inspects the opener.
_FULL_BULLET = re.compile(r"\\item\s+(.+?)(?=\n\s*\\item|\n\s*\\end\{itemize\})", re.DOTALL)
# The Skills section lists technologies, not achievements, and its entries use
# \item too. Counting them as unquantified bullets was a 34% "failure rate"
# that was really the wrong population (CLAUDE.md #7).
_SKILLS_SECTION = re.compile(r"\\section\*\{[^}]*Skills[^}]*\}(.*?)(?=\\section\*|\Z)", re.DOTALL)
_YEAR_ONLY = re.compile(r"\b(?:19|20)\d{2}\b")
_HAS_DIGIT = re.compile(r"\d")


def check_unquantified_bullets(tex: str) -> list[str]:
    r"""Achievement bullets that end on no measured result.

    Yale's formula is ACTION VERB + what you did and at what scale + QUANTIFIED
    RESULT, and the last term is the one that gets dropped. This names the
    bullets that dropped it, so a repair pass has something to act on —
    "7 of 24 bullets carry no number" plus the offending text beats repeating
    the rule at a model that already ignored it (CLAUDE.md #4).

    REPORTED, NEVER BLOCKING, and the measurement is why. Over 2,378
    achievement bullets in 100 live résumés:

        carry a number   1579   66.4%
        carry none        799   33.6%
        résumés with every bullet quantified   0 of 100

    A blocking version fails every résumé ever generated, which is exactly the
    detector CLAUDE.md #16 says to measure before shipping rather than after.
    It is also the wrong pressure: a hard counter is satisfied by inventing a
    number, and an invented figure is worse than an absent one. The policy's
    own writing rules already say to use only figures the base résumé supports
    and to state the outcome qualitatively where it gives none — so a bullet
    with no honest number available is CORRECT, and this warning exists to
    catch the ones that had a number available and did not use it.

    A bare year is not a result. "Migrated the platform in 2024" states when,
    not how much, so years are removed before looking for a digit.

    Skills entries are excluded: they list technologies, use `\item`, and must
    never carry numbers. Including them put the unquantified rate at 34% when
    the real figure for the population this judges is the same 33.6% measured
    over achievements alone — close by coincidence, and wrong by construction.
    """
    body = _SKILLS_SECTION.sub("", tex or "")
    missing = []
    total = 0
    for m in _FULL_BULLET.finditer(body):
        text = m.group(1)
        total += 1
        if not _HAS_DIGIT.search(_YEAR_ONLY.sub(" ", text)):
            missing.append(" ".join(text.split()))
    if not missing:
        return []
    shown = "; ".join(b[:90] for b in missing[:3])
    return [f"unquantified: {len(missing)} of {total} bullets end on no measured "
            f"result — add one ONLY where the base résumé supports it, never invent: {shown}"]


def check_weak_bullet_openers(tex: str) -> list[str]:
    """Bullets that open with a phrase where the action verb belongs.

    Reported rather than blocked: a weak opener is a writing problem, and
    falling back to the corpus over one would trade a whole tailored document
    for a phrase. It feeds the quality retry, which is the mechanism that can
    actually rewrite it.
    """
    out = []
    for m in _BULLET.finditer(tex or ""):
        head = re.sub(r"\\[a-zA-Z]+\s*|[{}]", " ", m.group(1)).strip().lower()
        for weak in _WEAK_OPENERS:
            if head.startswith(weak):
                out.append(f"weak_opener: a bullet begins \"{weak}\" — open "
                           f"with an action verb instead")
                break
    return out


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
    return _mention_re(skill).search(text.lower()) is not None


def _mention_re(skill: str) -> "re.Pattern[str]":
    """The boundary pattern `_mentions` searches with, for callers that need
    WHERE a mention is rather than whether there is one (`strip_fabrications`).

    Factored out rather than copied so the stripper can never remove a token
    the detector would not have flagged, or miss one it would (CLAUDE.md #10).
    Like `_mentions`, it expects LOWERCASED text.
    """
    return re.compile(rf"(?<![a-z0-9]){re.escape(skill.lower())}(?![a-z0-9])")


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
    return [(name, _plain(tailored_tex[start:end]))
            for name, start, end in _fabrication_spans(tailored_tex)]


def _fabrication_spans(tailored_tex: str) -> list[tuple[str, int, int]]:
    """(region name, start, end) of every region this guard inspects, in the
    RAW text. The one locator both the detector (`_fabrication_regions`) and
    the stripper (`strip_fabrications`) read, so the stripper edits exactly the
    text the detector judged and nothing else."""
    spans = []
    header = _HEADER_SUBTITLE_RE.search(tailored_tex)
    if header:
        spans.append(("header subtitle", header.start(1), header.end(1)))
    skills = _SKILLS_SECTION_RE.search(tailored_tex)
    if skills:
        spans.append(("Skills section", skills.start(1), skills.end(1)))
    return spans


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
    return [f"fabrication: '{skill.title()}' not in base resume ({region})"
            for skill, region in _fabrications(base_skills_text, tailored_tex)]


def _fabrications(base_skills_text: str, tailored_tex: str) -> list[tuple[str, str]]:
    """(blocklist token, first region it is claimed in) for every unsupported
    claim -- the findings `check_fabrication` words, and the exact set
    `strip_fabrications` is allowed to remove. One function, so the two can
    never disagree about what was flagged."""
    found = []
    seen = set()
    for region, text in _fabrication_regions(tailored_tex):
        for skill in sorted(_claims(text)):
            if skill in seen or _supported_by_base(skill, base_skills_text):
                continue
            seen.add(skill)
            found.append((skill, region))
    return found


# --- stripping a flagged claim ---------------------------------------------
#
# The user's requirement, verbatim: "I don't want fabricated shit but at the
# same time I do want the tailored resumes". Until 2026-10-09 a fabrication
# that survived every repair swapped the whole document for the corpus, which
# threw away the tailoring for one token in a comma list. What this detector
# flags is, by construction, one of 17 technology names in the header subtitle
# or the Skills section -- nearly always a list entry -- so it can be cut out.
#
# Conservative by design: anything that is not plainly a list entry (prose,
# a decorated token, a claim repeated outside the inspected regions, an edit
# that would leave an empty list or a dangling separator) returns `tex=None`
# and the caller keeps the corpus fallback. A wrong strip ships either a
# fabrication or a mangled line; a refused strip costs only the tailoring,
# which is exactly what happened before this existed.

# List separators. "tight" ones bind a pair ("Rust/TypeScript", "AWS \& GCP")
# and are removed in preference to a looser neighbour, so "A, Rust/TS, B"
# becomes "A, TS, B" rather than "A, Rust..."-shaped debris.
_TIGHT_SEP = r"(?:/|\\&|and)"
_LOOSE_SEP = r"(?:,|;|\||\\textbar(?:\{\})?)"
_SEP_BODY = (rf"(?:\s*{_LOOSE_SEP}(?:\s+and)?\s*|\s*(?:/|\\&)\s*|\s+and\s+)")
_LEFT_SEP_RE = re.compile(_SEP_BODY + r"\Z")
_RIGHT_SEP_RE = re.compile(_SEP_BODY)
# Where a list starts: an opening paren or brace, a category label's colon
# (`\textbf{Languages:}` or `Languages:`), a bare `\item`, or the region start.
_LEFT_OPEN_RE = re.compile(r"(?:\(|\{|:\}?|\\item)\s*\Z|\A\s*\Z")
# Where a list ends: a closing paren or brace, a full stop, or the next
# `\item` / `\end{...}` / line break / section / end of region.
_RIGHT_CLOSE_RE = re.compile(
    r"\s*(?:\)|\}|\.(?=\s|\Z|\\)|(?=\\item\b|\\end\{|\\\\|\\section|\Z))")
# A single-entry category line: `\item`, an optional label, then the token.
_ITEM_LABEL_RE = re.compile(r"\\item\s*(?:\\textbf\{[^{}]*:\}|[^\\{}\n:]*:)?\s*\Z")

# Shapes an edit must never CREATE. Counted before and after; any increase
# refuses the strip. Pre-existing occurrences are the model's, not ours.
_DAMAGE = tuple(re.compile(p) for p in (
    r"[,;|/]\s*[,;|/]",                       # doubled separator
    r"\(\s*\)",                                # empty parenthesis
    r"\(\s*(?:[,;|/]|and\s)",                  # list opening on a separator
    r"(?:[,;|/]|\sand)\s*\)",                  # list closing on a separator
    r":\}?[ \t]*[,;|/]",                       # category label then separator
    r"(?:[,;|/]|\\&)\s*(?=\\item\b|\\end\{|\Z)",  # dangling separator
    r"\\begin\{itemize\}\s*\\end\{itemize\}",     # emptied list
    r"\\item\s*(?:\\textbf\{[^{}]*\}\s*)?(?=\\item\b|\\end\{)",  # empty item
))


class StrippedFabrications(NamedTuple):
    """`tex` is the document with every flagged claim removed, or None when
    that cannot be done cleanly (then `reason` says why). `removed` holds each
    claim as it was written in the document, once."""
    tex: str | None
    removed: tuple[str, ...]
    reason: str


# What may trail a flagged token and still be part of the SAME list entry --
# the claim's own qualifier, never another claim. Measured on 740 real tailored
# résumés (2026-10-09): of the flagged documents the bare-token rule could not
# strip, the commonest shapes were "Rust (learning)", "Angular (familiar)",
# "Kotlin (learning)", "Angular 12+", "Angular.js" and "Ruby on Rails". The
# qualifier states something about the fabricated technology, so it goes with
# it; a parenthesis that nests, or holds markup, is not a plain qualifier.
_VERSION_TAIL = re.compile(r"(?:\.js\b)?(?:[ \t]*\d+(?:\.\d+)*(?:\+|\.x)?(?![\w.]))?")
_QUALIFIER_TAIL = re.compile(r"[ \t]*\([^(){}\\\n]*\)")
_ON = re.compile(r"\s+on\s+")


def _entry_span(region: str, start: int, end: int, flagged) -> tuple[int, int]:
    """Extend [start, end) over a "Ruby on Rails" compound of flagged tokens
    and the token's own version / parenthetical qualifier."""
    lower = region.lower()
    # Leftwards over "<flagged> on " and rightwards over " on <flagged>": both
    # halves are claims the detector flags, so nothing unflagged is removed.
    compound_left = re.compile(rf"(?:{flagged.pattern})\s+on\s+\Z")
    while (left := compound_left.search(lower, 0, start)) is not None:
        start = left.start()
    while True:
        on = _ON.match(lower, end)
        after = on and flagged.match(lower, on.end())
        if not after:
            break
        end = after.end()
    end = _VERSION_TAIL.match(region, end).end()
    qualifier = _QUALIFIER_TAIL.match(region, end)
    if qualifier:
        end = qualifier.end()
    return start, end


def _strip_one(region: str, start: int, end: int, flagged) -> str | None:
    """`region` with the list entry at [start, end) removed, or None.

    `flagged` matches any claim being stripped (lowercased), so an entry may
    span a compound of them but never an unflagged word."""
    if start and region[start - 1] == "\\":
        return None  # part of a macro name, not a word
    start, end = _entry_span(region, start, end, flagged)
    prefix, suffix = region[:start], region[end:]
    left_sep = _LEFT_SEP_RE.search(prefix)
    left_open = None if left_sep else _LEFT_OPEN_RE.search(prefix)
    right_sep = _RIGHT_SEP_RE.match(suffix)
    right_close = None if right_sep else _RIGHT_CLOSE_RE.match(suffix)

    if left_sep and right_sep:
        tight_left = re.fullmatch(rf"\s*{_TIGHT_SEP}\s*", left_sep.group(0))
        tight_right = re.fullmatch(rf"\s*{_TIGHT_SEP}\s*", right_sep.group(0))
        if tight_left and not tight_right:
            return region[:left_sep.start()] + suffix
        return prefix + suffix[right_sep.end():]
    if left_open and right_sep:
        return prefix + suffix[right_sep.end():]
    if left_sep and right_close:
        return region[:left_sep.start()] + suffix
    if left_open and right_close:
        opener, closer = left_open.group(0).strip(), right_close.group(0).strip()
        if opener == "(" and closer == ")":
            head = prefix[:left_open.start()].rstrip()
            return head + suffix[right_close.end():]
        if closer == "" and (opener.startswith(":") or opener == "\\item"):
            item = _ITEM_LABEL_RE.search(prefix)
            if item is not None:
                return region[:item.start()] + suffix[right_close.end():]
        return None
    return None  # prose, or a decorated token: not a list entry


def _as_written(tex: str, skill: str) -> str:
    """The first spelling of `skill` in the inspected regions, e.g. "Vue.js"."""
    pattern = _mention_re(skill)
    for _name, start, end in _fabrication_spans(tex):
        match = pattern.search(tex[start:end].lower())
        if match:
            return tex[start + match.start():start + match.end()]
    return skill.title()


def strip_fabrications(base_skills_text: str, tailored_tex: str) -> StrippedFabrications:
    """Remove every claim `check_fabrication` flags, from the regions it reads.

    Precise: the claims are `_fabrications`' (the detector's own findings), the
    regions are `_fabrication_spans` (the detector's own locator) and each
    occurrence is found with `_mention_re` (the detector's own boundary
    matcher), so "Java" never touches "JavaScript" and "Scala" never touches
    "Scalable". Nothing about what counts as a fabrication changes (#16).

    Conservative: refuses (tex=None) when any occurrence is not a plain list
    entry, when the claim is also made outside the inspected regions, or when
    the edit would leave an empty list, empty parenthesis or dangling
    separator. The caller must still re-run every gate on the result -- this
    function does not, on purpose, so there is one place that judges.
    """
    claims = _fabrications(base_skills_text, tailored_tex)
    if not claims:
        return StrippedFabrications(tailored_tex, (), "")
    if len(tailored_tex.lower()) != len(tailored_tex):
        return StrippedFabrications(None, (), "case folding changes offsets")

    tex = tailored_tex
    removed: list[str] = []
    flagged = re.compile("|".join(_mention_re(skill).pattern for skill, _ in claims))
    for skill, _region in claims:
        pattern = _mention_re(skill)
        # As written in the document, read BEFORE any edit: a compound entry
        # ("Ruby on Rails") can remove one claim while stripping another.
        written = _as_written(tailored_tex, skill)
        for _ in range(100):
            hit = None
            for _name, start, end in _fabrication_spans(tex):
                match = pattern.search(tex[start:end].lower())
                if match:
                    hit = (start, end, match)
                    break
            if hit is None:
                break
            start, end, match = hit
            region = tex[start:end]
            edited = _strip_one(region, match.start(), match.end(), flagged)
            if edited is None:
                return StrippedFabrications(
                    None, (), f"'{region[match.start():match.end()]}' is not a "
                              f"plain list entry")
            tex = tex[:start] + edited + tex[end:]
        else:
            return StrippedFabrications(None, (), f"'{skill}' did not converge")
        if _mentions(skill, tex):
            return StrippedFabrications(
                None, (), f"'{written}' is also claimed outside the regions "
                          f"the detector inspects")
        removed.append(written)

    for shape in _DAMAGE:
        if len(shape.findall(tex)) > len(shape.findall(tailored_tex)):
            return StrippedFabrications(
                None, (), f"the edit would leave a broken list ({shape.pattern})")
    return StrippedFabrications(tex, tuple(removed), "")


# --- prompt echo -----------------------------------------------------------
#
# Each entry is a family of phrases that exist in the TAILORING PROMPT and
# nowhere on a resume. The name is the repair feedback: repair_node folds
# violation text verbatim into the retry, so "planning_voice" plus the matched
# phrase tells the model what to delete, where a bare "output rejected" does
# not (CLAUDE.md rule 4).
#
# Chosen by measurement over the real population, not by taste. Two shipped
# resumes contain the model's own instruction vocabulary; the candidate set was
# scored against the other 738, and only markers at 0/738 were kept:
#
#     marker                     false positives over 738 real resumes
#     ------                     -------------------------------------
#     "header"  (bare word)      129/738  17.48%   <- REJECTED
#     "ensure"  (bare word)       56/738   7.59%   <- REJECTED
#     "job description"            2/738   0.27%   <- kept, see below
#     "keyword" / "safer"          1/738   0.14%   <- REJECTED
#     every marker below           0/738   0.00%
#
# "header" and "ensure" are the trap: they are in the leaked text, so a
# detector calibrated on the two known-bad documents would have picked them and
# fired on one resume in six. The bare words the incident report listed --
# 'alternatively', 'bullet', 'instead', 'example', 'thus', 'similarly',
# 'possibly' -- measure 0/738 today but are generic English with no
# self-referential meaning, and are deliberately NOT markers: a summary that
# says "however" is not a leaked prompt. What every family below has in common
# is that it can only be ABOUT the generation task. "job description" is kept
# despite 2/738 because those two are genuine leaks that the other families
# also catch, so it never fires alone (verified: removing it changes nothing on
# this population) -- and a resume that discusses "the job description" is
# leaking regardless.
_ECHO_MARKERS: dict[str, re.Pattern] = {
    # the prompt's names for its own inputs
    "base_resume_ref": re.compile(
        r"\bbase (?:resume|header|body|bullets?|template)\b", re.I),
    "jd_ref": re.compile(
        r"\bthe jd\b|\bjd[-\s](?:relevant|keywords?|terminology|vocabulary|terms?)\b",
        re.I),
    "job_description_ref": re.compile(r"\bjob description\b", re.I),
    # the prompt's names for its own output contract
    "output_contract_ref": re.compile(
        r"\breturn only\b|\btailored body\b|\bverbatim\b|\bsection headers?\b"
        r"|\bbullet counts?\b|\bmax_tokens\b", re.I),
    "fabrication_ref": re.compile(
        r"\bfabricat(?:e|ed|ing|ion)\b|\bdo not invent\b", re.I),
    # the model narrating its own process instead of producing the document
    #
    # The `(?!\s+Encrypt\b)` is not a nicety. Measured 2026-10-08 over all
    # 1,333 tailored resumes in S3, this marker fired on 177 of them — 13.3%,
    # where every other marker in this table sits at 0.1–0.2% — and 175 of the
    # 177 matches were the string "Let's", every one of them inside
    # "Traefik ingress with Let's Encrypt" in the Technical Skills section.
    # Re-measured with the exclusion in place: 2 of 1,333, or 0.2%.
    #
    # Let's Encrypt is a certificate authority. On an SRE resume it is a
    # correct, expected technical term, and this marker's severity is `block`,
    # so the guard was rejecting one resume in eight for naming it and spending
    # repair rounds telling the model to delete "Let's" — from a skills line
    # where the only thing it could delete is the technology.
    #
    # It also accounted for 4 of 5 failing tailor cases in the AI eval and so
    # for most of guard_pass_rate's fall from 0.92 to 0.84.
    #
    # The two survivors are genuine, and worth reading as proof the marker
    # earns its place — both are the model's planning text shipped verbatim
    # into a resume:
    #
    #     "Let's examine base resume sections to see what bold formatting
    #      exists"
    #     "Let's craft: IT Support / Sysadmin with \textbf{3+ years} of exper..."
    #
    # So the fix removes 175 false positives and keeps both true ones.
    #
    # The exclusion is CASE-SENSITIVE — `(?-i:Encrypt)` inside an otherwise
    # case-insensitive pattern. Every one of the 190 product-name occurrences
    # is written "Let's Encrypt" with a capital E, while every lowercase form
    # in the corpus is planning voice: rewrite, look, examine, go, check, list,
    # craft, reorder, put, reword. Both variants fire on the same 2 documents,
    # so this costs nothing measured and exonerates strictly less — it still
    # catches "Let's encrypt the database at rest", which a case-insensitive
    # lookahead cleared.
    #
    # KNOWN BLIND SPOT, found while measuring this and left alone deliberately:
    # the apostrophe class is `'` only, so the curly form U+2019 never matches.
    # 223 resumes contain "Let's Encrypt" with either apostrophe and only 177
    # reached this marker, meaning 46 used the curly one. Widening to ['\u2019]
    # would need the same Encrypt exclusion widened with it, and since the
    # genuine leaks both use a straight apostrophe there is nothing measured to
    # gain. Recorded rather than silently tolerated.
    #
    # The comment above says candidate markers were kept only at 0/738 false
    # positives. This one was not re-measured after the skills section grew to
    # include Let's Encrypt, which is CLAUDE.md #15: the corpus is the other
    # side of the comparison and it moved.
    "planning_voice": re.compile(
        r"\bwe (?:must|cannot|can|should|need to|have|will|are given)\b"
        r"|\blet'?s\b(?!\s+(?-i:Encrypt)\b)",
        re.I),
    "instruction_ref": re.compile(
        r"\bthe instructions?\b|\breminder:|\bthe rules? says?\b", re.I),
    # an unfilled placeholder sitting where content belongs
    "placeholder": re.compile(
        r"\.\.\.\s*tailored\s*\.\.\.|\bplaceholder\b|\[insert\b|<insert\b", re.I),
}


def check_prompt_echo(tex: str) -> list[str]:
    """The model's own instruction vocabulary, left inside the document.

    Two of 740 real production resumes (0.27%) contain it. Both are the whole
    chain of thought written into the .tex between \\begin{document} and
    \\end{document}: a0527faaf531 narrates "1. Header: We must change the
    \\normalsize title line", 773dd7cd6729 emits six section headers with
    "... tailored ..." under each and then argues with itself about whether the
    header block counts as body. A resume containing the word "jd-relevant" was
    sent to an employer.

    Neither is catchable structurally. 773dd7cd6729 carries all six required
    \\section* headers, balanced braces and 1728 words, so
    `check_required_sections`, `check_brace_balance` and tailor_resume's
    `word_count < 500` fallback all pass it, and 181 anchors put it far above
    `check_near_empty`'s floor. This check is the only thing in the repository
    that sees it.

    Measured false-positive rate: 0 of 737 real tailored resumes (0.00%), the
    737 being the 740-document population minus the three known defects.
    Measured on the body AND on the spliced document, because the shared
    preamble is the one piece of text every output has in common and a marker
    firing there would be a 100% false positive on tailor_resume's hard-gate
    path.

    LaTeX comments are stripped first, the same way
    `shared.resume_verify._strip_latex` does, and that is a scope decision
    rather than an exculpation of the kind CLAUDE.md rule 16 refuses: a `%`
    comment is not typeset, so it cannot reach an employer, which is the harm
    this check exists to prevent. It is not hypothetical — `resumes/fullstack.tex`
    carries the line `% "section header on page 1, content on page 2" awkward
    split`, an author's note about page breaks, and matching it made this check
    fire on the repository's own base resume, failing two tests in
    test_ai_truncation.py including the end-to-end eval-harness case (the same
    path CI's AI Eval Gate runs). The 740-document production population
    contains no comments at all, so this narrowing changes nothing there: both
    known-bad documents still fire on 5 and 7 marker families with comments
    stripped, because their leaked text is plain prose, not commented out.

    Returns one entry per marker FAMILY, not per match: the repair prompt needs
    to know what kind of text to remove, and eight copies of "planning_voice"
    would just crowd out the other violations.
    """
    tex = re.sub(r"(?<!\\)%.*", " ", tex)
    out = []
    for name, pattern in _ECHO_MARKERS.items():
        match = pattern.search(tex)
        if match:
            out.append(f"prompt_echo: {name} — the output contains "
                       f"{match.group(0)!r}, which is prompt text, not resume "
                       f"content; emit the document only")
    return out


# --- near-empty output -----------------------------------------------------

# An absolute floor on identity tokens, not a completeness measure. Measured
# over 740 real tailored resumes (body only, the input the guard is handed):
#
#     min 0   p1 133   p5 186   median 236   p90 248   max 289
#
# The distribution has exactly one member below 102: b45671b7ec5c, whose entire
# document body is the word "and". So every floor from 5 to 100 flags 1 of 740
# and that one is the known defect. 40 is chosen for margin on both sides --
# 2.55x below the lowest legitimate reading in the population, and far enough
# above 0 that a one-page resume from a future user with half this candidate's
# history still clears it. It is NOT tight enough to catch partial loss; that
# is shared.composition_policy's job, and DOC_RECALL_FLOOR's at ingest.
#
# Deliberately NOT tailor_resume.py's existing `word_count < 500` measure,
# which is not a near-empty instrument: it fires on 52 of 740 real stored
# outputs (7.03%) whose anchor counts are 131-228, because it strips
# `\textbf{Python}` whole and so undercounts exactly the LaTeX-dense bodies
# that carry the most content. At floor 40 this check fires on 1 of 740
# (0.14%) -- a 50x more precise instrument for the same question (CLAUDE.md
# rule 12).
NEAR_EMPTY_ANCHOR_FLOOR = 40


def check_near_empty(tex: str) -> list[str]:
    """Did the model return a document at all?

    `is_latex_document` and `sections_have_content` both pass b45671b7ec5c, a
    real shipped artifact whose body is the single word "and" -- the first
    because \\documentclass and \\begin{document} are present in the preamble
    the splice supplies, the second because it needs only ONE of five sections
    to be non-empty (CLAUDE.md rule 2 names it). A near-empty resume is worse
    than a failed one: a failure is visible and this is not.

    Instrument is `shared.resume_verify.extract_anchors` -- proper nouns,
    years and quantities, the tokens a document cannot be about a career
    without. The count, the floor and the margin are in NEAR_EMPTY_ANCHOR_FLOOR
    above; test_output_quality_echo_and_empty.py pins the floor against both
    the thinnest real resume and a body-less preamble, so a change to
    extract_anchors (e.g. PR #167) fails a test rather than silently moving
    this check's sensitivity.

    Reports the number, not just the verdict: a repair told "8 identity anchors,
    a complete resume has about 200" can act; "output rejected" cannot.
    """
    found = len(extract_anchors(tex, is_latex=True))
    if found >= NEAR_EMPTY_ANCHOR_FLOOR:
        return []
    return [f"near_empty: only {found} identity anchors (proper nouns, years, "
            f"quantities) in the output; the floor is {NEAR_EMPTY_ANCHOR_FLOOR} "
            f"and a complete resume carries about 200 — the document is "
            f"effectively blank"]


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
      - `prompt_echo` gates check_prompt_echo -> "block" violations, and
        `near_empty` gates check_near_empty -> "block" violations. Both are
        new in the task that added those checks; both are on for `tailor`
        and off everywhere else, because both thresholds were measured
        against 740 real tailored RESUMES and nothing else (CLAUDE.md
        rule 7). See `guardrails.policy` for what each other task would
        need measured before it could turn them on.
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

    if policy.get("prompt_echo"):
        for echo in check_prompt_echo(tex):
            # "block", for the same reason fabrication is: the document is
            # well-formed and wrong, and it leaves the system under the user's
            # own name. A resume carrying the phrase "jd-relevant" was sent to
            # an employer. Unlike fabrication this one is unambiguously
            # repairable -- the model already produced the resume, it just
            # shipped its notes alongside -- and repair_node hands it the
            # matched phrase verbatim, so the two bounded attempts are likely
            # to succeed. Cost is measured: it fires on 2 of 740 real outputs
            # (0.27%), so this buys at most 4 extra provider calls per 740
            # tailoring runs.
            violations.append(Violation("prompt_echo", echo, "block"))

    if policy.get("near_empty"):
        for empty in check_near_empty(tex):
            # "block" here is an ATTEMPT, not the guarantee. `quality_gate`
            # finalizes best-effort once `repair_attempts >= 2`, so a model
            # that keeps returning nothing still reaches finalize -- which is
            # precisely CLAUDE.md rule 2: a route that ends in the same state
            # whether the repair worked or was abandoned cannot be the only
            # defence. The guarantee is the matching hard gate in
            # tailor_resume.handler(), which discards the body and ships the
            # corpus instead; see the comment there. Blocking here as well is
            # still worth it because a repair is cheap on 1 output in 740 and
            # recovers the tailoring that the hard gate would throw away.
            violations.append(Violation("near_empty", empty, "block"))

    if base_body:
        for textbf_issue in check_textbf_preservation(base_body, tex):
            violations.append(Violation("textbf_preservation", textbf_issue, "warn"))

    return GuardResult(violations=violations)
