"""How a resume is composed from the corpus — as the user's settings, enforced.

The master is a corpus: everything about the candidate, three pages or ten. A
resume is composed from it for one job, and these are the rules for that
composition. They belong to the user.

Today they are string literals in the tailoring prompt:

    tailor_resume.py:250  "Projects: EXACTLY 3 PROJECTS. No more, no less."
    tailor_resume.py:251  'ALWAYS KEEP BOTH "Purrrfect Keys" AND "NaukriBaba"'
    tailor_resume.py:268  "The resume MUST be exactly TWO PAGES."
    tailor_resume.py:269  "Page 1: ... Clover IT Services (7 bullets), and
                           Seattle Kraken (3 bullets)."

Two problems. Those are one person's project names compiled into the prompt, so
the system is single-tenant by construction. And nothing enforces any of it:
`_validate_macro_arities` checks that each `\\jobentry` call has four arguments,
and nothing anywhere counts how many `\\jobentry` calls there are. Every one of
those lines is a request.

So this module splits the rules by whether they can be measured.

COUNTABLE — the model is told, and the output is counted:

    max_experience_entries, max_projects, bullets_per_entry

JUDGEMENT — prompt text only, because there is nothing to count:

    rename, writing, emphasise

EXECUTABLE — told to the model, and applied deterministically if it does not
comply:

    prefer, when written structurally

`prefer` used to sit in JUDGEMENT, on the reasoning that "choose the more
relevant of these two roles" cannot be executed. Two of its three real uses
turned out not to need relevance at all: "never include the teaching assistant
role" and "include UT Arlington IT rather than Seattle Kraken" are set
operations over entry names. Written as {"exclude": ...} or
{"include": ..., "over": ...} they are rendered into the prompt AND applied by
`compose_from_corpus` when the output ignores them. A prose rule still works
and stays prompt-only, which is the honest boundary: prose that needs a
judgement is left to the thing that can judge.

`writing` and `emphasise` are judgement by necessity, not by choice. "Every
bullet states an outcome and a number" is checkable in principle -- count the
digits -- but a count cannot tell a real metric from an invented one, and the
cure for a resume that reads as a tool inventory must not be a resume that
invents percentages. So they are stated and not enforced, and the fabrication
guard in lambdas/pipeline/guardrails/output_guards.py remains the thing that
stops a number the base resume never contained. Named here so the split stays
honest: these two are requests, and this file's own docstring says what that
means.

Page count is a third case: countable, but only against a compiled PDF, not
against LaTeX source. It is carried here and checked by whoever holds the PDF;
`check_output` deliberately does not pretend to know it from the .tex.

The split is the point. "EXACTLY 3 PROJECTS" with nothing counting is the same
mistake as every other prompt-level constraint this project has been burned by.
"""
from __future__ import annotations

import re
from typing import Any

