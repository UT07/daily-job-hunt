"""Contract test: /api/apply/preview response must include the exact keys
the Phase 1 modal expects.

Pinned keys: eligible, reason, profile_complete, missing_required_fields,
job, platform, platform_metadata, resume, profile, cover_letter,
custom_questions, already_applied, existing_application_id, cache_hit.

Why: Smart Apply Phase 1 spec §6.2. Same pattern as PR #44.
"""
from __future__ import annotations
from app import _build_shell_response

# These are the keys Phase 1's <AutoApplyModal> reads. If you remove any of
# these from the response, update the modal first and bump this test.
REQUIRED_KEYS = {
    "eligible",
    "reason",
    "profile_complete",
    "missing_required_fields",
    "job",
    "platform",
    "platform_metadata",
    "resume",
    "profile",
    "cover_letter",
    "custom_questions",
    "already_applied",
    "existing_application_id",
    "cache_hit",
}


def test_shell_response_has_all_required_keys():
    """The shell response (the degraded path) is the canonical shape — any
    key the full path returns is also returned by the shell. Assert here."""
    shell = _build_shell_response("no_apply_url", missing=[])
    actual_keys = set(shell.keys())
    missing = REQUIRED_KEYS - actual_keys
    assert not missing, (
        f"_build_shell_response is missing keys the Phase 1 modal expects: {missing}. "
        f"Either add to _build_shell_response or update REQUIRED_KEYS + the modal."
    )
