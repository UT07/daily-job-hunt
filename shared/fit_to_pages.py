"""Make a resume fit its page budget, deterministically.

`compile_latex` has measured the compiled PDF since shared/page_check landed,
and on a violation it logged and uploaded anyway:

    page_violations = check_pdf(pdf_path, expected_pages=expected_pages)
    log_violations(page_violations, label=...)
    ... s3.put_object(...)

The same shape as the composition check before 2026-09-30 — the instrument was
right and nothing was wired to it. Measured on the live corpus, composed to the
user's 3+3 policy and compiled with tectonic:

    full content, margins 0.60/0.70in   4 pages  [4040, 4178, 3958, 80]
    full content, margins 0.50/0.60in   3 pages  [4264, 4481, 3511]
    3 bullets/entry, 6 skills, tight    3 pages  [4075, 3996, 147]
    3 bullets/entry, 5 skills, tight    2 pages  [4176, 3830]

Two things follow. A two-page resume is not reachable from this corpus by
layout alone, so content must go; and the last 147 characters cost a whole
skills row, so the order the levers are pulled in decides what the candidate
loses. Hence a fixed order, cheapest first:

    1. margins          loses nothing, 4 pages -> 3
    2. entry bullets    the N+1th bullet of each role, oldest roles first
    3. skills items     last, because they are ATS keyword matches

and each step is followed by a real compile. Guessing constants would be
quicker and would also be wrong for the next corpus: this returns a document
MEASURED to fit, or says it could not and leaves the document alone.
"""
from __future__ import annotations

import re
from typing import Any, Callable

from shared.composition_policy import resolve

_ENTRY = re.compile(r"(?<!\{)\\(?:jobentry|projectentry(?:url)?)(?![a-zA-Z])")
_BLOCK = re.compile(r"\\begin\{itemize\}(.*?)\\end\{itemize\}", re.DOTALL)
_GEOMETRY = re.compile(r"top=[\d.]+in,bottom=[\d.]+in,left=[\d.]+in,right=[\d.]+in")

TIGHT_MARGINS = "top=0.50in,bottom=0.50in,left=0.60in,right=0.60in"

# Floors. Below these the document stops being a resume: an entry with fewer
# than three bullets reads as filler, and a skills section of three lines
# throws away the keyword matches an ATS scores on.
MIN_BULLETS = 3
MIN_SKILLS = 5


def _itemize_blocks(tex: str) -> list[dict]:
    """Every itemize block, tagged with whether it belongs to an entry."""
    starts = [m.start() for m in _ENTRY.finditer(tex)]
    blocks = []
    for i, m in enumerate(_BLOCK.finditer(tex)):
        before = [s for s in starts if s < m.start()]
        between = tex[before[-1]:m.start()] if before else "\\section"
        is_entry = bool(before) and "\\section" not in between and "\\end{itemize}" not in between
        items = re.split(r"(?=\\item)", m.group(1))
        real = [x for x in items if x.lstrip().startswith("\\item")]
        blocks.append({"span": (m.start(1), m.end(1)),
                       "head": items[0] if not items[0].lstrip().startswith("\\item") else "",
                       "items": real, "is_entry": is_entry, "index": i})
    return blocks


def tighten_margins(tex: str) -> tuple[str, bool]:
    """The one lever that costs no content. Returns (tex, changed)."""
    if TIGHT_MARGINS in tex:
        return tex, False
    out, n = _GEOMETRY.subn(TIGHT_MARGINS, tex, count=1)
    return out, bool(n)


def cap_items(tex: str, *, entry_max: int | None = None,
              skills_max: int | None = None) -> tuple[str, int]:
    """Trim itemize blocks to a maximum. Returns (tex, items removed).

    `skills_max` applies to the FIRST non-entry block only — the Technical
    Skills list. Education's coursework and the Certifications list are left
    alone: they are short, and certifications are a claim the candidate holds,
    not padding.
    """
    blocks = _itemize_blocks(tex)
    first_non_entry = next((b["index"] for b in blocks if not b["is_entry"]), None)
    pieces, pos, removed = [], 0, 0
    for b in blocks:
        cap = entry_max if b["is_entry"] else (
            skills_max if b["index"] == first_non_entry else None)
        if cap is None or len(b["items"]) <= cap:
            continue
        removed += len(b["items"]) - cap
        start, end = b["span"]
        pieces.append(tex[pos:start])
        pieces.append(b["head"] + "".join(b["items"][:cap]))
        pos = end
    pieces.append(tex[pos:])
    return "".join(pieces), removed