# Defaults. max_projects is 3 on the user's instruction of 2026-09-29 — four
# was judged too many for a two-page document.
#
# max_experience_entries is 4 on the user's instruction of 2026-09-30: "job
# entry should be like 3 or 4 at max. Yuno, Clover, UTA IT, Seattle Kraken
# that's all". It was 2, which is why the UT Arlington IT role was missing from
# every generated resume -- the cap silently dropped it, and the corpus row
# production reads holds only Yuno and Clover anyway. Raising this is necessary
# but not sufficient: a corpus with two employers cannot produce four entries,
# so the uploaded master has to carry them.
DEFAULTS: dict[str, Any] = {
    "max_experience_entries": 4,
    "max_projects": 3,
    "pages": 2,
    "bullets_per_entry": {"min": 3, "max": 7},
    "prefer": [],
    "rename": [],
    # Writing quality. In DEFAULTS rather than the user row because it is not
    # user-specific -- every resume is better for quantified impact than for a
    # tool inventory -- and because nothing in the prompt said it. The user's
    # 2026-09-30 report: "the writing should be of very high quality with impact
    # measured rather than looking like a list of tools".
    "writing": [
        "FORMULA for every bullet: ACTION VERB + what you did and at what "
        "scale + QUANTIFIED RESULT. Yale Office of Career Strategy's "
        "formulation, and the order matters -- the verb first, the number "
        "last, so the line lands on its outcome.",
        "Open with a strong past-tense verb: Designed, Built, Cut, Migrated, "
        "Automated, Scaled, Led, Shipped, Reduced, Recovered. NEVER open with "
        "\"Responsible for\", \"Worked on\", \"Helped with\", "
        "\"Assisted in\", \"Duties included\", \"Involved in\" or "
        "\"Tasked with\" -- those describe a job description, not a person.",
        "Give the scope: the tools, and the SIZE. \"across 34 Lambda "
        "functions\", \"for 150 API endpoints\", \"a 12-person team\". A "
        "reader cannot judge an achievement whose scale is unstated.",
        "End on a measured result -- latency, cost, uptime, error rate, time "
        "saved, throughput, revenue, headcount. A close estimate beats no "
        "number; an INVENTED number is worse than either. Use only figures "
        "the base resume already supports, and where it gives none, state the "
        "outcome qualitatively rather than reaching for a percentage.",
        "The progression to aim for, from Yale's own example: "
        "\"Managed customer service calls\" (a task) -> "
        "\"Improved customer support by managing client calls\" (an action) "
        "-> \"Increased customer satisfaction 20% in three months by "
        "resolving client issues and streamlining call workflows\" (an "
        "action with a measured result). Write the third kind.",
        "Mirror the job description's own vocabulary where the resume "
        "genuinely supports it -- an ATS matches words, not synonyms. Never "
        "claim a tool the base resume does not evidence.",
    ],
    # Conditional emphasis, keyed on the target role like `rename`. Empty by
    # default: which axis to foreground depends on the roles a given candidate
    # is pursuing.
    "emphasise": [],
}

_COUNTABLE = ("max_experience_entries", "max_projects")

# Arity-aware so the match cannot be fooled by a macro DEFINITION
# (\newcommand{\jobentry}[4]{...}) or by \jobentryfoo.
_MACROS = {
    "experience": re.compile(r"(?<!\{)\\jobentry(?![a-zA-Z])"),
    "projects": re.compile(r"(?<!\{)\\projectentry(?:url)?(?![a-zA-Z])"),
}


def resolve(stored: dict | None) -> dict[str, Any]:
    """Merge a stored policy over the defaults.

    Shallow by design except for bullets_per_entry: a user who sets only
    ``{"max_projects": 2}`` keeps every other default rather than losing them.
    """
    policy = dict(DEFAULTS)
    if not isinstance(stored, dict):
        return policy
    for key, value in stored.items():
        if key == "bullets_per_entry" and isinstance(value, dict):
            merged = dict(DEFAULTS["bullets_per_entry"])
            merged.update(value)
            policy[key] = merged
        elif key in DEFAULTS:
            policy[key] = value
    return policy


