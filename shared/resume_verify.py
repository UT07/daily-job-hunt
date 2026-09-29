"""Did the conversion carry the content across? Deterministic, no AI.

`sections_have_content` was the only gate on a PDF-to-LaTeX conversion, and it
returns True when ANY ONE of summary/skills/experience/projects/education is
non-empty. It was written to catch total loss — a 16,953-char resume rendering
as 1,807 chars of valid, empty LaTeX — and it does. It cannot catch partial
loss: a conversion that dropped two of three employers and all five projects
still has an Education section, so it passes cleanly.

That is not hypothetical. Measured 2026-09-29, an 8,490-char PDF became a
compilable two-page document holding 772 characters of content, and every
structural check in the repo went green: is_latex_document (markers present),
sections_have_content (one section non-empty), check_section_completeness
(matches header NAMES, and the renderer emits all six unconditionally).

So this module measures the one thing none of them do — whether the words
survived — by comparing anchors. An anchor is a token that a faithful
conversion has no reason to alter:

    proper nouns   Arlington, Clover, Kubernetes, Purrrfect
    years          2019, 2026
    quantities     43%, $2.4M, 8,000+, 99.9%

Prose gets reworded, reordered and re-escaped in a legitimate conversion.
Company names and numbers do not. Recall against that set separates "rendered
differently" from "lost", which is exactly the distinction every existing check
misses.

Deliberately AI-free. The project has been burned repeatedly by asking a model
to confirm its own output, and a lost employer is countable.
"""
from __future__ import annotations

import re

# Words that look like proper nouns because they start a sentence or a bullet,
# or because resumes shout them. Including these would inflate recall with
# tokens that carry no identity.
_STOPWORDS = frozenset("""
    a an and are as at be been built by can create created delivered design
    designed develop developed for from had has have implemented in into is it
    its led lead maintained managed of on or owned ran reduced released shipped
    so than that the their them then there these they this to use used using
    was were what when where which who will with worked
    summary experience education skills projects certifications profile
    objective awards publications technical featured professional employment
    present current remote onsite hybrid contract intern internship
    january february march april may june july august september october
    november december jan feb mar apr jun jul aug sep sept oct nov dec
    monday tuesday wednesday thursday friday
""".split())

# A capitalised or all-caps word, allowing internal punctuation so "Node.js",
# "CI/CD", "React-Native" and "AT&T" survive as single anchors.
_PROPER = re.compile(r"\b[A-Z][A-Za-z0-9]*(?:[.\-/&+][A-Za-z0-9]+)*\b")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
# 43%  $2.4M  8,000+  99.9%  3x  120ms
_QUANTITY = re.compile(r"(?<![\w.])(?:[$€£]\s?)?\d[\d,.]*\s?(?:%|[KMB]\b|x\b|ms\b|\+)?")

# LaTeX control sequences and their arguments' delimiters are formatting, not
# content. Stripping them stops \textbf and \begin{itemize} counting as anchors
# present on one side and absent on the other.
_TEX_COMMAND = re.compile(r"\\[a-zA-Z@]+\*?")
_TEX_BRACES = re.compile(r"[{}$&~^_\\]")


def _strip_latex(text: str) -> str:
    text = re.sub(r"(?<!\\)%.*", " ", text)          # comments
    text = _TEX_COMMAND.sub(" ", text)
    return _TEX_BRACES.sub(" ", text)


def extract_anchors(text: str, *, is_latex: bool = False) -> set[str]:
    """Tokens a faithful conversion should preserve verbatim.

    Case-folded, because a renderer may legitimately change capitalisation in a
    heading. Single characters and pure stopwords are dropped.
    """
    if not text:
        return set()
    if is_latex:
        text = _strip_latex(text)

    anchors: set[str] = set()
    for match in _PROPER.finditer(text):
        token = match.group(0)
        if len(token) >= 3 and token.lower() not in _STOPWORDS:
            anchors.add(token.lower())
    anchors.update(m.group(0) for m in _YEAR.finditer(text))
    for match in _QUANTITY.finditer(text):
        token = match.group(0).strip()
        # A bare small integer is noise — bullet numbering, a page number. A
        # quantity with a unit or separator is a claim.
        if len(token) > 2 or token.endswith(("%", "+")):
            anchors.add(token.replace(" ", ""))
    return anchors


def _display_forms(text: str) -> dict[str, str]:
    """folded token -> the casing it first appears with in the source.

    Comparison has to fold case, because a renderer may legitimately change
    capitalisation in a heading. Reporting should not: "Missing: Arlington,
    Purrrfect" is actionable, "missing: arlington, purrrfect" looks like a bug
    in the tool rather than a problem with the document.
    """
    forms: dict[str, str] = {}
    for match in _PROPER.finditer(text or ""):
        forms.setdefault(match.group(0).lower(), match.group(0))
    return forms