_LIST_SPACING = re.compile(r"itemsep=[\d.]+pt,\s*topsep=[\d.]+pt")
_SECTION_SPACING = re.compile(
    r"(\\titlespacing\*\{\\section\}\{0pt\}\{)[\d.]+em\}\{[\d.]+em\}")


def tighten_list_spacing(tex: str) -> tuple[str, bool]:
    r"""Close up the gaps between bullets. Costs the candidate nothing.

    The template ships `itemsep=1.8pt, topsep=2.5pt`. Across ~11 lists and
    ~29 items that is several lines of pure whitespace, and whitespace is the
    one thing a résumé can lose for free.
    """
    # Compared as TEXT, not on subn's match count. The pattern matches its own
    # output -- `itemsep=0pt, topsep=1pt` is itself `itemsep=<num>pt,
    # topsep=<num>pt` -- so on a second application subn reports a match while
    # producing an identical document, and the flag would be claiming a change
    # that did not happen. `fit` is not actually fooled by that: its loop skips
    # a step when `nxt == current` regardless of what the step says. The flag
    # is corrected anyway, because a return value that is wrong only where the
    # one current caller happens not to look is a trap for the next one.
    out, _ = _LIST_SPACING.subn("itemsep=0pt, topsep=1pt", tex, count=1)
    return out, out != tex


def tighten_section_spacing(tex: str) -> tuple[str, bool]:
    r"""Close up the space above and below section headings. Also free."""
    # Text comparison, for the reason given in tighten_list_spacing.
    out, _ = _SECTION_SPACING.subn(r"\g<1>0.35em}{0.20em}", tex, count=1)
    return out, out != tex


def reduction_plan(tex: str, policy: dict[str, Any] | None = None) -> list[tuple[str, Callable]]:
    """The levers, cheapest first. Each returns (tex, description or None).

    "Cheapest" means cheapest TO THE CANDIDATE, and that ordering was wrong
    until 2026-10-08: bullets were spent down to the floor before any of the
    free typographic levers were tried. Measured on the three documents that
    still overran after every lever had been pulled -- page 3 holding 39, 147
    and 383 characters, a spillover rather than a page -- closing up list
    spacing alone brought two of them to exactly two pages:

        7d7533eaa322   [4151, 3831,  39]  ->  [4337, 3684]
        ad871c2c1134   [4048, 3868, 147]  ->  [4173, 3890]
        9be139f8581f   [3913, 4124, 383]  ->  [4196, 4077, 147]

    The consequence is larger than those three. Because whitespace is now
    spent BEFORE content, a document that used to need its bullets cut to 3 to
    fit may now fit at 6 or 7 -- so this removes content from fewer résumés,
    not more. Documents already within budget are untouched either way: `fit`
    returns before the first lever when `pages <= budget`.
    """
    p = resolve(policy)
    bullet_start = p["bullets_per_entry"]["max"]

    def margins(t):
        out, changed = tighten_margins(t)
        return out, ("tightened margins to 0.5/0.6in" if changed else None)

    def list_spacing(t):
        out, changed = tighten_list_spacing(t)
        return out, ("closed up list spacing" if changed else None)

    def section_spacing(t):
        out, changed = tighten_section_spacing(t)
        return out, ("closed up section spacing" if changed else None)

    # Free levers first, content last. Each of the three below takes nothing
    # away from the candidate; everything after them does.
    steps: list[tuple[str, Callable]] = [
        ("margins", margins),
        ("list-spacing", list_spacing),
        ("section-spacing", section_spacing),
    ]

    for n in range(bullet_start - 1, MIN_BULLETS - 1, -1):
        def bullets(t, n=n):
            out, removed = cap_items(t, entry_max=n)
            return out, (f"capped entries at {n} bullets (-{removed})" if removed else None)
        steps.append((f"bullets<={n}", bullets))

    for n in (7, 6, MIN_SKILLS):
        def skills(t, n=n):
            out, removed = cap_items(t, skills_max=n)
            return out, (f"capped skills at {n} items (-{removed})" if removed else None)
        steps.append((f"skills<={n}", skills))

    return steps


