#!/usr/bin/env python3
"""Idempotently create the 5 NaukriBaba feature flags in PostHog.

Usage:
    POSTHOG_PERSONAL_API_KEY=phx_xxxxx \
    POSTHOG_PROJECT_ID=167813 \
    python scripts/posthog_create_flags.py

The Personal API Key is different from the Project API Key (phc_*).
Create one at: https://eu.posthog.com/settings/user-api-keys
Required scopes: feature_flag:write

The project ID comes from the URL of any dashboard:
https://eu.posthog.com/project/<PROJECT_ID>/dashboard/...

Re-running this script is safe: existing flags are left alone.
"""
from __future__ import annotations

import os
import sys
from typing import Any

import requests

FLAGS: list[dict[str, Any]] = [
    {
        "key": "auto_apply",
        "name": "Auto-Apply (cloud browser)",
        "description": "Gate the auto-apply WS pipeline. Off = button hidden + endpoints return 503.",
    },
    {
        "key": "council_scoring",
        "name": "Council Scoring (3-perspective)",
        "description": "Use 3-perspective AI council vs single-shot scoring. Off = single-shot.",
    },
    {
        "key": "tailor_full_rewrite",
        "name": "Resume Full Rewrite",
        "description": "Allow heavy tailoring depth. Off = downgrade to 'moderate'.",
    },
    {
        "key": "scraper_glassdoor",
        "name": "Glassdoor Scraper",
        "description": "Run Glassdoor scraper in pipeline. Off = skip (login wall).",
    },
    {
        "key": "scraper_gradireland",
        "name": "GradIreland Scraper",
        "description": "Run GradIreland scraper. Off = skip (template changed, returning 0 jobs).",
    },
]


def _api_base() -> str:
    host = os.environ.get("POSTHOG_HOST", "https://eu.posthog.com")
    # The ingest host (eu.i.posthog.com) and management host (eu.posthog.com)
    # are different. Strip the 'i.' if user gave us the ingest host.
    return host.replace("eu.i.posthog.com", "eu.posthog.com").rstrip("/")


def _list_existing_keys(session: requests.Session, project_id: str) -> set[str]:
    """Page through /feature_flags/ and collect existing flag keys."""
    keys: set[str] = set()
    url: str | None = f"{_api_base()}/api/projects/{project_id}/feature_flags/"
    while url:
        resp = session.get(url, params={"limit": 100})
        resp.raise_for_status()
        body = resp.json()
        for flag in body.get("results", []):
            keys.add(flag["key"])
        url = body.get("next")
    return keys


def _create_flag(session: requests.Session, project_id: str, flag: dict[str, Any]) -> None:
    """POST one flag with rollout_percentage=0 (off for everyone)."""
    url = f"{_api_base()}/api/projects/{project_id}/feature_flags/"
    payload = {
        "key": flag["key"],
        "name": flag["name"],
        "active": True,  # the flag itself is "running"; rollout=0 means nobody gets it
        "filters": {
            "groups": [
                {"properties": [], "rollout_percentage": 0}
            ]
        },
        "ensure_experience_continuity": False,
    }
    if flag.get("description"):
        payload["name"] = f"{flag['name']} — {flag['description']}"
    resp = session.post(url, json=payload)
    if resp.status_code >= 400:
        print(f"  ✗ create failed ({resp.status_code}): {resp.text[:200]}")
        resp.raise_for_status()


def main() -> int:
    personal_key = os.environ.get("POSTHOG_PERSONAL_API_KEY")
    project_id = os.environ.get("POSTHOG_PROJECT_ID")
    if not personal_key:
        print("ERROR: POSTHOG_PERSONAL_API_KEY not set.", file=sys.stderr)
        print("Get one at https://eu.posthog.com/settings/user-api-keys", file=sys.stderr)
        return 1
    if not project_id:
        print("ERROR: POSTHOG_PROJECT_ID not set.", file=sys.stderr)
        print("Find it in any dashboard URL: /project/<PROJECT_ID>/dashboard/...", file=sys.stderr)
        return 1

    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {personal_key}",
        "Content-Type": "application/json",
    })

    print(f"Project: {project_id} @ {_api_base()}")
    existing = _list_existing_keys(session, project_id)
    print(f"Found {len(existing)} existing flag(s): {sorted(existing) or '(none)'}")
    print()

    created = 0
    skipped = 0
    for flag in FLAGS:
        if flag["key"] in existing:
            print(f"  • {flag['key']}: already exists — skipped")
            skipped += 1
            continue
        print(f"  + {flag['key']}: creating...")
        _create_flag(session, project_id, flag)
        created += 1

    print()
    print(f"Done. Created {created}, skipped {skipped}.")
    print(f"Manage flags at: {_api_base()}/project/{project_id}/feature_flags")
    return 0


if __name__ == "__main__":
    sys.exit(main())
