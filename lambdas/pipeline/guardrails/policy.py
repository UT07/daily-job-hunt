"""Per-task guardrail policy.

Declarative so the eval harness can diff policy changes across runs, and so
turning a check off is a reviewable one-line change rather than a code edit.
"""

POLICIES: dict[str, dict] = {
    "default": {
        "injection_detection": True,
        "pii_scrub": True,
        "banned_phrases": True,
        "fabrication": False,
        "latex_structure": False,
        "fairness_cap": False,
    },
    "score": {
        "injection_detection": True,
        "pii_scrub": True,
        "banned_phrases": False,
        "fabrication": False,
        "latex_structure": False,
        "fairness_cap": True,
    },
    "tailor": {
        "injection_detection": True,
        "pii_scrub": True,
        "banned_phrases": True,
        "fabrication": True,
        "latex_structure": True,
        "fairness_cap": False,
    },
    "cover_letter": {
        "injection_detection": True,
        "pii_scrub": True,
        "banned_phrases": True,
        "fabrication": True,
        "latex_structure": False,
        "fairness_cap": False,
    },
}


def policy_for(task: str) -> dict:
    return POLICIES.get(task, POLICIES["default"])