def fit(tex: str, measure: Callable[[str], int], policy: dict[str, Any] | None = None,
        ) -> tuple[str, list[str], bool]:
    """Reduce until the compiled document fits. Returns (tex, actions, fits).

    `measure` compiles and returns a page count — injected so this stays a pure
    decision procedure and so the caller owns the (slow) compile. A step that
    changes nothing costs no compile.

    Returns the ORIGINAL document when no reduction reaches the budget. A
    half-trimmed resume that still overflows is strictly worse than the
    untrimmed one: the same page count, less of the candidate on it.
    """
    budget = resolve(policy)["pages"]
    if not isinstance(budget, int) or budget < 1:
        return tex, [], True

    pages = measure(tex)
    if pages <= budget:
        return tex, [], True

    actions, current = [], tex
    for _name, step in reduction_plan(tex, policy):
        nxt, described = step(current)
        if described is None or nxt == current:
            continue
        current = nxt
        actions.append(described)
        pages = measure(current)
        if pages <= budget:
            return current, actions, True

    return tex, [], False


# ---------------------------------------------------------------------------
# Separators
#
# Two defects visible in the compiled PDF, both reported 2026-10-05.
#
# The corpus defines
#
#     \newcommand{\jobentry}[4]{... \textbf{#1} -- #2 \hfill \textit{#3} ...}
#
# and calls it as `\jobentry{Yuno Energy}{}{Jun 2026 – Present}{...}`. #2 is
# the LOCATION and it is empty for every entry, so each role renders as
# "Yuno Energy --" with a dash attached to nothing. That is the "dash in the
# wrong place": it is not the date separator at all, it is a separator for an
# argument that was never supplied.
#
# And the date separator is U+2013 (e2 80 93). The user asked for a hyphen, and
# a hyphen is also the safer character: an en-dash depends on the font and the
# input encoding surviving every hop between here and the PDF, and a resume is
# read by parsers that do not all agree about U+2013.
# ---------------------------------------------------------------------------

# Only between two date-ish tokens, so an en-dash inside a project NAME --
# "NaukriBaba – AI Job-Automation Platform" -- keeps its typography. Replacing
# every dash in the document would be the easy version and would rewrite the
# candidate's own titles.
_DATE_SEP = re.compile(
    r"((?:[A-Z][a-z]{2,8}\.?\s+)?\d{4})\s*[‐-―]\s*"
    r"((?:[A-Z][a-z]{2,8}\.?\s+)?\d{4}|Present|Current|Now)"
)

_JOBENTRY_DEF = re.compile(
    r"(\\newcommand\{\\jobentry\}\[4\]\{)(.*?)(\n\})", re.DOTALL)


def normalise_date_separators(tex: str) -> tuple[str, int]:
    """En/em dashes BETWEEN DATES become hyphens. Returns (tex, count)."""
    out, n = _DATE_SEP.subn(r"\1 - \2", tex)
    return out, n


def fix_empty_location_separator(tex: str) -> tuple[str, bool]:
    """Make `\\jobentry`'s location separator conditional on a location.

    `\\ifx\\relax#2\\relax` is the standard empty-argument test: it compares the
    argument, wrapped in \\relax on both sides, against a bare \\relax pair. An
    empty #2 makes the two sides identical.
    """
    m = _JOBENTRY_DEF.search(tex)
    if not m or "\\ifx\\relax#2\\relax" in m.group(2):
        return tex, False
    body = m.group(2)
    fixed = body.replace("\\textbf{#1} -- #2",
                         "\\textbf{#1}\\ifx\\relax#2\\relax\\else{} -- #2\\fi")
    if fixed == body:
        return tex, False
    return tex[:m.start(2)] + fixed + tex[m.end(2):], True


# `\clearpage` and friends, as emitted by the model into the resume BODY.
# Matched with a trailing boundary so `\newpagething` is left alone, and
# `\cleardoublepage` is included because it can insert a blank page outright.
_FORCED_BREAK = re.compile(
    r"^[ \t]*\\(?:clearpage|cleardoublepage|newpage|pagebreak)\b[ \t]*$\n?",
    re.MULTILINE,
)


