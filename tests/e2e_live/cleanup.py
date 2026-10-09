"""Delete every trace of an e2e_live test account, then PROVE it is gone.

Used by the session fixture's teardown (always runs, pass or fail) and as a CLI
for a run that died before teardown could run:

    E2E_LIVE_...=... python -m tests.e2e_live.cleanup --user-id <uuid>
    E2E_LIVE_...=... python -m tests.e2e_live.cleanup --orphans   # every e2e+*@naukribaba.test

The verification at the end is the point (CLAUDE.md rule 2): a cleanup that
cannot tell "deleted" from "did nothing" is not a cleanup. Every table that has
a `user_id` column is counted again after deletion and reported per table.
"""

from __future__ import annotations

import argparse
import sys

from tests.e2e_live._live import Admin, LiveConfig

TEST_EMAIL_DOMAIN = "@naukribaba.test"
S3_PREFIXES = ("users/{uid}/", "sessions/{uid}/")


class CleanupIncomplete(AssertionError):
    pass


def _s3_keys(bucket: str, prefix: str) -> list[str]:
    import boto3

    s3 = boto3.client("s3")
    keys, token = [], None
    while True:
        kw = {"Bucket": bucket, "Prefix": prefix}
        if token:
            kw["ContinuationToken"] = token
        page = s3.list_objects_v2(**kw)
        keys.extend(o["Key"] for o in page.get("Contents", []))
        if not page.get("IsTruncated"):
            return keys
        token = page["NextContinuationToken"]


def _s3_delete(bucket: str, keys: list[str]) -> None:
    import boto3

    s3 = boto3.client("s3")
    for i in range(0, len(keys), 1000):
        s3.delete_objects(Bucket=bucket, Delete={"Objects": [{"Key": k} for k in keys[i:i + 1000]]})


def cleanup_account(cfg: LiveConfig, uid: str, *, marker: str | None = None,
                    run_started_iso: str | None = None) -> list[str]:
    """Delete the account's rows, S3 objects, jobs_raw rows and auth user.

    Returns human-readable verification lines; raises CleanupIncomplete (after
    doing everything it can) if anything remains."""
    admin = Admin(cfg)
    lines: list[str] = []
    residue: list[str] = []

    tables = [t for t in admin.tables_with_column("user_id")]

    # Hashes of every job this account owns, captured BEFORE deletion, so the
    # shared jobs_raw rows the run created can be found afterwards.
    hashes: set[str] = set()
    if "jobs" in tables:
        for row in admin.select("jobs", {"user_id": f"eq.{uid}", "select": "*"}):
            for k in ("job_id", "job_hash", "canonical_hash"):
                if row.get(k):
                    hashes.add(str(row[k]))

    # Child tables first; a FK violation is retried on the next pass.
    pending = list(tables)
    for _ in range(4):
        still = []
        for t in pending:
            r = admin.delete(t, {"user_id": f"eq.{uid}"})
            if r.status_code >= 300:
                still.append(t)
        pending = still
        if not pending:
            break
    for t in pending:
        lines.append(f"cleanup: {t}: DELETE kept failing")

    r = admin.delete("users", {"id": f"eq.{uid}"})
    if r.status_code >= 300:
        lines.append(f"cleanup: users: DELETE -> {r.status_code} {r.text[:200]}")

    # jobs_raw is shared across users and has no user_id. Only rows this run
    # created are removed: the unique per-run company marker, or one of this
    # account's hashes AND scraped after the run started (never a pre-existing
    # shared row that happens to share a hash).
    raw_deleted = 0
    if marker:
        n = admin.count("jobs_raw", {"company": f"ilike.*{marker}*"})
        admin.delete("jobs_raw", {"company": f"ilike.*{marker}*"})
        raw_deleted += n
    if hashes and run_started_iso:
        in_list = "(" + ",".join(sorted(hashes)) + ")"
        params = {"job_hash": f"in.{in_list}", "scraped_at": f"gte.{run_started_iso}"}
        n = admin.count("jobs_raw", params)
        admin.delete("jobs_raw", params)
        raw_deleted += n

    # S3
    for tmpl in S3_PREFIXES:
        prefix = tmpl.format(uid=uid)
        keys = _s3_keys(cfg.s3_bucket, prefix)
        if keys:
            _s3_delete(cfg.s3_bucket, keys)
        lines.append(f"cleanup: s3://{cfg.s3_bucket}/{prefix} deleted {len(keys)} object(s)")

    # Auth user last: if anything above failed, the account still exists and
    # the CLI can be re-run against it.
    status = admin.delete_auth_user(uid)
    if status not in (200, 204, 404):
        lines.append(f"cleanup: auth user DELETE -> {status}")

    # ---------------- verification ----------------
    for t in tables:
        n = admin.count(t, {"user_id": f"eq.{uid}"})
        lines.append(f"cleanup: {t}: {n} rows remain")
        if n:
            residue.append(f"{t}={n}")
    n = admin.count("users", {"id": f"eq.{uid}"})
    lines.append(f"cleanup: users: {n} rows remain")
    if n:
        residue.append(f"users={n}")
    if marker:
        n = admin.count("jobs_raw", {"company": f"ilike.*{marker}*"})
        lines.append(f"cleanup: jobs_raw (marker {marker!r}, {raw_deleted} deleted): {n} rows remain")
        if n:
            residue.append(f"jobs_raw={n}")
    for tmpl in S3_PREFIXES:
        prefix = tmpl.format(uid=uid)
        n = len(_s3_keys(cfg.s3_bucket, prefix))
        lines.append(f"cleanup: s3 {prefix}: {n} objects remain")
        if n:
            residue.append(f"s3:{prefix}={n}")
    st = admin.auth_user_status(uid)
    lines.append(f"cleanup: auth user {uid}: GET -> {st} ({'gone' if st == 404 else 'STILL EXISTS'})")
    if st != 404:
        residue.append("auth_user")

    if residue:
        raise CleanupIncomplete("cleanup left residue: " + ", ".join(residue) + "\n" + "\n".join(lines))
    return lines


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--user-id")
    g.add_argument("--orphans", action="store_true",
                   help=f"clean every auth user whose email ends with {TEST_EMAIL_DOMAIN}")
    ap.add_argument("--marker", help="per-run company marker, to clean jobs_raw")
    args = ap.parse_args(argv)
    cfg = LiveConfig.from_env()
    if args.user_id:
        uids = [args.user_id]
    else:
        uids = [u["id"] for u in Admin(cfg).list_auth_users()
                if (u.get("email") or "").startswith("e2e+") and u["email"].endswith(TEST_EMAIL_DOMAIN)]
        print(f"found {len(uids)} orphan test account(s)")
    rc = 0
    for uid in uids:
        try:
            for line in cleanup_account(cfg, uid, marker=args.marker):
                print(line)
        except CleanupIncomplete as e:
            print(e, file=sys.stderr)
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
