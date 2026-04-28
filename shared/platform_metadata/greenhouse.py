"""Greenhouse application metadata fetcher.

Public API: https://developers.greenhouse.io/job-board.html
Endpoint: GET boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{posting_id}?questions=true
"""
from __future__ import annotations

import logging
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

GREENHOUSE_DEFAULT_CL_MAX = 10000  # platform-wide default per design spec


class GreenhouseFetchError(Exception):
    """Raised when Greenhouse metadata cannot be fetched.

    The `reason` attribute is one of:
    - job_no_longer_available (404)
    - greenhouse_api_error (5xx or other HTTP failure)
    - greenhouse_timeout
    """

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


# Greenhouse → spec §7.1 CustomQuestion.type vocabulary
_TYPE_MAP = {
    "input_text": "text",
    "textarea": "textarea",
    "input_file": "file",
    "multi_value_multi_select": "multi_select",
    "single_checkbox": "checkbox",
    # multi_value_single_select handled below (yes_no vs select)
}


def fetch_greenhouse(board_token: str, posting_id: str, timeout: float = 10.0) -> dict:
    """Fetch and normalize Greenhouse posting metadata.

    Args:
        board_token: Greenhouse board slug (e.g. "airbnb")
        posting_id: Greenhouse posting numeric id (as string, e.g. "7649441")
        timeout: Per-request timeout in seconds

    Returns:
        Normalized metadata dict (see shared.platform_metadata.__init__ for shape)

    Raises:
        GreenhouseFetchError: if the posting is gone (404) or the API errored
    """
    url = f"https://boards-api.greenhouse.io/v1/boards/{board_token}/jobs/{posting_id}"

    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.get(url, params={"questions": "true"})
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        if e.response.status_code == 404:
            raise GreenhouseFetchError(
                f"Greenhouse posting {board_token}/{posting_id} not found",
                reason="job_no_longer_available",
            )
        raise GreenhouseFetchError(
            f"Greenhouse API returned {e.response.status_code}",
            reason="greenhouse_api_error",
        )
    except httpx.TimeoutException:
        raise GreenhouseFetchError(
            f"Greenhouse API timeout after {timeout}s",
            reason="greenhouse_timeout",
        )

    raw = response.json()
    questions = _normalize_questions(raw.get("questions", []),
                                       default_category=None,
                                       block_description=None)

    # Merge compliance[].questions[] (EEO).
    # Each compliance block has a top-level `description` (the EEO disclosure text,
    # e.g. "Voluntary Self-Identification of Disability... OMB Control 1250-0005").
    # The questions inside have description=null. We propagate the block description
    # down so the AI prompt sees the disclosure context.
    for compliance_block in raw.get("compliance") or []:
        block_desc = compliance_block.get("description") or ""
        questions.extend(_normalize_questions(
            compliance_block.get("questions", []),
            default_category="eeo",
            block_description=block_desc,
        ))

    demo = raw.get("demographic_questions") or {}
    if isinstance(demo, dict):
        questions.extend(_normalize_questions(
            demo.get("questions", []),
            default_category="eeo",
            block_description=demo.get("description") or "",
        ))

    cl_meta = _extract_cover_letter_meta(questions)

    return {
        "platform": "greenhouse",
        "job_title": raw.get("title", ""),
        "questions": questions,
        "cover_letter_field_present": cl_meta["present"],
        "cover_letter_required": cl_meta["required"],
        "cover_letter_max_length": GREENHOUSE_DEFAULT_CL_MAX,
    }


def _map_type(gh_type: str, values: list) -> str:
    if gh_type == "multi_value_single_select":
        labels = {v.get("label", "").strip().lower() for v in values}
        if labels == {"yes", "no"}:
            return "yes_no"
        return "select"
    return _TYPE_MAP.get(gh_type, "text")


def _normalize_questions(
    raw_questions: list,
    default_category: Optional[str],
    block_description: Optional[str] = None,
) -> list[dict]:
    """Normalize raw Greenhouse questions to the unified shape."""
    normalized = []
    for q in raw_questions:
        fields = q.get("fields") or []
        if not fields:
            continue
        first = fields[0]
        values = first.get("values", [])
        # Prefer per-question description; fall back to block-level (EEO disclosure)
        description = q.get("description") or block_description or None
        entry = {
            "label": q.get("label", ""),
            "description": description,
            "required": bool(q.get("required", False)),
            "type": _map_type(first.get("type", "input_text"), values),
            "field_name": first.get("name", ""),
            "options": [v.get("label", "") for v in values],
        }
        if default_category:
            entry["category"] = default_category
        normalized.append(entry)
    return normalized


def _extract_cover_letter_meta(questions: list[dict]) -> dict:
    for q in questions:
        if q["field_name"] == "cover_letter":
            return {"present": True, "required": q["required"]}
    return {"present": False, "required": False}
