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

    prefer, rename

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
DEFAULTS: dict[str, Any] = {
    "max_experience_entries": 2,
    "max_projects": 3,
    "pages": 2,
    "bullets_per_entry": {"min": 3, "max": 7},
    "prefer": [],
    "rename": [],
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
    ]
    if p["prefer"]:
        lines.append("")
        lines.append("PREFERENCES when choosing between entries:")
        lines += [f"- {rule}" for rule in p["prefer"]]
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
    return violations


def describe_violations(violations: list[str]) -> str:
    """A repair instruction naming the counts, so a retry has something to act on.

    "You emitted 5 projects, the limit is 3" is actionable. Repeating the
    original rule at a model that already ignored it is not.
    """
    if not violations:
        return ""
    return ("The document you produced breaks the composition rules: "
            + "; ".join(violations)
            + ". Remove the least relevant entries until it complies. Delete "
              "the whole entry including its itemize block — do not leave an "
              "empty shell.")
