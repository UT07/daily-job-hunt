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

    names, places, tools   Arlington, Clover, Kubernetes, Purrrfect, kubectl
    years                  2019, 2026
    quantities             43%, $2.4M, 8,000+, 99.9%

What is NOT an anchor is the vocabulary a legitimate conversion may rephrase:
function words, month names, section headings, and the capitalised verbs a
résumé bullet opens with ("Orchestrated", "Configured", "Skilled in"). Recall
against what remains separates "rendered differently" from "lost", which is
exactly the distinction every existing check misses.

Two things about that set are easy to get wrong, and both were wrong here until
2026-09-30 — each one making the instrument report LESS loss than had occurred:

  * **Case is not identity.** The pattern required a capital initial, so a
    source that wrote "we use kubernetes daily" produced NO anchors at all and
    every output satisfied it, including one that kept nothing. That is free
    credit, not a false alarm — CLAUDE.md rule 2.

  * **The set must be closed under punctuation splitting.** The pattern absorbs
    ``. - / & +`` so "TypeScript/JavaScript" was a single token that no set
    containing "typescript" and "javascript" could ever satisfy. Identical
    content, written two ways, measured as 0.0% recall. Membership is therefore
    tested per punctuation-part against the output TEXT, not by set difference.

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

# The verbs a résumé bullet opens with. They are capitalised by position, not by
# identity, so the capital-initial rule extracted them as proper nouns: measured
# over 707 real résumés, 868 of 3116 residual anchor instances (27.9%) were
# these — 'orchestrated' in 168 résumés, 'skilled' 62, 'collaborated' 49,
# 'scripting' 31, 'holds' 27, 'optimized' 27, 'engineered' 22, 'applied' 21,
# 'enforced' 19, 'configured' 16, 'secured' 13, 'instrumented' 8.
#
# Excluding them does two things. It stops a real loss being diluted by tokens
# that carry no identity, and — since these are precisely the words a faithful
# conversion may rephrase — it stops rephrasing reading as loss. Measured on 27
# real (PDF text, .tex) pairs with every verb replaced by a synonym and every
# name, number and macro left byte-identical: the worst section scores 0.972
# with this list and 0.909 without it, against a floor of 0.88.
#
# Verbs ONLY. A longer list that also held generic nouns (data, engineer,
# systems, security, platform, new) was measured and rejected: audited against
# the same 27 documents those are parts of real identities — "New York",
# "Data Engineering Intern", "AWS Certified Solutions Architect" — so removing
# them deletes anchors that DO carry identity.
_BULLET_VERBS = frozenset("""
    accelerated achieved adapted added addressed administered adopted advanced
    analysed analyzed applied architected assessed audited authored automated
    benchmarked boosted championed collaborated completed conducted configured
    consolidated containerised containerized contributed converted coordinated
    cut debugged decreased defined deployed deprecated diagnosed documented
    doubled drove eliminated enabled engineered enforced enhanced ensured
    established evaluated executed expanded facilitated fixed generated grew
    halved handled hardened headed held holding holds identified improved
    increased initiated instrumented integrated introduced launched leveraged
    mentored migrated modernised modernized monitored onboarded operated
    optimised optimized orchestrated oversaw partnered patched performed
    pioneered planned presented processed produced profiled promoted
    provisioned prototyped published rearchitected rebuilt reconciled
    redesigned refactored removed reorganised reorganized replaced researched
    resolved restructured revamped rewrote saved scaled scoped scripted
    scripting secured simplified skilled slashed spearheaded standardised
    standardized
    streamlined supervised supported sustained tested tracked trained
    transferred transformed translated tripled troubleshot tuned unified
    updated upgraded utilised utilized validated verified wrote
""".split())

_STOPWORDS = _STOPWORDS | _BULLET_VERBS

# A word, allowing internal punctuation so "Node.js", "CI/CD", "React-Native"
# and "AT&T" survive as single anchors.
#
# NOT anchored on [A-Z]: a capital initial identifies a token's POSITION in a
# sentence, not its identity, and demanding one made a lowercase source ask for
# nothing. "we use kubernetes daily" extracted zero anchors, so an output that
# kept none of it scored 1.000 — the same as one that kept all of it.
# _STOPWORDS is what separates identity from prose, and it does that regardless
# of case.
_ANCHOR_WORD = re.compile(r"\b[A-Za-z][A-Za-z0-9]*(?:[.\-/&+][A-Za-z0-9]+)*\b")