def render_for_prompt(policy: dict[str, Any]) -> str:
    """The policy as instructions, with no candidate's names baked in.

    Everything here is derived from the user's own settings, so a second user
    gets their own rules rather than the first user's project list.
    """
    p = resolve(policy)
    bullets = p["bullets_per_entry"]
    lines = [
        "COMPOSITION RULES (from this candidate's profile):",
        f"- Include AT MOST {p['max_experience_entries']} experience entries. "
        "Choose the ones most relevant to this job.",
        f"- Include AT MOST {p['max_projects']} projects. Choose the ones most "
        "relevant to this job.",
        f"- Each entry carries between {bullets['min']} and {bullets['max']} bullets.",
        f"- The finished resume must be exactly {p['pages']} pages.",
        "",
        "These are limits, not targets: fewer, stronger entries beat padding to "
        "the cap. Selecting from the candidate's full history is the job — do "
        "not include everything.",
        "",
        # There was no ordering rule at all, and "choose the ones most relevant"
        # invites a relevance sort. ATS parsers read employment history as a
        # chronology and infer seniority from position, so a more relevant older
        # role placed first reads as the candidate's current job.
        "ORDER: keep experience entries in the SAME ORDER as the base resume — "
        "most recent first. Relevance decides WHICH entries to include, never "
        "where they sit. Do not move an older role above a newer one because it "
        "matches the job description better.",
    ]
    if p["prefer"]:
        lines.append("")
        lines.append("PREFERENCES when choosing between entries:")
        # A structured rule is rendered to prose here so it reaches the model as
        # an instruction AND stays executable by compose_from_corpus. One
        # definition, both consumers -- the alternative is a prose rule and a
        # machine rule that drift apart, which is CLAUDE.md rule 10's failure.
        for rule in p["prefer"]:
            if not isinstance(rule, dict):
                lines.append(f"- {rule}")
                continue
            why = f" — {rule['why']}" if rule.get("why") else ""
            if rule.get("exclude"):
                lines.append(f"- NEVER include \"{rule['exclude']}\"{why}.")
            elif rule.get("include") and rule.get("over"):
                lines.append(f"- Include \"{rule['include']}\" IN PREFERENCE TO "
                             f"\"{rule['over']}\"{why}. Never both: they compete "
                             "for the same slot.")
            elif rule.get("include"):
                lines.append(f"- Always include \"{rule['include']}\"{why}.")
    if p["writing"]:
        lines.append("")
        lines.append("WRITING QUALITY (this is the difference between a resume "
                     "that reads as achievements and one that reads as an "
                     "inventory):")
        lines += [f"- {rule}" for rule in p["writing"]]
    if p["emphasise"]:
        lines.append("")
        lines.append("EMPHASIS, when the target role matches:")
        for rule in p["emphasise"]:
            when = rule.get("when") if isinstance(rule, dict) else None
            what = rule.get("what") if isinstance(rule, dict) else str(rule)
            lines.append(f"- {what}" + (f" — when the target role involves {when}." if when else ""))
    if p["rename"]:
        lines.append("")
        lines.append("TITLE ADJUSTMENTS (same role, wording suited to the target):")
        for rule in p["rename"]:
            when = rule.get("when")
            scope = f" when the target role is {when}" if when else ""
            lines.append(f"- Write \"{rule.get('from')}\" as "
                         f"\"{rule.get('to')}\"{scope}.")
    return "\n".join(lines)


def count_entries(tex: str) -> dict[str, int]:
    """How many experience and project entries the generated LaTeX contains."""
    return {kind: len(pattern.findall(tex or "")) for kind, pattern in _MACROS.items()}


def check_output(tex: str, policy: dict[str, Any] | None = None) -> list[str]:
    """Violations of the countable rules. Empty list means it complies.

    Only over-inclusion is a violation. Fewer entries than the cap is a valid
    choice — the caps are limits, and a candidate with one job cannot produce
    two. Treating under-count as failure would make the rule impossible for
    exactly the people it should not constrain.

    Page count is absent on purpose: it cannot be known from LaTeX source, and
    a check that guesses is worse than one that abstains.
    """
    p = resolve(policy)
    counts = count_entries(tex)
    violations = []
    for kind, cap_key in (("experience", "max_experience_entries"),
                          ("projects", "max_projects")):
        cap = p[cap_key]
        if isinstance(cap, int) and counts[kind] > cap:
            noun = "experience entries" if kind == "experience" else "projects"
            violations.append(f"{counts[kind]} {noun}, limit is {cap}")
    # Preferences are part of "does this comply", not a separate question. They
    # were checked nowhere until 2026-09-30, and a resume that satisfies every
    # count while choosing the entries the user rejected passed silently.
    violations.extend(check_preferences(tex, p))
    return violations