def anchor_recall(source: str, output: str, *, output_is_latex: bool = True
                  ) -> tuple[float, list[str]]:
    """Fraction of the source's anchors present in the output, and what is missing.

    Returns (1.0, []) for an empty source: nothing was asked for, so nothing was
    lost. A caller deciding whether to overwrite must check the source is
    non-empty separately — this function will not invent a failure.
    """
    wanted = extract_anchors(source)
    if not wanted:
        return 1.0, []
    have = extract_anchors(output, is_latex=output_is_latex)
    missing = sorted(wanted - have)
    return (len(wanted) - len(missing)) / len(wanted), missing


def describe_loss(source: str, output: str, *, limit: int = 25,
                  output_is_latex: bool = True) -> str:
    """A one-line report naming what went missing, for a user-facing message.

    Names the tokens rather than quoting a percentage: "Arlington, Purrrfect,
    UTWorld" tells someone what to check; "recall 0.62" does not.
    """
    recall, missing = anchor_recall(source, output, output_is_latex=output_is_latex)
    if not missing:
        return f"All {len(extract_anchors(source))} anchors preserved."
    forms = _display_forms(source)
    shown = ", ".join(forms.get(token, token) for token in missing[:limit])
    more = f" (+{len(missing) - limit} more)" if len(missing) > limit else ""
    return f"{recall:.0%} of content preserved. Missing: {shown}{more}"


def section_recall(source: str, output: str, *, output_is_latex: bool = True
                   ) -> dict[str, tuple[float, list[str]]]:
    """Anchor recall per source section: ``{section: (recall, missing)}``.

    A global threshold cannot see entry-scale loss. Measured against a real
    resume, deleting one of two employers moves whole-document recall only
    97.8% -> 94.1%, because that employer's tokens are a small share of 270
    anchors. Inside the Experience section alone the same deletion is
    unmissable.

    So the check is per section: a section that exists in the source has to be
    represented in the output. Sections are found by the same deterministic
    segmentation the parser uses, so this measures the document the parser
    actually saw.
    """
    from resume_parser import segment_sections  # local: avoids a cycle at import

    out: dict[str, tuple[float, list[str]]] = {}
    for name, body in segment_sections(source).items():
        if name == "_header":
            continue
        out[name] = anchor_recall(body, output, output_is_latex=output_is_latex)
    return out


# Chosen from measurement, not taste. Against a real resume and the .tex it was
# compiled from — a perfectly faithful pair — whole-document recall is 97.8%,
# the 2.2% being tokenisation artifacts (Route 53 vs Route53, a split URL).
# Realistic damage, same source:
#
#     faithful                                97.8%
#     one of two employers deleted            94.1%
#     education deleted (Arlington)           84.4%
#     projects section deleted                70.0%
#     the measured 772-char production case   41.1%
#
# 0.90 sits below every faithful reading and above every section-scale loss.
# It is NOT tight enough to catch a single dropped entry on its own, which is
# what SECTION_FLOOR is for.
DOC_RECALL_FLOOR = 0.90

# Per section the bar is higher, because at ingest there is no composition
# happening — this is a corpus and it is supposed to keep everything. Also
# measured rather than chosen. On the faithful pair, per section:
#
#     summary 100%   skills 98.8%   experience 100%
#     projects 95.8%   education 100%   certifications 100%
#
# so the worst faithful section is 95.8%. Deleting one of two employers takes
# the experience section to 80.8% while barely moving the whole-document number
# (97.8% -> 94.1%), which is exactly the loss a global floor cannot see.
#
# 0.88 sits between them with about 7 points of margin on each side.
SECTION_FLOOR = 0.88


def conversion_is_faithful(source: str, output: str, *, output_is_latex: bool = True
                           ) -> tuple[bool, str]:
    """Is this conversion good enough to replace a working resume?

    Returns (ok, reason). Conservative by construction: it answers "did we
    provably lose content", so anything it cannot measure passes. The caller
    must not treat a pass as proof the conversion is good — only as the absence
    of demonstrated loss.
    """
    if not source.strip():
        return True, "empty source; nothing to preserve"

    doc, missing = anchor_recall(source, output, output_is_latex=output_is_latex)
    if doc < DOC_RECALL_FLOOR:
        forms = _display_forms(source)
        return False, (f"only {doc:.0%} of the source survived conversion "
                       f"(floor {DOC_RECALL_FLOOR:.0%}). "
                       f"Missing: {', '.join(forms.get(t, t) for t in missing[:12])}")

    for name, (recall, gone) in section_recall(
            source, output, output_is_latex=output_is_latex).items():
        if recall < SECTION_FLOOR:
            forms = _display_forms(source)
            return False, (f"the {name} section only {recall:.0%} survived "
                           f"(floor {SECTION_FLOOR:.0%}). "
                           f"Missing: {', '.join(forms.get(t, t) for t in gone[:12])}")

    return True, f"{doc:.0%} of anchors preserved, every section above the floor"