# The punctuation _ANCHOR_WORD absorbs, and the shape of an anchor whose
# membership may be tested part by part. Restricted to a LETTER initial on purpose: a
# decimal quantity like "2.1M" is also punctuation-joined, and splitting it
# would let a stray "1m" anywhere in the output vouch for it.
_PART_SPLIT = re.compile(r"[.\-/&+]")
_SPLITTABLE = re.compile(r"^[a-z][a-z0-9]*(?:[.\-/&+][a-z0-9]+)*$")
_ALNUM_RUN = re.compile(r"[A-Za-z0-9]+")
_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")
# 43%  $2.4M  8,000+  99.9%  3x  120ms
_QUANTITY = re.compile(r"(?<![\w.])(?:[$€£]\s?)?\d[\d,.]*\s?(?:%|[KMB]\b|x\b|ms\b|\+)?")

# LaTeX control sequences and their arguments' delimiters are formatting, not
# content. Stripping them stops \textbf and \begin{itemize} counting as anchors
# present on one side and absent on the other.
_TEX_COMMAND = re.compile(r"\\[a-zA-Z@]+\*?")
_TEX_BRACES = re.compile(r"[{}$&~^_\\]")

# Commands whose ARGUMENTS are structure rather than content, so the argument
# has to go with the command. Once anchors stopped requiring a capital initial,
# "itemize", "geometry" and "tabular" became anchors like any other word, and a
# re-render with a different preamble would have scored them as content lost.
_TEX_STRUCTURAL = re.compile(
    r"\\(?:begin|end|documentclass|usepackage|newcommand|renewcommand"
    r"|providecommand|newenvironment|newcolumntype|usetikzlibrary"
    r"|pagestyle|thispagestyle|geometry|hypersetup|definecolor"
    r"|setlength|addtolength|setcounter|titleformat|titlespacing|titlerule"
    r"|fontfamily|selectfont|input|include|bibliographystyle|label|ref|cite)"
    r"\*?(?:\s*(?:\[[^\]\n]*\]|\{[^{}\n]*\}))*"
)

# An escaped literal is CONTENT that merely needs escaping: AT\&T, 43\%,
# \$2.4M, snake\_case. _TEX_BRACES deletes & $ _ because LaTeX uses them for
# alignment, math mode and subscripts, and cannot tell those apart from the
# escaped ones — so escaped literals are parked under a sentinel and restored
# afterwards. Without this, correctly escaping "AT&T" read as losing it.
_ESCAPED_LITERAL = re.compile(r"\\([&$_#%])")
_SENTINEL = {"&": "\x01", "$": "\x02", "_": "\x03", "#": "\x04", "%": "\x05"}
_RESTORE = re.compile("[\x01-\x05]")
_FROM_SENTINEL = {v: k for k, v in _SENTINEL.items()}


def _strip_latex(text: str) -> str:
    text = re.sub(r"(?<!\\)%.*", " ", text)          # comments
    text = _ESCAPED_LITERAL.sub(lambda m: _SENTINEL[m.group(1)], text)
    text = _TEX_STRUCTURAL.sub(" ", text)
    text = _TEX_COMMAND.sub(" ", text)
    text = _TEX_BRACES.sub(" ", text)
    return _RESTORE.sub(lambda m: _FROM_SENTINEL[m.group(0)], text)


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
    for match in _ANCHOR_WORD.finditer(text):
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
    """folded token -> the casing it appears with in the source.

    Comparison has to fold case, because a renderer may legitimately change
    capitalisation in a heading. Reporting should not: "Missing: Arlington,
    Purrrfect" is actionable, "missing: arlington, purrrfect" looks like a bug
    in the tool rather than a problem with the document.

    A capitalised occurrence wins over a lowercase one, because a token that
    appears both ways is a name the source happened to lowercase somewhere.
    """
    forms: dict[str, str] = {}
    for match in _ANCHOR_WORD.finditer(text or ""):
        token = match.group(0)
        folded = token.lower()
        if folded not in forms or (token[:1].isupper()
                                   and not forms[folded][:1].isupper()):
            forms[folded] = token
    return forms