def describe_violations(violations: list[str]) -> str:
    """A repair instruction naming the counts, so a retry has something to act on.

    "You emitted 5 projects, the limit is 3" is actionable. Repeating the
    original rule at a model that already ignored it is not.
    """
    if not violations:
        return ""
    # Two kinds of violation need two kinds of instruction. "Remove the least
    # relevant entries" is the wrong advice for a preference break, where the
    # fix is a SWAP: the rejected entry out, the preferred one in. Telling the
    # model to delete would produce a shorter resume that is still wrong.
    swaps = [v for v in violations if "the policy" in v]
    counts = [v for v in violations if v not in swaps]
    parts = ["The document you produced breaks the composition rules: "
             + "; ".join(violations) + "."]
    if counts:
        parts.append("Remove the least relevant entries until it complies. "
                     "Delete the whole entry including its itemize block — do "
                     "not leave an empty shell.")
    if swaps:
        parts.append("For the preference rules, SWAP rather than delete: take "
                     "out the entry the policy rejects and put in the one it "
                     "prefers, copied from the base resume verbatim.")
    return " ".join(parts)


# --------------------------------------------------------------------------
# Deterministic composition, for when no model output survives.
#
# `tailor_resume` falls back to the base resume whenever a hard gate fails, and
# until 2026-09-30 it shipped the corpus verbatim — 5 experience entries and 5
# projects against caps of 3 and 3 — while logging that it did. The log said
# "does not meet the composition rules" and nothing acted on it: CLAUDE.md rule
# 13, a check whose severity is not wired to behaviour.
#
# Trimming a corpus to the caps needs no model. The entries are already there,
# already ordered, already written; the only question is which to keep. That is
# arithmetic over the user's own rules, so it happens here and it always
# happens, whether the council produced a document or died trying.
# --------------------------------------------------------------------------

# Where an entry's block ends: the next entry, the next section, a page break,
# or the end of the document. Scanning to the next boundary swallows the
# entry's own \begin{itemize}...\end{itemize} without having to parse it, so a
# removed entry never leaves an orphaned bullet list behind.
_BOUNDARY = re.compile(
    r"(?<!\{)\\(?:jobentry|projectentry(?:url)?|section|subsection|clearpage"
    r"|newpage|end\{document\})(?![a-zA-Z])"
)


def _read_group(tex: str, i: int) -> tuple[str, int]:
    """Read the balanced ``{...}`` starting at ``tex[i] == '{'``.

    Returns (inner text, index just past the closing brace). Escaped braces
    (``\\{``) are not counted; a LaTeX resume that puts one inside an entry
    NAME would defeat this, and none of the six real corpus entries does.
    """
    if i >= len(tex) or tex[i] != "{":
        raise ValueError("not a group")
    depth = 0
    for j in range(i, len(tex)):
        if tex[j] == "{" and (j == 0 or tex[j - 1] != "\\"):
            depth += 1
        elif tex[j] == "}" and tex[j - 1] != "\\":
            depth -= 1
            if depth == 0:
                return tex[i + 1:j], j + 1
    raise ValueError("unbalanced braces")


def _normalise_name(text: str) -> str:
    """An entry name reduced to something a policy needle can match.

    The corpus writes "Dept. of Computer Science \\& Engineering" and
    "NaukriBaba – AI Job-Automation Platform"; a user writing a rule types
    "Dept. of Computer Science" and "NaukriBaba". Both sides go through this,
    so the comparison is substring-on-normalised rather than exact.
    """
    # No explicit \& -> & step: the character filter below already drops the
    # backslash, so "Science \& Engineering" and "Science & Engineering"
    # normalise identically. A replace() pass was written here first and
    # measured redundant -- a mutation removing it killed no test, because the
    # filter does the work. Named rather than deleted silently: the next reader
    # should not have to rediscover that the escape is handled.
    s = re.sub(r"\\[a-zA-Z]+\s*", " ", text)  # \textbf, \textit, \\ -- NOT redundant
    s = re.sub(r"[^a-z0-9&]+", " ", s.lower())
    return re.sub(r"\s+", " ", s).strip()


def _entry_blocks(tex: str, kind: str) -> list[dict]:
    """Every entry of ``kind`` as a removable span, in document order.

    Each item carries ``start``/``end`` character offsets, the entry's first
    argument as ``name``, and its normalised form as ``key``.
    """
    blocks = []
    for match in _MACROS[kind].finditer(tex or ""):
        start = match.start()
        pos = match.end()
        while pos < len(tex) and tex[pos] in " \t\n":
            pos += 1
        try:
            name, _ = _read_group(tex, pos)
        except ValueError:
            continue                          # malformed call: leave it alone
        following = _BOUNDARY.search(tex, match.end())
        end = following.start() if following else len(tex)
        blocks.append({"start": start, "end": end, "name": name,
                       "key": _normalise_name(name)})
    return blocks


