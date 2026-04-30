#!/usr/bin/env python
"""Audit CloudTrail for suspicious activity related to a Lambda execution role.

Phase A.1.1 from the grand plan. Companion to
docs/runbooks/2026-04-30-creds-leak-response.md.

Read-only — calls only `cloudtrail:LookupEvents`. Caller needs that permission
on the account where the Lambda runs.

Scope and limitation
--------------------

CloudTrail's basic LookupEvents API supports a small set of LookupAttributes,
and `ResourceName=<role-name>` matches events that *operated on* the role —
e.g. `AssumeRole` calls naming it as their target, `UpdateAssumeRolePolicy`,
deletions, etc. It does NOT cover events whose principal was a session
*issued from* that role (e.g. `s3:PutObject` made by a Lambda after
assuming the role). For full session-principal filtering, use CloudTrail
Lake (SQL) or Athena over the trail's S3 export.

This script is intentionally narrow: it surfaces (a) every AssumeRole into
the role, broken down by source IP / user agent / time, and (b) flags any
event hitting that role from a non-AWS-service caller. That's enough to
detect "someone replayed a leaked STS token to assume the role again" or
"the role's trust policy was tampered with". It is NOT enough to detect
"someone replayed a leaked STS token and made S3 calls"; for that, the
operator needs to escalate to Athena/Lake (see runbook).

Usage:
    python scripts/audit_cloudtrail_creds_leak.py \
        --since 2026-04-22T00:00:00Z \
        --role-name <Lambda-execution-role-name> \
        --output audit_report.json

Optional:
    --region eu-west-1            # defaults to AWS_REGION / eu-west-1
    --bucket-allowlist a,b,c      # accepted by the bucket-write rule
    --known-ips 1.2.3.4,5.6.7.8   # safe IPs / non-AWS callers (no flag)
    --max-events 50000            # safety cap (default 50k)
"""
from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import sys
from collections import defaultdict

import boto3

# --- Suspicion rules ---
# Ordered: each event is matched against the first rule it satisfies; ungrouped events
# are reported as `flagged: false` and rolled up into the summary group.

_IAM_WRITE_PREFIXES = ("Create", "Put", "Attach", "Detach", "Delete", "Update")

# Tag events whose user agent ends in `.amazonaws.com` as AWS-service-originated
# so the unknown-IP rule doesn't false-flag them.
_AWS_SERVICE_USER_AGENT_RE = re.compile(r"\.amazonaws\.com$")


def _is_unexpected_iam(event_name: str, event_source: str) -> bool:
    if event_source != "iam.amazonaws.com":
        return False
    return any(event_name.startswith(p) for p in _IAM_WRITE_PREFIXES)


def _is_unexpected_sts(event_name: str, user_agent: str) -> bool:
    if event_name == "AssumeRole":
        return False  # the legitimate Lambda assume-role path
    if event_name in {"GetCallerIdentity", "GetSessionToken"}:
        # Lambda runtime / SDKs call these constantly — only flag when from a
        # non-AWS-service user agent (someone running aws-cli locally with
        # leaked creds, for instance).
        return not _AWS_SERVICE_USER_AGENT_RE.search(user_agent or "")
    return False


def _is_s3_write_outside_allowlist(event_name: str, request_params: dict | None, allowlist: set[str]) -> bool:
    if not event_name.startswith(("Put", "Delete", "Copy", "Restore")):
        return False
    if not request_params:
        return False
    bucket = request_params.get("bucketName")
    if not bucket:
        return False
    return bucket not in allowlist


