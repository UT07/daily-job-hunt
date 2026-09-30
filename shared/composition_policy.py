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

    prefer, rename, writing, emphasise

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
        "Every bullet states an OUTCOME and, wherever the base resume supports "
        "one, a NUMBER: latency, cost, uptime, throughput, error rate, time "
        "saved, scale, headcount. A bullet with no measurable result is a weak "
        "bullet.",
        "Lead each bullet with a concrete action, not a technology. "
        '"Cut p99 checkout latency 40% by sharding the session store" beats '
        '"Worked with Redis, Kubernetes and Terraform".',
        "Technologies appear where they earned the result, not as a list. If a "
        "bullet is mostly comma-separated tool names it is a Skills entry in "
        "the wrong place -- rewrite it or drop it.",
        "Never invent or inflate a number. Use only figures the base resume "
        "already states; if it gives none for an achievement, describe the "
        "outcome qualitatively instead.",
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
        lines += [f"- {rule}" for rule in p["prefer"]]
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