def actionable_preferences(policy: dict[str, Any] | None = None) -> list[dict]:
    """The `prefer` rules that can be applied without a model.

    Two shapes, both optional, both also rendered into the prompt:

        {"exclude": "<entry name>", "why": "..."}
        {"include": "<entry name>", "over": "<entry name>", "why": "..."}

    A plain string stays prompt-only. That is the honest split: prose like
    "lead with whichever role the job resembles" cannot be executed here, and
    pretending to parse it would be worse than leaving it to the model.
    """
    rules = []
    for rule in resolve(policy)["prefer"]:
        if isinstance(rule, dict) and (rule.get("exclude") or rule.get("include")):
            rules.append(rule)
    return rules


def compose_from_corpus(tex: str,
                        policy: dict[str, Any] | None = None) -> tuple[str, list[str]]:
    """Cut a corpus down to one job's resume. Returns (tex, what it did).

    Selection applies the user's rules in a fixed order — exclusions, then
    preferences, then the caps — and emission keeps DOCUMENT order, because
    those are two different questions. Relevance and preference decide WHICH
    entries survive; chronology decides where they sit. Trimming from the end
    is what preserves the latter: the corpus is most-recent-first, so the cap
    drops the oldest survivor rather than reordering anything.

    An empty action list means the corpus already complied.
    """
    p = resolve(policy)
    rules = actionable_preferences(p)
    dropped, actions = [], []

    for kind, cap_key, noun in (("experience", "max_experience_entries", "experience entry"),
                                ("projects", "max_projects", "project")):
        keep = _entry_blocks(tex, kind)

        for rule in rules:
            needle = _normalise_name(rule.get("exclude") or "")
            if not needle:
                continue
            for block in [b for b in keep if needle in b["key"]]:
                keep.remove(block)
                dropped.append(block)
                why = rule.get("why") or "excluded by policy"
                actions.append(f"dropped {noun} \"{block['name']}\" — {why}")

        for rule in rules:
            wanted = _normalise_name(rule.get("include") or "")
            beaten = _normalise_name(rule.get("over") or "")
            if not wanted or not beaten:
                continue
            # Only honoured when the preferred entry actually survived. If it
            # was excluded or absent, dropping its rival too would leave the
            # resume short for no reason the user asked for.
            if not any(wanted in b["key"] for b in keep):
                continue
            for block in [b for b in keep if beaten in b["key"]]:
                keep.remove(block)
                dropped.append(block)
                why = rule.get("why") or "the other entry is preferred"
                actions.append(f"dropped {noun} \"{block['name']}\" — {why}")

        # Required entries survive the cap. Today the user's corpus lists Yuno
        # Energy first, so trimming from the end never reaches it -- but that
        # is the corpus's shape, not a guarantee, and a rule that holds only
        # while the data cooperates is not a rule. When the cap would cut into
        # a required entry, the oldest NON-required entry goes instead.
        required = [_normalise_name(r["include"]) for r in rules
                    if r.get("include") and not r.get("over")]
        cap_now = p[cap_key]
        if required and isinstance(cap_now, int) and len(keep) > cap_now:
            protected = [b for b in keep if any(n in b["key"] for n in required)]
            rest = [b for b in keep if b not in protected]
            room = max(cap_now - len(protected), 0)
            survivors = {id(b) for b in protected} | {id(b) for b in rest[:room]}
            # Both halves, or nothing is removed from the document: `keep`
            # decides what survives, `dropped` is what is actually cut out. The
            # first version of this block updated only `keep`, so the cap
            # silently stopped trimming whenever a required entry existed --
            # caught by test_the_cap_never_drops_a_required_entry asserting the
            # SURVIVING COUNT rather than only the required entry's presence.
            for block in [b for b in keep if id(b) not in survivors]:
                dropped.append(block)
                actions.append(f"dropped {noun} \"{block['name']}\" — over the "
                               f"limit of {cap_now}, and the oldest of those not "
                               f"required by the policy")
            keep = [b for b in keep if id(b) in survivors]

        cap = p[cap_key]
        if isinstance(cap, int) and len(keep) > cap:
            # Said out loud when the cap is the ONLY thing selecting, because
            # position is a weak reason and a prose `prefer` rule means the user
            # has a stronger one this function cannot read. On the live corpus
            # that difference decides Seattle Kraken (3rd) against the UT
            # Arlington IT role (4th): by position the wrong one survives. A
            # silently-wrong trim would look exactly like a working one.
            prose_only = [r for r in resolve(p)["prefer"] if not isinstance(r, dict)]
            if prose_only and not rules:
                actions.append(
                    f"selected {noun}s by position only: {len(prose_only)} "
                    "preference rule(s) are prose and cannot be applied without "
                    "a model — rewrite them as {\"exclude\": ...} or "
                    "{\"include\": ..., \"over\": ...} to have them enforced here"
                )
            for block in keep[cap:]:
                dropped.append(block)
                actions.append(f"dropped {noun} \"{block['name']}\" — over the "
                               f"limit of {cap}, and the oldest of those kept")

    # Back to front, so each removal leaves earlier offsets valid.
    for block in sorted(dropped, key=lambda b: b["start"], reverse=True):
        tex = tex[:block["start"]] + tex[block["end"]:]
    return tex, actions