def classify(event: dict, allowlist: set[str], known_ips: set[str]) -> tuple[bool, str]:
    """Return (is_flagged, reason). Reason is short for grouping."""
    event_name = event.get("EventName", "")
    event_source = event.get("EventSource", "")
    user_agent = event.get("UserAgent", "") or ""
    source_ip = event.get("SourceIPAddress", "") or ""

    raw = event.get("CloudTrailEvent")
    request_params = None
    if raw:
        try:
            request_params = json.loads(raw).get("requestParameters") or None
        except (json.JSONDecodeError, TypeError):
            pass

    if _is_unexpected_iam(event_name, event_source):
        return True, f"iam_write:{event_name}"
    if _is_unexpected_sts(event_name, user_agent):
        return True, f"sts_unusual:{event_name}"
    if _is_s3_write_outside_allowlist(event_name, request_params, allowlist):
        bucket = (request_params or {}).get("bucketName", "?")
        return True, f"s3_write_outside_allowlist:{bucket}"
    if known_ips and source_ip and source_ip not in known_ips and not _AWS_SERVICE_USER_AGENT_RE.search(user_agent):
        return True, f"unknown_ip:{source_ip}"
    return False, ""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--since", required=True, help="ISO 8601 start time, e.g. 2026-04-22T00:00:00Z")
    parser.add_argument("--role-name", required=True, help="IAM role name (the part after :role/)")
    parser.add_argument("--output", required=True, help="Path to write JSON report")
    parser.add_argument("--region", default=os.environ.get("AWS_REGION", "eu-west-1"))
    parser.add_argument("--bucket-allowlist", default="utkarsh-job-hunt",
                        help="Comma-separated S3 buckets that are expected (no flag if write hits these)")
    parser.add_argument("--known-ips", default="",
                        help="Comma-separated IPs/CIDRs known to be safe (no flag if event source matches)")
    parser.add_argument("--max-events", type=int, default=50_000)
    args = parser.parse_args()

    allowlist = {b.strip() for b in args.bucket_allowlist.split(",") if b.strip()}
    known_ips = {ip.strip() for ip in args.known_ips.split(",") if ip.strip()}
    start_time = datetime.datetime.fromisoformat(args.since.replace("Z", "+00:00"))

    ct = boto3.client("cloudtrail", region_name=args.region)

    print(f"[audit] Querying CloudTrail since {start_time.isoformat()} for role={args.role_name!r}...", file=sys.stderr)
    paginator = ct.get_paginator("lookup_events")

    raw_events = []
    seen = 0
    for page in paginator.paginate(
        LookupAttributes=[{"AttributeKey": "ResourceName", "AttributeValue": args.role_name}],
        StartTime=start_time,
        PaginationConfig={"MaxItems": args.max_events},
    ):
        for event in page.get("Events", []):
            raw_events.append(event)
            seen += 1
            if seen % 1000 == 0:
                print(f"[audit] Pulled {seen} events...", file=sys.stderr)

    print(f"[audit] Total events: {len(raw_events)}", file=sys.stderr)

    # Classify and group
    flagged = []
    grouped = defaultdict(lambda: {"count": 0, "first_seen": None, "last_seen": None,
                                    "user_agents": set(), "source_ips": set()})

    for event in raw_events:
        is_flagged, reason = classify(event, allowlist, known_ips)
        ts = event.get("EventTime")
        ts_iso = ts.isoformat() if hasattr(ts, "isoformat") else str(ts)

        if is_flagged:
            flagged.append({
                "event_id": event.get("EventId"),
                "event_name": event.get("EventName"),
                "event_time": ts_iso,
                "source_ip": event.get("SourceIPAddress"),
                "user_agent": event.get("UserAgent"),
                "reason": reason,
                "username": event.get("Username"),
            })

        key = event.get("EventName", "?")
        g = grouped[key]
        g["count"] += 1
        if g["first_seen"] is None or ts_iso < g["first_seen"]:
            g["first_seen"] = ts_iso
        if g["last_seen"] is None or ts_iso > g["last_seen"]:
            g["last_seen"] = ts_iso
        if event.get("UserAgent"):
            g["user_agents"].add(event["UserAgent"])
        if event.get("SourceIPAddress"):
            g["source_ips"].add(event["SourceIPAddress"])

    summary = {
        k: {**v, "user_agents": sorted(v["user_agents"])[:10], "source_ips": sorted(v["source_ips"])[:10]}
        for k, v in grouped.items()
    }

    report = {
        "audit_window_start": start_time.isoformat(),
        "audit_window_end": datetime.datetime.now(datetime.UTC).isoformat(),
        "role_name": args.role_name,
        "region": args.region,
        "total_events": len(raw_events),
        "flagged_count": len(flagged),
        "flagged": flagged,
        "summary_by_event_name": summary,
        "rules_applied": {
            "bucket_allowlist": sorted(allowlist),
            "known_ips": sorted(known_ips),
        },
    }

    with open(args.output, "w") as fh:
        json.dump(report, fh, indent=2, default=str)

    print(f"[audit] Wrote {args.output}: {len(raw_events)} events, {len(flagged)} flagged.", file=sys.stderr)
    if flagged:
        print(f"[audit] WARNING: {len(flagged)} suspicious events. See report and follow runbook Step 3.", file=sys.stderr)
        sys.exit(2)


if __name__ == "__main__":
    main()