def _word_set(text: str, *, is_latex: bool = False) -> set[str]:
    """Every maximal alphanumeric run in the text, case-folded.

    Membership in this set is exactly a case-insensitive word-boundary match for
    any purely alphanumeric needle, and costs a dict lookup instead of a fresh
    regex per anchor per section.
    """
    if not text:
        return set()
    if is_latex:
        text = _strip_latex(text)
    return {m.group(0).lower() for m in _ALNUM_RUN.finditer(text)}


def _required_parts(anchor: str) -> list[str]:
    """The pieces of a punctuation-joined anchor that the output must contain.

    "typescript/javascript" -> ["typescript", "javascript"], so the same two
    languages written "TypeScript and JavaScript" satisfy it. Every part is
    required: half a compound is still loss.

    Parts that are stopwords or single characters are dropped — they would
    vouch for the anchor on their own. "at&t" splits to a stopword and a single
    letter, leaving nothing, so it falls back to the whole token and can only be
    satisfied by an exact tokenisation match.
    """
    parts = [p for p in _PART_SPLIT.split(anchor) if p]
    keep = [p for p in parts if len(p) >= 2 and p not in _STOPWORDS]
    return keep or [anchor]


def _survives(anchor: str, output_words: set[str]) -> bool:
    """Is this anchor's content present in the output, split or joined?

    Only for letter-initial anchors. A quantity carries punctuation that
    ``_strip_latex`` destroys ("$2.4M" -> " 2.4M", "43\\%" -> "43 %"), so it is
    left to the tokeniser on both sides, which normalises both the same way.
    """
    if not _SPLITTABLE.match(anchor):
        return False
    return all(part in output_words for part in _required_parts(anchor))


def _by_identity(missing: list[str], forms: dict[str, str]) -> list[str]:
    """Order missing tokens so the ones a person can act on come first.

    The list a message shows is truncated, and alphabetical order fills it with
    quantities — "Missing: $0.006, 10+, 10,700+, 10ms, 149" — which tells nobody
    anything. Measured against the real 2026-09-28 degradation, that is exactly
    what the first twelve were. "Missing: Arlington, Clover, Purrrfect" says
    what to check.

    So: tokens the source capitalises first, then the rest, alphabetical within
    each group. This changes the ORDER of a message, never the count, never the
    decision.
    """
    return sorted(missing,
                  key=lambda t: (not forms.get(t, t)[:1].isupper(), t))


def anchor_recall(source: str, output: str, *, output_is_latex: bool = True
                  ) -> tuple[float, list[str]]:
    """Fraction of the source's anchors present in the output, and what is missing.

    Returns (1.0, []) for an empty source: nothing was asked for, so nothing was
    lost. A caller deciding whether to overwrite must check the source is
    non-empty separately — this function will not invent a failure.

    An anchor counts as present two ways, and needs only one of them:

      * the output's own anchor set holds it — the same tokeniser on both sides,
        which is what normalises "43\\%" and "$2.4M" consistently;
      * every punctuation-part of it appears as a word in the output. This is
        what makes the set closed under splitting, so "TypeScript/JavaScript"
        and "TypeScript and JavaScript" stop scoring as total loss.

    Both are asymmetric on purpose: this measures whether the SOURCE's content
    survived, not whether the two documents are the same document.
    """
    wanted = extract_anchors(source)
    if not wanted:
        return 1.0, []
    have = extract_anchors(output, is_latex=output_is_latex)
    words = _word_set(output, is_latex=output_is_latex)
    missing = sorted(t for t in wanted
                     if t not in have and not _survives(t, words))
    return (len(wanted) - len(missing)) / len(wanted), missing


def describe_loss(source: str, output: str, *, limit: int = 25,
                  output_is_latex: bool = True) -> str:
    """A one-line report naming what went missing, for a user-facing message.

    Names the tokens rather than quoting a percentage: "Arlington, Purrrfect,
    UTWorld" tells someone what to check; "recall 0.62" does not. Ordered by
    _by_identity so the truncated list holds the names and not the numbers.
    """
    recall, missing = anchor_recall(source, output, output_is_latex=output_is_latex)
    if not missing:
        return f"All {len(extract_anchors(source))} anchors preserved."
    forms = _display_forms(source)
    ordered = _by_identity(missing, forms)
    shown = ", ".join(forms.get(token, token) for token in ordered[:limit])
    more = f" (+{len(missing) - limit} more)" if len(missing) > limit else ""
    return f"{recall:.0%} of content preserved. Missing: {shown}{more}"


