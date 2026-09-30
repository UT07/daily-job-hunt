"""Per-task guardrail policy.

Declarative so the eval harness can diff policy changes across runs, and so
turning a check off is a reviewable one-line change rather than a code edit.

Every key here MUST be read by `guardrails.input_guards.check_input` or
`guardrails.output_guards.check_output` (or a future guard living in this
package). A key that isn't consumed by anything is worse than no key at all:
it looks like a reviewable one-line toggle but silently changes nothing --
exactly the defect class this task's audit went looking for. If you add a
flag here, add the `policy.get(...)` read for it in the same change.

There is deliberately NO "fairness_cap" key. An earlier draft of this policy
carried one, paired with an imagined `apply_fairness_cap` that was never
implemented anywhere in this package. The real geography/work-authorization
score cap is `apply_geo_score_cap` in `shared/work_auth.py`, called
unconditionally from `score_batch.py` (line ~161, right after
`score_single_job_deterministic` returns) -- not gated by any policy flag,
guardrails or otherwise. That function lives outside this package on
purpose: it mutates a score dict post-hoc, has no Violation/GuardResult
shape, and isn't part of the input/output guard pipeline these POLICIES
entries gate. Re-adding a "fairness_cap" key here without also making
score_batch.py's call actually consult it would resurrect the same dead-flag
defect; see test_guardrails_types.py::test_no_fairness_cap_key for the
regression test pinning this absence.

`pii_scrub`, declared True on every policy below, used to be exactly this
kind of dead flag: read by nothing, with `scrub_pii()` in `input_guards.py`
having no caller outside its own unit tests, the same shape as the removed
`fairness_cap` above. Closed in the task that wired the guard *nodes* into
the graph: `guard_input_node` now calls
`guardrails.input_guards.maybe_scrub_pii(prompt, task)`, which consults this
same `policy_for(task)["pii_scrub"]` flag and redacts emails/phone numbers
before the (accepted) prompt is fenced and dispatched to a third-party
free-tier provider. See `maybe_scrub_pii`'s docstring in `input_guards.py`
and `test_agents_guard_nodes.py::test_guard_input_scrubs_pii_when_policy_enables_it`
for the wiring regression test.

`prompt_echo` and `near_empty` are True for `tailor` and False everywhere
else, and the False is a measurement gap rather than a judgement that the
defect does not matter there. Both thresholds were calibrated against 740
real tailored RESUMES (CLAUDE.md rule 7 -- scope a check to the population
it is meant to judge), and the two other output tasks have populations that
would move them:

  cover_letter  `near_empty`'s floor is 40 identity anchors. A cover letter
                is ~250 words and carries roughly 25, so turning it on
                would fire on every letter ever written. `prompt_echo`
                is the more plausible of the two -- a leaked prompt in a
                letter is just as bad -- but its markers include "job
                description" and first-person-plural planning voice, and
                a cover letter is prose that may legitimately discuss the
                job description. Turn it on after measuring the marker set
                against the real cover-letter population, not before.
  score         the output is a JSON score object, not a document. Neither
                check has any meaning on it.
"""

POLICIES: dict[str, dict] = {
    "default": {
        "injection_detection": True,
        "pii_scrub": True,
        "banned_phrases": True,
        "fabrication": False,
        "latex_structure": False,
        "prompt_echo": False,
        "near_empty": False,
    },
    "score": {
        "injection_detection": True,
        "pii_scrub": True,
        "banned_phrases": False,
        "fabrication": False,
        "latex_structure": False,
        "prompt_echo": False,
        "near_empty": False,
    },
    "tailor": {
        "injection_detection": True,
        "pii_scrub": True,
        "banned_phrases": True,
        "fabrication": True,
        "latex_structure": True,
        "prompt_echo": True,
        "near_empty": True,
    },
    "cover_letter": {
        "injection_detection": True,
        "pii_scrub": True,
        "banned_phrases": True,
        "fabrication": True,
        "latex_structure": False,
        "prompt_echo": False,
        "near_empty": False,
    },
}


def policy_for(task: str) -> dict:
    return POLICIES.get(task, POLICIES["default"])