def check_preferences(tex: str, policy: dict[str, Any] | None = None) -> list[str]:
    """Violations of the EXECUTABLE `prefer` rules. Empty means it complies.

    Separate from the caps because a document can satisfy every count and still
    be the wrong document. Measured in production 2026-09-30: job 385ba44d33e6
    (Twilio) shipped three experience entries and three projects -- fully
    within the caps, so nothing fired -- having chosen Seattle Kraken and
    omitted the UT Arlington IT role, which is the exact swap the user's policy
    forbids. `check_output` passed it. Rule 4: the preference was a request in
    the prompt, and only a check is a guarantee.

    Prose rules are not judged here. They are not executable, so reporting them
    as violations would mean flagging every document forever.
    """
    p = resolve(policy)
    rules = actionable_preferences(p)
    if not rules:
        return []

    keys = [b["key"] for kind in ("experience", "projects")
            for b in _entry_blocks(tex, kind)]
    violations, seen = [], set()
    for rule in rules:
        excluded = _normalise_name(rule.get("exclude") or "")
        wanted = _normalise_name(rule.get("include") or "")
        beaten = _normalise_name(rule.get("over") or "")

        if excluded and any(excluded in k for k in keys):
            msg = f'"{rule["exclude"]}" is included and the policy excludes it'
        elif wanted and not beaten and not any(wanted in k for k in keys):
            # A bare {"include": X} is "must be present". render_for_prompt has
            # rendered it as "Always include X" since the structured shapes were
            # introduced and nothing enforced it, so it was a request like every
            # other prompt line. Found by auditing: job 7718b1877d50 (Paddle)
            # shipped two experience entries with Yuno Energy -- the current role
            # -- absent altogether. Two is inside a cap of three, no exclusion
            # fired and no rival was present, so every existing check passed a
            # resume with no current job on it.
            msg = f'"{rule["include"]}" is missing and the policy requires it'
        elif (wanted and beaten and any(beaten in k for k in keys)
              and not any(wanted in k for k in keys)):
            # Only a violation in this direction. The winner alone is the
            # desired outcome, and neither present is a valid choice -- the
            # caps are limits, not quotas.
            msg = (f'"{rule["over"]}" is included but "{rule["include"]}" is '
                   f'not; the policy prefers the latter')
        else:
            continue
        if rule.get("why"):
            msg += f' ({rule["why"]})'
        if msg not in seen:
            seen.add(msg)
            violations.append(msg)
    return violations