def section_recall(source: str, output: str, *, output_is_latex: bool = True
                   ) -> dict[str, tuple[float, list[str]]]:
    """Anchor recall per source section: ``{section: (recall, missing)}``.

    A global threshold cannot see entry-scale loss. Re-measured 2026-09-30 over
    27 real résumés, deleting one employer leaves whole-document recall at 0.914
    in the median case — above DOC_RECALL_FLOOR — because that employer's tokens
    are a small share of a few hundred anchors. The section it was deleted from
    reads 0.739. Inside one section the same deletion is unmissable.

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


# Chosen from measurement, not taste — and RE-MEASURED on 2026-09-30 after the
# two extractor fixes above, because both floors had been derived with the
# biased instrument. The first derivation used ONE faithful pair (97.8% doc,
# 95.8% worst section); one document cannot tell a floor from a coincidence, so
# this one uses 27.
#
# Population: 27 real (source, output) pairs where the source is the pdfplumber
# text of a compiled PDF and the output is the exact .tex it was compiled from —
# the same shapes conversion_is_faithful is handed on the upload path. Two repo
# résumés, the production corpus row, and 24 tailored .tex read out of S3.
# Damage is applied to the output, never the source.
#
#                                  doc min  doc med   worst section min / med
#     faithful                       0.990    0.996        0.972    0.990
#     every bullet verb reworded     0.990    0.996        0.972    0.990
#     verbs AND nouns reworded       0.959    0.974        0.918    0.946
#     one employer deleted           0.672    0.914        0.392    0.739
#     education deleted              0.879    0.922        0.308    0.368
#     projects deleted               0.630    0.763        0.217    0.356
#     gutted to the summary          0.104    0.187        0.033    0.091
#
# The second and third rows are the false-positive measurement CLAUDE.md rule 16
# asks for: every capitalised name, digit and macro left byte-identical and only
# prose replaced by synonyms, so any recall drop there is the instrument
# punishing a conversion that lost nothing. Verb rewording now costs exactly
# nothing (it cost 6 points before — worst section 0.909). Rewording nouns as
# well, which is heavier than a re-render has any reason to be, still clears
# both floors.
#
# Neither floor moves. They were re-derived, not retained by default:
#
#   * 0.90 still sits below every faithful and every reworded reading (min
#     0.959) and above every section-scale loss at the median. Raising it to
#     0.93 or 0.95 catches nothing more on this population — the section floor
#     is already firing on all of it — so it would spend margin for nothing.
#   * 0.88 still sits between the worst faithful section (0.972, was 0.939) and
#     the damaged ones. 0.90 is also clean; 0.92 starts blocking a faithful
#     pair. The margin GREW with the fix, on both sides, which is the point:
#     the same floor now means what its docstring says.
#
# Gate outcome on the same 108 damaged documents: 105 blocked, up from 102. The
# three that pass are all the 8,121-byte production corpus row, where the
# section being deleted was 81, 84 and 204 bytes — 1.0% to 2.5% of the document.
# Nothing was lost that a floor should have caught.
DOC_RECALL_FLOOR = 0.90

# Per section the bar is higher, because at ingest there is no composition
# happening — this is a corpus and it is supposed to keep everything. A global
# threshold cannot see entry-scale loss: deleting one employer leaves the
# whole-document number at 0.914 in the median case, comfortably above 0.90,
# while the section it was deleted from reads 0.739. Per section, faithful:
#
#     summary 1.000   skills 0.990   experience 0.979
#     projects 0.972   education 1.000   certifications 1.000
#
# (minimums over the 27 pairs; medians are 1.000 for all but skills at 0.992).
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
        named = ", ".join(forms.get(t, t) for t in _by_identity(missing, forms)[:12])
        return False, (f"only {doc:.0%} of the source survived conversion "
                       f"(floor {DOC_RECALL_FLOOR:.0%}). Missing: {named}")

    for name, (recall, gone) in section_recall(
            source, output, output_is_latex=output_is_latex).items():
        if recall < SECTION_FLOOR:
            forms = _display_forms(source)
            named = ", ".join(forms.get(t, t) for t in _by_identity(gone, forms)[:12])
            return False, (f"the {name} section only {recall:.0%} survived "
                           f"(floor {SECTION_FLOOR:.0%}). Missing: {named}")

    return True, f"{doc:.0%} of anchors preserved, every section above the floor"