def strip_forced_breaks(tex: str) -> tuple[str, int]:
    r"""Remove model-emitted hard page breaks. Returns (tex, how many).

    Measured 2026-10-07 over 272 live resumes: 14 of the 15 that overran the
    two-page budget carried one of these, and the fit loop could never recover
    any of them -- because it was pulling CONTENT levers against a LAYOUT
    defect. One document, after every lever had been pulled, compiled to
    per-page text lengths of

        [4010, 87, 3654]        <- page 2 holds 87 characters. It is blank.

    from a single `\clearpage` the model had emitted between Experience and
    Featured Projects. Deleting that one macro: [4010, 3741]. Two pages. No
    amount of trimming reaches that, which is exactly why the loop reported
    "could NOT fit after 9 attempt(s)" having genuinely changed the document
    nine times.

    Safe by construction, not merely by measurement: a forced break can only
    ever ADD a page boundary. Removing one lets LaTeX break where it would
    have anyway, so the page count can fall or stay and cannot rise. That
    matters because 73 of the 272 carried a break and still fit -- their break
    happened to land near a natural boundary -- and this must not disturb them.

    Done here rather than in the prompt on purpose. CLAUDE.md #4: a
    prompt-level constraint is a request; only a check is a guarantee. The
    prompt may ask as well, but this is what makes it true.

    Only whole lines are matched. An inline `\pagebreak` mid-paragraph is not
    something the templates produce, and a regex loose enough to catch it is
    loose enough to corrupt a line it did not understand.
    """
    out, n = _FORCED_BREAK.subn("", tex)
    return out, n


# A LaTeX line-break length, e.g. the `[0.08em]` in `\\[0.08em]`.
_SPACING_ARG = re.compile(r"\[\d*\.?\d+em\]")


def strip_orphan_spacing(tex: str) -> tuple[str, int]:
    r"""Remove a `[0.08em]` that is text rather than an argument.

    `\\[0.08em]` is a line break with extra leading. The length in brackets is
    an OPTIONAL ARGUMENT to `\\` and is only that when a `\\` precedes it; on
    its own it is ordinary prose and LaTeX sets it as "[0.08em]" in the
    document.

    The model rewrites the header subtitle on roughly half of all résumés, and
    on 2 of 120 sampled it emitted the spacing twice -- once correctly, once as
    text on the next line:

        ...Kubernetes, AWS, Terraform, CI/CD}\\[0.08em]
        [0.08em] Dublin, Ireland | +353 ... | 254utkarsh@gmail.com

    which prints a literal "[0.08em]" immediately above the candidate's phone
    number, on the most-read line of the document.

    Decided by looking BACKWARDS from each match rather than by anchoring to
    the line start, because `\\` and its argument may legitimately be split
    across a newline -- whitespace is allowed between them -- so "at the start
    of a line" would delete valid markup. Preceded by `\\` (ignoring
    whitespace) it is an argument and is kept; otherwise it is prose and goes.
    """
    out, removed = [], 0
    pos = 0
    for m in _SPACING_ARG.finditer(tex):
        before = tex[:m.start()].rstrip()
        if before.endswith("\\\\"):
            continue                      # a real optional argument
        out.append(tex[pos:m.start()])
        pos = m.end()
        # swallow one following space so "[0.08em] Dublin" does not become
        # " Dublin" with a leading gap
        if tex[pos:pos + 1] == " ":
            pos += 1
        removed += 1
    if not removed:
        return tex, 0
    out.append(tex[pos:])
    return "".join(out), removed


def normalise_separators(tex: str) -> tuple[str, list[str]]:
    """The pre-compile fixes that are never a trade-off. Returns (tex, actions).

    Everything here is a correction, not a reduction: each one either fixes
    something that renders wrong or removes something that can only hurt. The
    levers that COST the candidate something -- bullets, skills, margins --
    live in `reduction_plan` and are pulled only when the document overruns.
    """
    actions = []
    tex, dashes = normalise_date_separators(tex)
    if dashes:
        actions.append(f"date separator -> hyphen in {dashes} place(s)")
    tex, fixed = fix_empty_location_separator(tex)
    if fixed:
        actions.append("suppressed the location separator when no location is given")
    tex, breaks = strip_forced_breaks(tex)
    if breaks:
        actions.append(f"removed {breaks} forced page break(s)")
    tex, orphans = strip_orphan_spacing(tex)
    if orphans:
        actions.append(f"removed {orphans} stray line-break length(s) printing as text")
    return tex, actions
