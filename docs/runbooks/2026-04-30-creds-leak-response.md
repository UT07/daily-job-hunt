# Runbook — STS-token-leak response (2026-04-30)

**Phase A.1.1 from the grand plan.** Run this once, end-to-end, before merging more PRs that touch shared error paths.

## What happened

`pipeline_tasks.error` was returned to the user verbatim via `GET /api/tasks/{id}` and rendered as resume content. When a Lambda's IAM role temp creds expired mid-task, `boto3.ClientError`'s `str(e)` flattened the AWS error body — including the leaked `IQoJ...` STS session token — into the error string. The frontend then displayed that string. Time window: any task that hit a creds-expiry edge between **2026-04-22** (Plan 3a/auto-apply API endpoints went live, exposing this code path) and the F1 sanitizer ship in PR #26.

## What's already done in code

- **F1 — error sanitizer**: PR #26 (the prod-health consolidation PR) ships `4c5b764 fix(security): redact AWS credentials before persisting task error/result`. After that merges + deploys, the same class of leak cannot recur.

## What you (operator) need to do

### Step 1 — Read the threat model honestly

The leaked tokens are **STS session credentials**, not permanent IAM access keys. They carry an `exp` claim — typically 1 hour from issue (`sts:AssumeRole` default). By 2026-04-30, the oldest leaked token (Apr 22) has expired roughly 150 times over and is unusable. **You do not need to "rotate" a Lambda execution role** — the role itself isn't compromised; the issued sessions are.

What IS at risk: any window between leak and expiry during which a third party (browser dev tools, network capture, frontend logging) intercepted a live token and used it. CloudTrail is the ground truth for whether that happened.

### Step 2 — Run the CloudTrail audit (basic pass)

```bash
cd /Users/ut/code/naukribaba && source .venv/bin/activate
python scripts/audit_cloudtrail_creds_leak.py \
  --since 2026-04-22T00:00:00Z \
  --role-name <Lambda-execution-role-name> \
  --output audit_report_2026-04-30.json
```

**Important — what this script can and can't see.** CloudTrail's basic
`LookupEvents` API only filters by a few attributes. With
`ResourceName=<role>`, it returns events that *operated on* the role —
`AssumeRole` calls naming it, `UpdateAssumeRolePolicy`, deletions, etc.
It does **NOT** return events whose principal was a session *issued from*
the role (e.g. an `s3:PutObject` made after assuming the role). For full
session-principal filtering, you need CloudTrail Lake (SQL) or Athena
over the trail's S3 export — see Step 2.5.

So this basic script catches:

- Someone replaying a leaked STS token to call `AssumeRole` again
- Anyone tampering with the role's trust policy (`UpdateAssumeRolePolicy`)
- Calls from a `sourceIPAddress` not on `--known-ips` (operator-supplied)

It does **not** catch session-issued S3/SQS/SNS calls. Run Step 2.5 if
you suspect deeper abuse.

Review the `flagged` list in the report. If empty → basic pass clean.

### Step 2.5 — Optional deeper pass (CloudTrail Lake / Athena)

Only needed if Step 2 turned up suspicious AssumeRole calls or you want
belt-and-suspenders coverage of session-principal events.

In CloudTrail Lake or Athena, run a query of the form:

```sql
SELECT eventTime, eventName, eventSource, sourceIPAddress, userAgent,
       userIdentity.sessionContext.sessionIssuer.userName AS issuing_role
FROM cloudtrail_logs
WHERE eventTime >= '2026-04-22T00:00:00Z'
  AND userIdentity.sessionContext.sessionIssuer.userName = '<Lambda-execution-role-name>'
  AND eventName NOT IN ('AssumeRole', 'GetCallerIdentity')
ORDER BY eventTime DESC
LIMIT 1000
```

Look for any rows whose `sourceIPAddress` is not Lambda's expected
service range, any unexpected `eventName` outside the pipeline's normal
S3/Supabase/SES surface, or any user agents that aren't `botocore` or
`aws-cli`.

### Step 3 — Only if Step 2 finds abuse

1. **Revoke the role's trust policy temporarily** to stop further sessions:
   ```bash
   aws iam update-assume-role-policy --role-name <Lambda-execution-role-name> \
     --policy-document file://<empty-trust-policy>.json
   ```
   This breaks the Lambda invocations but is reversible.

2. **Audit S3 / DB / SES** for any data the abuser could have read or written. Cross-reference with CloudTrail event timestamps.

3. **Restore the trust policy** once the F1 sanitizer (PR #26) is deployed. Verify in CloudWatch logs that no `IQoJ`-prefixed strings appear in `pipeline_tasks.error` after the deploy.

### Step 4 — Verify the fix is shipped

```bash
# After PR #26 merges + deploy.yml runs:
aws logs filter-log-events \
  --log-group-name /aws/lambda/<api-fn> \
  --filter-pattern '"IQoJ"' \
  --start-time $(date -u -v-1H +%s)000   # macOS; -d "1 hour ago" on Linux
```

Expect zero results. If hits appear, the sanitizer isn't in the deployed image — re-check `deploy.yml` ran post-merge.

## Why no permanent-key rotation in this runbook

This runbook only addresses the **leaked-STS-token** scenario. If you suspect the GitHub Actions deploy creds (`AWS_ACCESS_KEY_ID` secret) have leaked instead, that IS a permanent-key rotation and is out of scope for this runbook — see AWS docs for the canonical "rotate access keys" procedure.

## Done criteria

- [ ] CloudTrail audit script (Step 2) run, report saved
- [ ] If Step 2 flagged anything OR risk appetite demands it: Step 2.5 deeper pass run via CloudTrail Lake / Athena
- [ ] All flagged events reviewed; empty OR escalated per Step 3
- [ ] PR #26 merged
- [ ] Post-deploy `filter-log-events` shows zero `IQoJ` strings (Step 4)
- [ ] This runbook closed out in the grand plan Phase A.1.1
