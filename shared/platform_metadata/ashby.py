"""Ashby application metadata fetcher.

Public endpoint: POST https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiJobPosting

The spec-listed REST endpoint (GET api.ashbyhq.com/posting-api/job-posting/{uuid})
returns 401 — that is a protected partner-API endpoint. The actual public endpoint
that powers all Ashby hosted job boards is a GraphQL API at:
  https://jobs.ashbyhq.com/api/non-user-graphql

Investigation conducted 2026-04-28. Contract captured in:
  docs/superpowers/research/2026-04-28-ashby-graphql-shape.md

The `field` field on each FormFieldEntry is a JSON! scalar containing:
  - path: stable field ID (used as field_name in normalized output)
  - title: human-readable label
  - type: one of String|Email|Phone|LongText|File|Boolean|Location|ValueSelect|MultiValueSelect
  - selectableValues: [{label, value}] — only present on ValueSelect/MultiValueSelect

A null jobPosting in the response (HTTP 200) means the job is no longer available.
"""
from __future__ import annotations

import logging

import httpx

logger = logging.getLogger(__name__)

ASHBY_GRAPHQL_URL = "https://jobs.ashbyhq.com/api/non-user-graphql"
ASHBY_DEFAULT_CL_MAX = 5000  # platform-wide default per design spec

_GQL_QUERY = """
query ApiJobPosting(
  $organizationHostedJobsPageName: String!,
  $jobPostingId: String!
) {
  jobPosting(
    organizationHostedJobsPageName: $organizationHostedJobsPageName,
    jobPostingId: $jobPostingId
  ) {
    id
    title
    applicationForm {
      sections {
        title
        descriptionHtml
        fieldEntries {
          id
          isRequired
          descriptionHtml
          field
        }
      }
    }
  }
}
""".strip()


class AshbyFetchError(Exception):
    """Raised when Ashby metadata cannot be fetched.

    The `reason` attribute is one of:
    - job_no_longer_available  (jobPosting=null in response)
    - ashby_api_error          (HTTP 4xx/5xx)
    - ashby_timeout
    - ashby_metadata_unavailable  (degraded fallback if API shape changes)
    """

    def __init__(self, message: str, reason: str):
        super().__init__(message)
        self.reason = reason


# Ashby field.type → spec §7.1 CustomQuestion.type vocabulary
_TYPE_MAP = {
    "String": "text",
    "Email": "text",
    "Phone": "text",
    "Location": "text",
    "LongText": "textarea",
    "File": "file",
    "Boolean": "yes_no",
    "ValueSelect": "select",
    "MultiValueSelect": "multi_select",
}

# System field paths that indicate a cover letter field
_COVER_LETTER_SYSTEM_PATHS = {"_systemfield_cover_letter"}


def fetch_ashby(org: str, job_posting_id: str, timeout: float = 10.0) -> dict:
    """Fetch and normalize Ashby posting metadata via GraphQL.

    Args:
        org: Ashby organization slug (e.g. "ashby", "linear")
        job_posting_id: UUID from posting-api/job-board list (e.g. "145ff46b-...")
        timeout: Per-request timeout in seconds

    Returns:
        Normalized metadata dict (see shared.platform_metadata.__init__ for shape)

    Raises:
        AshbyFetchError: if the posting is gone (null response) or the API errored
    """
    payload = {
        "operationName": "ApiJobPosting",
        "variables": {
            "organizationHostedJobsPageName": org,
            "jobPostingId": job_posting_id,
        },
        "query": _GQL_QUERY,
    }

    try:
        with httpx.Client(timeout=timeout, follow_redirects=False) as client:
            response = client.post(
                f"{ASHBY_GRAPHQL_URL}?op=ApiJobPosting",
                json=payload,
            )
            response.raise_for_status()
    except httpx.HTTPStatusError as e:
        raise AshbyFetchError(
            f"Ashby GraphQL API returned {e.response.status_code}",
            reason="ashby_api_error",
        )
    except httpx.TimeoutException:
        raise AshbyFetchError(
            f"Ashby GraphQL API timeout after {timeout}s",
            reason="ashby_timeout",
        )

    raw = response.json()
    posting = (raw.get("data") or {}).get("jobPosting")

    if posting is None:
        raise AshbyFetchError(
            f"Ashby posting {org}/{job_posting_id} not found (jobPosting=null)",
            reason="job_no_longer_available",
        )

    questions = _normalize_all_fields(
        posting.get("applicationForm", {}).get("sections", [])
    )
    cl_meta = _extract_cover_letter_meta(questions)

    return {
        "platform": "ashby",
        "job_title": posting.get("title", ""),
        "questions": questions,
        "cover_letter_field_present": cl_meta["present"],
        "cover_letter_required": cl_meta["required"],
        "cover_letter_max_length": ASHBY_DEFAULT_CL_MAX,
    }


def _map_type(ashby_type: str) -> str:
    return _TYPE_MAP.get(ashby_type, "text")


def _normalize_all_fields(sections: list) -> list[dict]:
    """Flatten all sections → fieldEntries into a single normalized question list."""
    normalized = []
    for section in sections:
        for entry in section.get("fieldEntries") or []:
            field = entry.get("field") or {}
            if not field:
                continue

            ashby_type = field.get("type", "String")
            selectable = field.get("selectableValues") or []

            entry_dict = {
                "label": field.get("title", ""),
                "description": entry.get("descriptionHtml") or None,
                "required": bool(entry.get("isRequired", False)),
                "type": _map_type(ashby_type),
                "field_name": field.get("path", entry.get("id", "")),
                "options": [v.get("label", "") for v in selectable],
            }
            normalized.append(entry_dict)
    return normalized


def _is_cover_letter_field(q: dict) -> bool:
    """Detect cover letter by system path or title heuristic."""
    if q["field_name"] in _COVER_LETTER_SYSTEM_PATHS:
        return True
    return "cover letter" in q["label"].lower()


def _extract_cover_letter_meta(questions: list[dict]) -> dict:
    for q in questions:
        if _is_cover_letter_field(q):
            return {"present": True, "required": q["required"]}
    return {"present": False, "required": False}
