# Phase 2 — Lambda Canary via SAM `DeploymentPreference` Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add `AutoPublishAlias: live` + `DeploymentPreference` to every `AWS::Serverless::Function` in `template.yaml`, repoint API Gateway / WebSocket / Step Functions / EventBridge integrations at the `:live` alias, attach CloudWatch alarms to critical-tier functions, and prove auto-rollback works end-to-end with one staged drill.

**Architecture:** Pure infra change — no application code edits. Tier strategy is locked in the roadmap (`docs/superpowers/plans/2026-04-27-deployment-safety-roadmap.md`, Phase 2 section): three risk tiers (Critical / Pipeline / Read-only) map to three CodeDeploy deployment configs (`Canary10Percent5Minutes` / `Linear10PercentEvery1Minute` / `AllAtOnce`). Critical-tier functions get three CloudWatch alarms (Errors, Throttles, DurationP99) attached to their `DeploymentPreference.Alarms` list so a regression auto-rolls back the alias within ~6 min. Roll-out is gated by a one-function sanity deploy (`naukribaba-ws-route`) and a deliberate-failure drill before the bulk roll-out.

**Tech Stack:** AWS SAM (CloudFormation transform), AWS CodeDeploy, CloudWatch Alarms, default Lambda metrics (`AWS/Lambda` namespace — `Errors`, `Throttles`, `Duration`), `cfn-lint`, `sam validate`, `sam build`, `sam deploy`.

**Spec:** `docs/superpowers/plans/2026-04-27-deployment-safety-roadmap.md` — Phase 2 section is the source of truth for tier choices, alarm thresholds, and traffic-shift strategies. **Do not invent new architectural decisions in this plan.**

**Pulled out of scope (deferred):**
- `JobHuntApi` (FastAPI container Lambda, line 1394) — the roadmap's tier list does not enumerate it. It is the public REST API surface and arguably the most critical function, but `PackageType: Image` deploys interact with CodeDeploy traffic-shifting differently (image-pinned versions, ECR digest tracking). **This phase leaves `JobHuntApi` without `AutoPublishAlias`.** Adding canary to `JobHuntApi` requires an explicit roadmap update (image-tag versioning strategy + ECR lifecycle). Documented in the ADR (Task 11).
- Custom EMF / business-metric alarms — Phase 4 territory. This phase uses default Lambda metrics only.
- Smoke-test PreTraffic hooks — Phase 6 territory. This phase leaves `DeploymentPreference.Hooks` empty so Phase 6 can wire it in without re-deploying tier configs.
- `template.yaml` parameterization for staging — Phase 3 territory. This phase deposits canary config into the prod-only template; Phase 3 inherits it when the template is parameterized.

---

## Function Inventory (verified against `template.yaml` 2026-04-27)

33 `AWS::Serverless::Function` resources total. 32 are tiered below; `JobHuntApi` is deferred (see "out of scope" above).

### Critical tier — `Canary10Percent5Minutes` (5 functions, 3 alarms each)
Write paths that touch user-visible data or trigger billable side effects.

| Logical ID | `FunctionName` | template line |
|---|---|---|
| `WsRouteFunction` | `naukribaba-ws-route` | 1615 |
| `TailorResumeFunction` | `naukribaba-tailor-resume` | 309 |
| `CompileLatexFunction` | `naukribaba-compile-latex` | 325 |
| `SaveJobFunction` | `naukribaba-save-job` | 371 |
| `GenerateCoverLetterFunction` | `naukribaba-generate-cover-letter` | 341 |

### Pipeline tier — `Linear10PercentEvery1Minute` (12 functions, no alarms)
Long-running async batch jobs. Linear shift is gentler for state-machine-driven invocations. Phase 4 adds EMF-backed composite alarms to this tier.

| Logical ID | `FunctionName` | line |
|---|---|---|
| `LoadConfigFunction` | `naukribaba-load-config` | 247 |
| `MergeDedupFunction` | `naukribaba-merge-dedup` | 261 |
| `ScoreBatchFunction` | `naukribaba-score-batch` | 275 |
| `AggregateScoresFunction` | `naukribaba-aggregate-scores` | 299 |
| `FindContactsFunction` | `naukribaba-find-contacts` | 357 |
| `SaveMetricsFunction` | `naukribaba-save-metrics` | 387 |
| `SendEmailFunction` | `naukribaba-send-email` | 401 |
| `PostScoreFunction` | `naukribaba-post-score` | 415 |
| `SelfImproveFunction` | `naukribaba-self-improve` | 431 |
| `CheckExpiryFunction` | `naukribaba-check-expiry` | 464 |
| `StaleNudgeFunction` | `naukribaba-stale-nudges` | 479 |
| `FollowUpReminderFunction` | `naukribaba-followup-reminders` | 493 |

### Read-only / idempotent tier — `AllAtOnce` (15 functions)
Scrapers (read external sources, write to staging table — re-runnable), small utility Lambdas, and connect/disconnect handlers (DynamoDB writes are idempotent on `connectionId`).

| Logical ID | `FunctionName` | line |
|---|---|---|
| `ScrapeApifyFunction` | `naukribaba-scrape-apify` | 90 |
| `ScrapeAdzunaFunction` | `naukribaba-scrape-adzuna` | 104 |
| `ScrapeHNFunction` | `naukribaba-scrape-hn` | 118 |
| `ScrapeYCFunction` | `naukribaba-scrape-yc` | 132 |
| `ScrapeLinkedInFunction` | `naukribaba-scrape-linkedin` | 148 |
| `ScrapeIndeedFunction` | `naukribaba-scrape-indeed` | 162 |
| `ScrapeGlassdoorFunction` | `naukribaba-scrape-glassdoor` | 176 |
| `ScrapeIrishFunction` | `naukribaba-scrape-irish` | 190 |
| `ScrapeGreenhouseFunction` | `naukribaba-scrape-greenhouse` | 204 |
| `ScrapeAshbyFunction` | `naukribaba-scrape-ashby` | 218 |
| `ScrapeContactsFunction` | `naukribaba-scrape-contacts` | 232 |
| `ChunkHashesFunction` | `naukribaba-chunk-hashes` | 289 |
| `NotifyErrorFunction` | `naukribaba-notify-error` | 450 |
| `WsConnectFunction` | `naukribaba-ws-connect` | 1581 |
| `WsDisconnectFunction` | `naukribaba-ws-disconnect` | 1599 |

### Cross-tier integrations that need re-pointing
After `AutoPublishAlias: live` is added, **`Function.Arn` resolves to `$LATEST` (unversioned)** and bypasses CodeDeploy. Consumers must reference `Function.Alias` (the alias ARN, e.g. `arn:...:function:Foo:live`) so canary traffic-shifting actually applies.

| Caller | Current | After |
|---|---|---|
| WS API Gateway integrations (3) | `${WsXFunction.Arn}` (1554, 1561, 1568) | `${WsXFunction.Alias}` |
| WS Lambda Permissions (3) | `!Ref WsXFunction` (1640, 1648, 1656) | `!Ref WsXFunctionAliaslive` |
| `DailyPipelineStateMachine.DefinitionString` | `${FnLogical.Arn}` (lines 737-1147 — see Task 6/7/8) | `${FnLogical.Alias}` |
| `SingleJobPipelineStateMachine.DefinitionString` | `${FnLogical.Arn}` (lines 1195-1287) | `${FnLogical.Alias}` |
| `StepFunctionsRole` IAM policy | `!GetAtt FnLogical.Arn` (665-691) | Add `!Ref FnLogicalAliaslive` alongside |
| EventBridge schedule targets (3) | `!GetAtt FnLogical.Arn` (1320, 1361, 1381) | `!Ref FnLogicalAliaslive` |
| EventBridge Lambda permissions (3) | `!Ref FnLogical` (1348, 1368, 1388) | `!Ref FnLogicalAliaslive` |

---

## File Structure

```
template.yaml                                              (MODIFY) AutoPublishAlias + DeploymentPreference per tier; repoint integrations
monitoring/
  alarms.yaml                                              (CREATE) reusable alarm fragments — canonical authoring location
.github/workflows/deploy.yml                               (MODIFY) timeout 30 → 45 min; --no-disable-rollback
docs/superpowers/specs/
  2026-04-27-canary-strategy-decision.md                   (CREATE) ADR
```

---

## Snippet Reference (defined ONCE here; tasks below say "apply Snippet X to function Y")

### Snippet A — Critical tier function block

Inserted under each Critical-tier function's `Properties:` block, **after `MemorySize:`** and **before `Environment:`** (or `Policies:` if no `Environment`). Indentation: 6 spaces.

```yaml
      AutoPublishAlias: live
      DeploymentPreference:
        Type: Canary10Percent5Minutes
        Alarms:
          - !Ref <FunctionLogicalId>ErrorsAlarm
          - !Ref <FunctionLogicalId>ThrottlesAlarm
          - !Ref <FunctionLogicalId>DurationP99Alarm
```

Replace `<FunctionLogicalId>` with the function's CFN logical ID (e.g. `WsRouteFunction`).

### Snippet B — Pipeline tier function block

```yaml
      AutoPublishAlias: live
      DeploymentPreference:
        Type: Linear10PercentEvery1Minute
```

No `Alarms` list. Pipeline tier relies on the linear-shift duration (10 min) plus Phase 4 EMF alarms layered in later.

### Snippet C — Read-only tier function block

`AllAtOnce` is the SAM default if `DeploymentPreference` is omitted, but the roadmap requires it be **explicit** so the choice is documented.

```yaml
      AutoPublishAlias: live
      DeploymentPreference:
        Type: AllAtOnce
```

### Snippet D — Critical-tier alarm triplet (parameterized)

The alarm pattern below is repeated **5 times** in `monitoring/alarms.yaml` and inlined into `template.yaml`. Substitute `<L>` (function logical ID) and `<N>` (function name string) for each of the 5 critical-tier functions.

```yaml
  <L>ErrorsAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: <N>-Errors
      AlarmDescription: Trips canary if any errors during traffic shift
      Namespace: AWS/Lambda
      MetricName: Errors
      Dimensions:
        - Name: FunctionName
          Value: <N>
        - Name: Resource
          Value: <N>:live
      Statistic: Sum
      Period: 60
      EvaluationPeriods: 1
      Threshold: 0
      ComparisonOperator: GreaterThanThreshold
      TreatMissingData: notBreaching

  <L>ThrottlesAlarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: <N>-Throttles
      AlarmDescription: Trips canary on any concurrency throttling during shift
      Namespace: AWS/Lambda
      MetricName: Throttles
      Dimensions:
        - Name: FunctionName
          Value: <N>
        - Name: Resource
          Value: <N>:live
      Statistic: Sum
      Period: 60
      EvaluationPeriods: 1
      Threshold: 0
      ComparisonOperator: GreaterThanThreshold
      TreatMissingData: notBreaching

  <L>DurationP99Alarm:
    Type: AWS::CloudWatch::Alarm
    Properties:
      AlarmName: <N>-DurationP99
      AlarmDescription: Trips canary if p99 duration exceeds 30s over 5min — catches death spiral
      Namespace: AWS/Lambda
      MetricName: Duration
      Dimensions:
        - Name: FunctionName
          Value: <N>
        - Name: Resource
          Value: <N>:live
      ExtendedStatistic: p99
      Period: 300
      EvaluationPeriods: 1
      Threshold: 30000
      ComparisonOperator: GreaterThanThreshold
      TreatMissingData: notBreaching
```

The 5 substitution pairs:

| `<L>` | `<N>` |
|---|---|
| `WsRouteFunction` | `naukribaba-ws-route` |
| `TailorResumeFunction` | `naukribaba-tailor-resume` |
| `CompileLatexFunction` | `naukribaba-compile-latex` |
| `SaveJobFunction` | `naukribaba-save-job` |
| `GenerateCoverLetterFunction` | `naukribaba-generate-cover-letter` |

> **Why `Resource: <N>:live` dimension?** The default `AWS/Lambda` metrics are emitted with both an unqualified `FunctionName` dimension and a qualified `Resource = FunctionName:Alias` dimension when invoked via an alias. Filtering on `Resource = <N>:live` scopes the alarm to traffic going through the published alias only — so canary errors register, but `$LATEST` test invocations do not pollute the alarm.

---

## Task 0: Branch sanity check (~5 min)

- [ ] **Step 1: Confirm clean tree on the worktree branch**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca && git status
```

Expected: `working tree clean` on `claude/objective-sanderson-eeedca`.

- [ ] **Step 2: Confirm `template.yaml` matches the version this plan was authored against**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca && wc -l template.yaml
```

Expected: ~1758 lines. If the count differs by more than ±5, re-verify function logical IDs against the inventory above before proceeding.

---

## Task 1: Create `monitoring/alarms.yaml` (~25 min)

**Files:**
- Create: `monitoring/alarms.yaml`

`monitoring/alarms.yaml` is the canonical authoring location. The file is **not** transformed into `template.yaml` automatically (see ADR Decision 2 — no `AWS::Include`). Authors edit `monitoring/alarms.yaml` first, then the same blocks are inlined into `template.yaml` in Tasks 3 and 6. The duplication is annoying but bounded (15 resources) and goes away when Phase 4 introduces a CFN macro.

- [ ] **Step 1: Create the directory and file**

```bash
mkdir -p /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/monitoring
```

- [ ] **Step 2: Write `monitoring/alarms.yaml`**

Use Snippet D as the template. Expand it 5 times — once per `<L>/<N>` pair from the substitution table. The resulting file has 15 alarm resources at 2-space indent (resource-level), prefixed with this header:

```yaml
# monitoring/alarms.yaml
# Canonical CloudWatch alarm definitions for Phase 2 critical-tier Lambda canaries.
# Inlined into template.yaml's Resources block (see plan Task 3 / Task 6). Authored
# here so future edits have one source of truth; copy-paste into template.yaml on
# any change.
#
# Roadmap: docs/superpowers/plans/2026-04-27-deployment-safety-roadmap.md
# Tier:    Critical (Canary10Percent5Minutes), 5 functions × 3 alarms each.
```

Followed by the 15 alarm blocks generated from Snippet D × the 5 substitution pairs. Do **not** invent any new fields — every alarm has identical structure modulo `<L>` and `<N>`.

- [ ] **Step 3: cfn-lint the alarm fragments inside a stub template**

`monitoring/alarms.yaml` is not a valid CFN template on its own (no `AWSTemplateFormatVersion`, no top-level `Resources:`). Wrap it in a temp scratch template to lint:

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca && \
( echo 'AWSTemplateFormatVersion: "2010-09-09"'; echo 'Resources:'; cat monitoring/alarms.yaml ) > /tmp/alarms-lint.yaml && \
pip show cfn-lint >/dev/null 2>&1 || pip install cfn-lint==1.* && \
cfn-lint /tmp/alarms-lint.yaml
```

Expected: zero errors. If `cfn-lint` complains about `ExtendedStatistic` on the Duration alarms, recheck the YAML — `ExtendedStatistic: p99` (string) is correct, not `Statistic: p99`.

- [ ] **Step 4: Commit**

```bash
git add monitoring/alarms.yaml && git commit -m "feat(monitoring): add CloudWatch alarm definitions for critical-tier canaries

5 × 3 = 15 alarms covering Errors, Throttles, p99 Duration for
naukribaba-ws-route, naukribaba-tailor-resume, naukribaba-compile-latex,
naukribaba-save-job, naukribaba-generate-cover-letter.

Authored standalone in monitoring/ as canonical source; inlined into
template.yaml in subsequent commits (no Fn::Transform machinery —
keeps Phase 2 zero-dependency)."
```

---

## Task 2: Step-1 sanity — `AutoPublishAlias: live` on **only** `WsRouteFunction`, deploy, verify alias (~30 min)

**Why a one-function pre-flight:** SAM auto-generates extra resources when `AutoPublishAlias` is added (`<Fn>Version<hash>`, `<Fn>Aliaslive`). Finding out at function #1 if there's a SAM CLI / IAM / ECR misconfig is cheaper than at function #28. This task **does not yet add `DeploymentPreference`** — that comes in Task 3.

**Files:**
- Modify: `template.yaml` line 1615-1635 (only the `WsRouteFunction` block)

- [ ] **Step 1: Open `template.yaml` and locate `WsRouteFunction` (line 1615)**

- [ ] **Step 2: Insert `AutoPublishAlias: live` after `MemorySize: 128`**

After line 1623 (`      MemorySize: 128`), insert one line:

```yaml
      AutoPublishAlias: live
```

The block becomes:

```yaml
  WsRouteFunction:
    Type: AWS::Serverless::Function
    Properties:
      FunctionName: naukribaba-ws-route
      CodeUri: lambdas/browser/
      Handler: ws_route.handler
      Runtime: python3.11
      Timeout: 5
      MemorySize: 128
      AutoPublishAlias: live
      Environment:
        ...
```

- [ ] **Step 3: Validate the template**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca && sam validate --lint
```

Expected: `template.yaml is a valid SAM Template`.

- [ ] **Step 4: Commit, push, and trigger deploy on the branch**

Per memory `feedback_sam_only.md`: never bypass `deploy.yml` with raw `sam deploy`. Use `workflow_dispatch` against the branch (do not merge to main yet):

```bash
git add template.yaml && git commit -m "feat(infra): add AutoPublishAlias to ws-route — Phase 2 step-1 sanity"
git push origin claude/objective-sanderson-eeedca
gh workflow run deploy.yml --ref claude/objective-sanderson-eeedca
gh run watch $(gh run list --workflow=deploy.yml --limit=1 --json databaseId -q '.[0].databaseId') --exit-status
```

Expected: deploy succeeds (~5-8 min). The CFN changeset shows `Add` for `WsRouteFunctionVersion<hash>` and `WsRouteFunctionAliaslive`.

- [ ] **Step 5: Verify the alias exists in AWS**

```bash
aws lambda get-alias --function-name naukribaba-ws-route --name live --region eu-west-1
```

Expected output (key fields):

```json
{
    "AliasArn": "arn:aws:lambda:eu-west-1:385017713886:function:naukribaba-ws-route:live",
    "Name": "live",
    "FunctionVersion": "1"
}
```

`FunctionVersion: "1"` confirms SAM published a version. If it says `$LATEST`, the deploy didn't pick up `AutoPublishAlias` — re-check the YAML insertion.

- [ ] **Step 6: Verify CodeDeploy is no-op (no DeploymentPreference yet)**

Open `https://eu-west-1.console.aws.amazon.com/codesuite/codedeploy/applications`. Expected: a CodeDeploy Application exists (auto-created by SAM, named like `<stack>-WsRouteFunction-<hash>`), but no deployments listed yet — there's no `DeploymentPreference` to drive a CodeDeploy deployment. The alias was created via plain CFN. **This is expected.**

- [ ] **Step 7: Smoke test that `naukribaba-ws-route` still runs**

The WS API Gateway integration still points at `${WsRouteFunction.Arn}` (we haven't repointed yet — Task 4). So traffic flows to `$LATEST`. This is fine — Task 4 will repoint after Task 3 proves the canary works.

```bash
aws lambda invoke --function-name naukribaba-ws-route \
  --payload '{"requestContext":{"connectionId":"test","domainName":"x","stage":"prod","routeKey":"$default"},"body":"{\"action\":\"ping\"}"}' \
  --cli-binary-format raw-in-base64-out /tmp/out.json --region eu-west-1 && cat /tmp/out.json
```

Expected: a JSON response (probably `{"statusCode":401}` because the test request has no auth — but a 401 is a healthy 401, the function ran). If you get an `Unhandled` error, the deploy regressed something — investigate before proceeding.

---

## Task 3: Add `DeploymentPreference` + alarms to `WsRouteFunction` (~30 min)

**Files:**
- Modify: `template.yaml` (1) `WsRouteFunction` block; (2) bottom of `Resources:` to inline 3 ws-route alarms from `monitoring/alarms.yaml`

- [ ] **Step 1: Inline the 3 ws-route alarms into `template.yaml`**

Find the end of the `Resources:` section in `template.yaml` — the last resource (`BrowserSessionTaskDef`) ends around line 1733, just before the `Outputs:` block at line 1734. Insert a section header followed by `WsRouteFunctionErrorsAlarm`, `WsRouteFunctionThrottlesAlarm`, `WsRouteFunctionDurationP99Alarm` — copy verbatim from `monitoring/alarms.yaml` (the three blocks generated from Snippet D with `<L>=WsRouteFunction`, `<N>=naukribaba-ws-route`).

```yaml
  # --- Phase 2 canary alarms (sourced from monitoring/alarms.yaml) ---
  # (paste WsRouteFunctionErrorsAlarm, WsRouteFunctionThrottlesAlarm,
  #  WsRouteFunctionDurationP99Alarm blocks here — verbatim from monitoring/alarms.yaml)
```

Indentation: 2 spaces (resource-level, same as `BrowserSessionTaskDef`).

- [ ] **Step 2: Replace the lone `AutoPublishAlias: live` line in `WsRouteFunction` with full Snippet A**

Locate the line added in Task 2 Step 2 and expand to Snippet A (with `<FunctionLogicalId>=WsRouteFunction`):

```yaml
      AutoPublishAlias: live
      DeploymentPreference:
        Type: Canary10Percent5Minutes
        Alarms:
          - !Ref WsRouteFunctionErrorsAlarm
          - !Ref WsRouteFunctionThrottlesAlarm
          - !Ref WsRouteFunctionDurationP99Alarm
```

- [ ] **Step 3: Validate**

```bash
sam validate --lint
```

Expected: valid. cfn-lint may warn `W3037` (Resource not used in template) on the alarms — false positive (they're referenced via `!Ref` inside `DeploymentPreference.Alarms`). Suppress only that warning class if it blocks; do not suppress real errors.

- [ ] **Step 4: Deploy**

```bash
git add template.yaml && git commit -m "feat(infra): canary + alarms on ws-route (first canary-driven deploy)"
git push origin claude/objective-sanderson-eeedca
gh workflow run deploy.yml --ref claude/objective-sanderson-eeedca
gh run watch $(gh run list --workflow=deploy.yml --limit=1 --json databaseId -q '.[0].databaseId') --exit-status
```

Expected: deploy succeeds. Because the function code didn't change, SAM publishes a no-op new version but CodeDeploy still records an "InPlace" deployment that completes immediately (0 → 100% with the same code).

- [ ] **Step 5: Confirm CodeDeploy deployment record exists**

Console: `https://eu-west-1.console.aws.amazon.com/codesuite/codedeploy/applications?region=eu-west-1`. Expected: an application named like `job-hunt-api-WsRouteFunction-<hash>` with at least one Deployment listed (status `Succeeded`). Click into the deployment — confirm it used `Canary10Percent5Minutes`.

- [ ] **Step 6: Confirm the alarms exist in CloudWatch**

```bash
aws cloudwatch describe-alarms --alarm-name-prefix naukribaba-ws-route- --region eu-west-1 \
  --query 'MetricAlarms[].AlarmName' --output text
```

Expected (any order):

```
naukribaba-ws-route-DurationP99    naukribaba-ws-route-Errors    naukribaba-ws-route-Throttles
```

All three should be in state `OK` or `INSUFFICIENT_DATA`.

---

## Task 4: Repoint WS API Gateway integrations + permissions at the alias (~25 min)

Until this task lands, the canary on `WsRouteFunction` is decorative — API Gateway invokes `${WsRouteFunction.Arn}` (resolves to `$LATEST`) and bypasses traffic-shifting. After this task, real WS traffic flows through `:live`.

**Files:**
- Modify: `template.yaml` lines 1549-1568 (3 integration blocks), lines 1637-1659 (3 permission blocks), and add Snippet C to `WsConnectFunction` + `WsDisconnectFunction`

- [ ] **Step 1: Repoint the 3 WS integrations**

For `ConnectIntegration` (line 1549), `DisconnectIntegration` (line 1556), `DefaultIntegration` (line 1563): change every `.Arn` to `.Alias` in the `IntegrationUri` `!Sub` string. Example for `DefaultIntegration`:

```yaml
  DefaultIntegration:
    Type: AWS::ApiGatewayV2::Integration
    Properties:
      ApiId: !Ref BrowserWebSocketApi
      IntegrationType: AWS_PROXY
      IntegrationUri: !Sub "arn:aws:apigateway:${AWS::Region}:lambda:path/2015-03-31/functions/${WsRouteFunction.Alias}/invocations"
```

The other two are identical edits with `WsConnectFunction.Alias` and `WsDisconnectFunction.Alias` respectively.

- [ ] **Step 2: Add Snippet C to `WsConnectFunction` and `WsDisconnectFunction`**

Step 1 references `${WsConnectFunction.Alias}` and `${WsDisconnectFunction.Alias}`, so those functions must have `AutoPublishAlias: live`. Insert Snippet C after `MemorySize: 128` in both functions (lines 1581 and 1599):

```yaml
      AutoPublishAlias: live
      DeploymentPreference:
        Type: AllAtOnce
```

This is exactly the change Task 8 would make for these two functions; moving it earlier costs nothing.

- [ ] **Step 3: Repoint the 3 WS Lambda Permissions**

For `WsConnectPermission` (line 1637), `WsDisconnectPermission` (line 1645), `WsRoutePermission` (line 1653): change `FunctionName: !Ref Ws<X>Function` to `FunctionName: !Ref Ws<X>FunctionAliaslive`. SAM auto-generates the logical ID `<FunctionLogicalId>Aliaslive` for the alias resource. After:

```yaml
  WsConnectPermission:
    Type: AWS::Lambda::Permission
    Properties:
      FunctionName: !Ref WsConnectFunctionAliaslive
      Action: lambda:InvokeFunction
      Principal: apigateway.amazonaws.com
      SourceArn: !Sub "arn:aws:execute-api:${AWS::Region}:${AWS::AccountId}:${BrowserWebSocketApi}/*/$connect"
```

Same shape for `WsDisconnectPermission` (`!Ref WsDisconnectFunctionAliaslive`) and `WsRoutePermission` (`!Ref WsRouteFunctionAliaslive`).

- [ ] **Step 4: Validate and deploy**

```bash
sam validate --lint
git add template.yaml && git commit -m "feat(infra): repoint WS integrations + perms at :live alias

API Gateway WS routes now invoke <fn>:live instead of \$LATEST so canary
traffic-shifting actually applies. WsConnect/WsDisconnect get AllAtOnce
DeploymentPreference to satisfy the alias-existence requirement of the
permission Refs (would have happened in Task 8 anyway)."
git push origin claude/objective-sanderson-eeedca
gh workflow run deploy.yml --ref claude/objective-sanderson-eeedca
gh run watch $(gh run list --workflow=deploy.yml --limit=1 --json databaseId -q '.[0].databaseId') --exit-status
```

Expected: deploy succeeds. CFN changeset shows `Modify` on the 3 integrations and 3 permissions, plus `Add` for `WsConnectFunctionVersion`, `WsConnectFunctionAliaslive`, `WsDisconnectFunctionVersion`, `WsDisconnectFunctionAliaslive`.

- [ ] **Step 5: Verify integration targets via AWS CLI**

```bash
WS_API_ID=$(aws apigatewayv2 get-apis --region eu-west-1 --query "Items[?Name=='naukribaba-browser-ws'].ApiId" --output text)
aws apigatewayv2 get-integrations --api-id $WS_API_ID --region eu-west-1 --query "Items[].IntegrationUri" --output text
```

Expected: 3 strings, each ending in `:naukribaba-ws-connect:live/invocations`, `:naukribaba-ws-disconnect:live/invocations`, `:naukribaba-ws-route:live/invocations` respectively.

---

## Task 5: Roll-out drill — deliberately break ws-route, deploy, watch auto-rollback (~45 min)

**Why now:** Before rolling canary out to 27 more functions, prove end-to-end that a bad deploy auto-rolls back. Roadmap success criterion: "A deliberate bad deploy ... is auto-rolled-back by CodeDeploy within 6 minutes."

**Files:**
- Modify: `lambdas/browser/ws_route.py` — add `raise RuntimeError(...)` at top of handler
- Then revert.

- [ ] **Step 1: Read current handler**

```bash
head -20 /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/lambdas/browser/ws_route.py
```

Note location of `def handler(event, context):`.

- [ ] **Step 2: Inject deliberate failure**

Edit `lambdas/browser/ws_route.py` — first line of the handler:

```python
def handler(event, context):
    raise RuntimeError("phase-2 canary drill — should auto-rollback within 6min")
    # ... existing handler body unchanged below ...
```

- [ ] **Step 3: Pre-stage an invocation loop in a second terminal**

The Errors alarm needs at least 1 datapoint of `Sum > 0` over a 60s window. Pre-stage the loop (do not run it yet):

```bash
# Run this AFTER the deploy reaches `SAM Deploy` step
for i in {1..30}; do
  aws lambda invoke --function-name naukribaba-ws-route:live \
    --payload '{"requestContext":{"connectionId":"drill","domainName":"x","stage":"prod","routeKey":"$default"},"body":"{\"action\":\"ping\"}"}' \
    --cli-binary-format raw-in-base64-out --region eu-west-1 /tmp/drill-$i.json
  sleep 10
done
```

- [ ] **Step 4: Commit and deploy the broken code**

```bash
git add lambdas/browser/ws_route.py && git commit -m "drill(phase-2): deliberately break ws-route to verify auto-rollback (REVERT)"
git push origin claude/objective-sanderson-eeedca
gh workflow run deploy.yml --ref claude/objective-sanderson-eeedca
RUN_ID=$(gh run list --workflow=deploy.yml --limit=1 --json databaseId -q '.[0].databaseId')
echo "Run: $RUN_ID"
gh run watch $RUN_ID
```

- [ ] **Step 5: Once the deploy is past `SAM Build` and into `SAM Deploy`, start the invocation loop from Step 3**

Errors materialize in CloudWatch. The 5-minute canary window starts when CodeDeploy begins shifting 10% traffic.

- [ ] **Step 6: Watch CodeDeploy auto-rollback in console**

CodeDeploy app for `WsRouteFunction`. Active deployment should:

1. Start `Step 1: shift 10% traffic to new version` — green.
2. Within 1-2 min of traffic + errors, `naukribaba-ws-route-Errors` alarm trips (state `ALARM`).
3. CodeDeploy detects alarm → status flips to `Stopped`, reason `Deployment alarm triggered`.
4. Alias `naukribaba-ws-route:live` remains pointing at the **prior** function version.

Total time: ~3-5 min from deploy start to rollback complete.

- [ ] **Step 7: Verify alias rolled back**

```bash
aws lambda get-alias --function-name naukribaba-ws-route --name live --region eu-west-1 --query 'FunctionVersion'
aws lambda list-versions-by-function --function-name naukribaba-ws-route --region eu-west-1 --query 'Versions[].Version'
```

Expected: alias version is **lower than** the highest function version (the broken one was published but never promoted to alias). E.g. `Versions = ["$LATEST", "1", "2", "3", "4"]` and alias points at `"3"`.

- [ ] **Step 8: Verify the deploy job in GitHub also failed**

```bash
gh run view $RUN_ID --log-failed | tail -50
```

Expected: `SAM Deploy` step exits non-zero with a CloudFormation rollback error mentioning the CodeDeploy deployment alarm. **Correct behaviour** — the GitHub Actions failure is what gates further commits to main on broken canaries.

- [ ] **Step 9: Revert the deliberate break**

```bash
git revert HEAD --no-edit
git push origin claude/objective-sanderson-eeedca
gh workflow run deploy.yml --ref claude/objective-sanderson-eeedca
gh run watch $(gh run list --workflow=deploy.yml --limit=1 --json databaseId -q '.[0].databaseId') --exit-status
```

Expected: deploy succeeds. Alarm returns to `OK` within ~1 min after the new version takes 100% traffic.

- [ ] **Step 10: Capture drill artefacts for the ADR**

Run and save:

```bash
aws cloudwatch describe-alarm-history --alarm-name naukribaba-ws-route-Errors --region eu-west-1 \
  --max-items 3 --query 'AlarmHistoryItems[].[Timestamp,HistoryItemType,HistorySummary]' --output table
```

These rows go into ADR Section "Drill evidence" (Task 11).

---

## Task 6: Roll out Critical tier to remaining 4 functions (~30 min)

**Files:**
- Modify: `template.yaml` — add Snippet A to 4 functions; inline 12 alarms; repoint Step Functions

- [ ] **Step 1: Apply Snippet A to 4 functions**

For each function, insert Snippet A after `MemorySize:`. Substitute `<FunctionLogicalId>` per the table:

| Function (line) | `<FunctionLogicalId>` |
|---|---|
| `TailorResumeFunction` (309) | `TailorResumeFunction` |
| `CompileLatexFunction` (325) | `CompileLatexFunction` |
| `GenerateCoverLetterFunction` (341) | `GenerateCoverLetterFunction` |
| `SaveJobFunction` (371) | `SaveJobFunction` |

- [ ] **Step 2: Inline the 12 corresponding alarms into `template.yaml`**

Append **after** the existing `WsRouteFunctionDurationP99Alarm` block (added in Task 3 Step 1) the 12 alarm blocks for `TailorResumeFunction`, `CompileLatexFunction`, `GenerateCoverLetterFunction`, `SaveJobFunction` — copy verbatim from `monitoring/alarms.yaml` (the four blocks of three alarms each).

- [ ] **Step 3: Repoint Step Functions DefinitionString for these 4 functions**

`DailyPipelineStateMachine` (line 725) and `SingleJobPipelineStateMachine` (line 1162) both invoke these functions via `Resource: "${FnLogical.Arn}"` strings inside `DefinitionString: !Sub |`. Change `.Arn` → `.Alias` at these exact lines:

| `<FunctionLogicalId>` | Lines to edit |
|---|---|
| `TailorResumeFunction` | 991, 1227 |
| `CompileLatexFunction` | 999, 1035, 1234, 1258 |
| `GenerateCoverLetterFunction` | 1023, 1247 |
| `SaveJobFunction` | 1071, 1077, 1282, 1287 |

For example, line 991:

```yaml
                    "Resource": "${TailorResumeFunction.Arn}",
```

becomes

```yaml
                    "Resource": "${TailorResumeFunction.Alias}",
```

- [ ] **Step 4: Update `StepFunctionsRole` IAM with alias ARNs**

The IAM policy at lines 658-691 lists `!GetAtt <Fn>.Arn` for each function. After adding aliases, Step Functions invokes the alias ARN — so add `!Ref <Fn>Aliaslive` alongside the existing `!GetAtt <Fn>.Arn`. Keep both forms during the rollout (some functions still don't have aliases until Tasks 7-8). Add to the `Resource:` list:

```yaml
                  - !Ref TailorResumeFunctionAliaslive
                  - !Ref CompileLatexFunctionAliaslive
                  - !Ref GenerateCoverLetterFunctionAliaslive
                  - !Ref SaveJobFunctionAliaslive
```

- [ ] **Step 5: Validate and deploy**

```bash
sam validate --lint
git add template.yaml && git commit -m "feat(infra): canary + alarms on remaining 4 critical-tier functions

Adds AutoPublishAlias + Canary10Percent5Minutes + 3 alarms each on
naukribaba-tailor-resume, naukribaba-compile-latex, naukribaba-generate-
cover-letter, naukribaba-save-job. Step Functions DefinitionString and
StepFunctionsRole IAM updated to invoke :live aliases."
git push origin claude/objective-sanderson-eeedca
gh workflow run deploy.yml --ref claude/objective-sanderson-eeedca
gh run watch $(gh run list --workflow=deploy.yml --limit=1 --json databaseId -q '.[0].databaseId') --exit-status
```

Expected: deploy succeeds (~10 min — 4 canary windows can shift in parallel within CFN, but each takes 5+min to complete its `Canary10Percent5Minutes` schedule).

- [ ] **Step 6: Verify all 5 critical-tier aliases**

```bash
for fn in naukribaba-ws-route naukribaba-tailor-resume naukribaba-compile-latex naukribaba-generate-cover-letter naukribaba-save-job; do
  echo "$fn → $(aws lambda get-alias --function-name $fn --name live --region eu-west-1 --query FunctionVersion --output text)"
done
```

Expected: 5 lines, each with a numeric function version.

- [ ] **Step 7: Verify all 15 critical-tier alarms exist and are not in ALARM state**

```bash
aws cloudwatch describe-alarms --alarm-name-prefix naukribaba- --region eu-west-1 \
  --query "MetricAlarms[?ends_with(AlarmName, '-Errors') || ends_with(AlarmName, '-Throttles') || ends_with(AlarmName, '-DurationP99')].[AlarmName,StateValue]" \
  --output table
```

Expected: 15 rows. State values `OK` or `INSUFFICIENT_DATA`. Any `ALARM` state needs investigation.

---

## Task 7: Roll out Pipeline tier (Linear10PercentEvery1Minute) to 12 functions (~25 min)

**Files:**
- Modify: `template.yaml` — add Snippet B to 12 pipeline-tier functions; repoint Step Functions and EventBridge integrations

- [ ] **Step 1: Apply Snippet B to all 12 pipeline-tier functions**

For each, insert Snippet B (`AutoPublishAlias: live` + `DeploymentPreference: Type: Linear10PercentEvery1Minute`) after `MemorySize:`:

```
LoadConfigFunction (247), MergeDedupFunction (261), ScoreBatchFunction (275),
AggregateScoresFunction (299), FindContactsFunction (357), SaveMetricsFunction (387),
SendEmailFunction (401), PostScoreFunction (415), SelfImproveFunction (431),
CheckExpiryFunction (464), StaleNudgeFunction (479), FollowUpReminderFunction (493)
```

- [ ] **Step 2: Repoint Step Functions DefinitionString for the 9 SF-invoked pipeline functions**

Change `.Arn` → `.Alias` at these lines:

| `<FunctionLogicalId>` | Lines |
|---|---|
| `LoadConfigFunction` | 737 |
| `MergeDedupFunction` | 922 |
| `ScoreBatchFunction` | 953, 1195 |
| `AggregateScoresFunction` | 968 |
| `FindContactsFunction` | 1059, 1271 |
| `SaveMetricsFunction` | 1111 |
| `SendEmailFunction` | 1124 |
| `PostScoreFunction` | 1097 |
| `SelfImproveFunction` | 1135 |

(`CheckExpiryFunction`, `StaleNudgeFunction`, `FollowUpReminderFunction` are not invoked by Step Functions — they're EventBridge targets, handled in Step 4.)

- [ ] **Step 3: Update `StepFunctionsRole` IAM**

Add to the `Resource:` list (lines 664-691):

```yaml
                  - !Ref LoadConfigFunctionAliaslive
                  - !Ref MergeDedupFunctionAliaslive
                  - !Ref ScoreBatchFunctionAliaslive
                  - !Ref AggregateScoresFunctionAliaslive
                  - !Ref FindContactsFunctionAliaslive
                  - !Ref SaveMetricsFunctionAliaslive
                  - !Ref SendEmailFunctionAliaslive
                  - !Ref PostScoreFunctionAliaslive
                  - !Ref SelfImproveFunctionAliaslive
```

- [ ] **Step 4: Repoint 3 EventBridge schedule targets**

Change `!GetAtt <Fn>.Arn` to `!Ref <Fn>Aliaslive` for the 3 EB-invoked Lambdas:

`ExpiryCheckSchedule` (line 1312, target line 1320):

```yaml
      Targets:
        - Arn: !Ref CheckExpiryFunctionAliaslive
          Id: ExpiryCheckTarget
```

Same edit for `StaleNudgeSchedule` (line 1361) → `!Ref StaleNudgeFunctionAliaslive`. Same for `FollowUpSchedule` (line 1381) → `!Ref FollowUpReminderFunctionAliaslive`.

- [ ] **Step 5: Repoint 3 EventBridge Lambda Permissions**

`ExpiryCheckPermission` (line 1344): change `FunctionName: !Ref CheckExpiryFunction` to `FunctionName: !Ref CheckExpiryFunctionAliaslive`. Same for `StaleNudgePermission` (line 1364) and `FollowUpPermission` (line 1384).

- [ ] **Step 6: Validate and deploy**

```bash
sam validate --lint
git add template.yaml && git commit -m "feat(infra): canary on 12 pipeline-tier functions (linear shift)

AutoPublishAlias + Linear10PercentEvery1Minute for load-config, merge-dedup,
score-batch, aggregate-scores, find-contacts, save-metrics, send-email,
post-score, self-improve, check-expiry, stale-nudges, followup-reminders.
Step Functions DefinitionString + StepFunctionsRole IAM + EventBridge
schedules and permissions repointed at :live."
git push origin claude/objective-sanderson-eeedca
gh workflow run deploy.yml --ref claude/objective-sanderson-eeedca
gh run watch $(gh run list --workflow=deploy.yml --limit=1 --json databaseId -q '.[0].databaseId') --exit-status
```

Expected: deploy succeeds. CFN parallelism means total wall time is typically 10-15 min (the 10-min linear shift sets the floor; multiple functions shift concurrently). The current `deploy.yml` 30-min timeout still has room; Task 9 raises it to 45 min for headroom on full-stack changes.

- [ ] **Step 7: Verify aliases for all 12 pipeline-tier functions**

```bash
for fn in naukribaba-load-config naukribaba-merge-dedup naukribaba-score-batch naukribaba-aggregate-scores \
          naukribaba-find-contacts naukribaba-save-metrics naukribaba-send-email naukribaba-post-score \
          naukribaba-self-improve naukribaba-check-expiry naukribaba-stale-nudges naukribaba-followup-reminders; do
  v=$(aws lambda get-alias --function-name $fn --name live --region eu-west-1 --query FunctionVersion --output text 2>&1)
  echo "$fn → $v"
done
```

Expected: 12 lines with numeric versions.

- [ ] **Step 8: Trigger one Step Functions execution to confirm pipeline still works**

```bash
SM_ARN=$(aws cloudformation describe-stacks --stack-name job-hunt-api --region eu-west-1 \
  --query "Stacks[0].Outputs[?OutputKey=='DailyPipelineArn'].OutputValue" --output text)
aws stepfunctions start-execution --state-machine-arn $SM_ARN --input '{"user_id":"default"}' --region eu-west-1
```

Watch in Step Functions console. Expected: `LoadUserConfig` first step now invokes `naukribaba-load-config:live` and succeeds. (If it fails downstream at scrapers, that's OK — scrapers are still on `.Arn` until Task 8.)

---

## Task 8: Roll out Read-only / idempotent tier (AllAtOnce) to 13 remaining functions (~25 min)

`WsConnectFunction` and `WsDisconnectFunction` already got Snippet C in Task 4. The remaining 13 functions:

```
ScrapeApifyFunction (90), ScrapeAdzunaFunction (104), ScrapeHNFunction (118),
ScrapeYCFunction (132), ScrapeLinkedInFunction (148), ScrapeIndeedFunction (162),
ScrapeGlassdoorFunction (176), ScrapeIrishFunction (190), ScrapeGreenhouseFunction (204),
ScrapeAshbyFunction (218), ScrapeContactsFunction (232), ChunkHashesFunction (289),
NotifyErrorFunction (450)
```

**Files:**
- Modify: `template.yaml` — add Snippet C to 13 functions; repoint Step Functions

- [ ] **Step 1: Apply Snippet C to all 13 functions**

Insert Snippet C after each function's `MemorySize:`.

- [ ] **Step 2: Repoint Step Functions DefinitionString for the SF-invoked subset**

Change `.Arn` → `.Alias` at:

| `<FunctionLogicalId>` | Lines |
|---|---|
| `ScrapeLinkedInFunction` | 753 |
| `ScrapeIndeedFunction` | 776 |
| `ScrapeHNFunction` | 810 |
| `ScrapeYCFunction` | 830 |
| `ScrapeIrishFunction` | 862 |
| `ScrapeGreenhouseFunction` | 883 |
| `ScrapeAshbyFunction` | 903 |
| `ChunkHashesFunction` | 929 |
| `NotifyErrorFunction` | 1147 |

`ScrapeApifyFunction`, `ScrapeAdzunaFunction`, `ScrapeContactsFunction`, `ScrapeGlassdoorFunction` are not in either DefinitionString. Verify with:

```bash
grep -n "ScrapeApifyFunction.Arn\|ScrapeAdzunaFunction.Arn\|ScrapeContactsFunction.Arn\|ScrapeGlassdoorFunction.Arn" \
  /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/template.yaml
```

Expected: only matches inside `StepFunctionsRole` policy (line ~666-676), not inside `DefinitionString`. If grep finds matches inside the DefinitionString, also change them to `.Alias`.

- [ ] **Step 3: Update `StepFunctionsRole` IAM**

Add to the `Resource:` list:

```yaml
                  - !Ref ScrapeApifyFunctionAliaslive
                  - !Ref ScrapeAdzunaFunctionAliaslive
                  - !Ref ScrapeHNFunctionAliaslive
                  - !Ref ScrapeYCFunctionAliaslive
                  - !Ref ScrapeLinkedInFunctionAliaslive
                  - !Ref ScrapeIndeedFunctionAliaslive
                  - !Ref ScrapeGlassdoorFunctionAliaslive
                  - !Ref ScrapeIrishFunctionAliaslive
                  - !Ref ScrapeGreenhouseFunctionAliaslive
                  - !Ref ScrapeAshbyFunctionAliaslive
                  - !Ref ScrapeContactsFunctionAliaslive
                  - !Ref ChunkHashesFunctionAliaslive
                  - !Ref NotifyErrorFunctionAliaslive
```

- [ ] **Step 4: Verify `LambdaInvokePolicy` on `SelfImproveFunction` covers the alias**

`SelfImproveFunction` at line 447 has `LambdaInvokePolicy: FunctionName: naukribaba-notify-error`. The string-based form generates an IAM policy with a `*` suffix on the function ARN, covering both `$LATEST` and `:live`. After deploy, verify:

```bash
ROLE=$(aws lambda get-function --function-name naukribaba-self-improve --region eu-west-1 \
  --query 'Configuration.Role' --output text | awk -F/ '{print $NF}')
aws iam list-role-policies --role-name $ROLE --query 'PolicyNames'
aws iam get-role-policy --role-name $ROLE --policy-name <policy-from-above> --query 'PolicyDocument.Statement[].Resource'
```

Expected: the policy includes a resource ending in `function:naukribaba-notify-error*`. If it ends without the `*`, edit to `FunctionName: naukribaba-notify-error:live`.

- [ ] **Step 5: Validate and deploy**

```bash
sam validate --lint
git add template.yaml && git commit -m "feat(infra): canary AllAtOnce on 13 read-only/idempotent functions

AutoPublishAlias for the 11 scrapers + chunk-hashes + notify-error.
AllAtOnce is documented per roadmap (no traffic-shift value for read-only,
but explicit choice in template). Step Functions repointed at :live.

Phase 2 canary roll-out is now complete: 32/33 functions on canary
(JobHuntApi container Lambda deferred — see ADR)."
git push origin claude/objective-sanderson-eeedca
gh workflow run deploy.yml --ref claude/objective-sanderson-eeedca
gh run watch $(gh run list --workflow=deploy.yml --limit=1 --json databaseId -q '.[0].databaseId') --exit-status
```

Expected: deploy succeeds (~5 min — AllAtOnce is fast).

- [ ] **Step 6: Verify all 32 aliases exist**

```bash
for fn in naukribaba-scrape-apify naukribaba-scrape-adzuna naukribaba-scrape-hn naukribaba-scrape-yc \
          naukribaba-scrape-linkedin naukribaba-scrape-indeed naukribaba-scrape-glassdoor \
          naukribaba-scrape-irish naukribaba-scrape-greenhouse naukribaba-scrape-ashby \
          naukribaba-scrape-contacts naukribaba-load-config naukribaba-merge-dedup \
          naukribaba-score-batch naukribaba-chunk-hashes naukribaba-aggregate-scores \
          naukribaba-tailor-resume naukribaba-compile-latex naukribaba-generate-cover-letter \
          naukribaba-find-contacts naukribaba-save-job naukribaba-save-metrics \
          naukribaba-send-email naukribaba-post-score naukribaba-self-improve \
          naukribaba-notify-error naukribaba-check-expiry naukribaba-stale-nudges \
          naukribaba-followup-reminders naukribaba-ws-connect naukribaba-ws-disconnect \
          naukribaba-ws-route; do
  v=$(aws lambda get-alias --function-name $fn --name live --region eu-west-1 --query FunctionVersion --output text 2>&1)
  echo "$fn → $v"
done | tee /tmp/canary-aliases.txt
```

Expected: 32 lines, no errors. Save the output for the ADR.

---

## Task 9: Update `.github/workflows/deploy.yml` — timeout 45min + `--no-disable-rollback` (~10 min)

**Files:**
- Modify: `.github/workflows/deploy.yml`

`--no-disable-rollback` is the default for SAM but can be overridden via `samconfig.toml` (Phase 3 territory). Setting it explicit here ensures Phase 3's parameterization can't accidentally regress it.

- [ ] **Step 1: Change `timeout-minutes: 30` to `45`**

Line 9 of `.github/workflows/deploy.yml`:

```yaml
jobs:
  deploy:
    runs-on: ubuntu-latest
    timeout-minutes: 45
```

- [ ] **Step 2: Add `--no-disable-rollback` to the `sam deploy` block**

In the `sam deploy` block (lines 67-88), add the flag alongside the existing `--no-confirm-changeset` etc:

```yaml
          sam deploy \
            --stack-name job-hunt-api \
            --region eu-west-1 \
            --capabilities CAPABILITY_IAM CAPABILITY_NAMED_IAM \
            --image-repository 385017713886.dkr.ecr.eu-west-1.amazonaws.com/job-hunt-api \
            --s3-bucket utkarsh-job-hunt \
            --s3-prefix sam-artifacts \
            --no-confirm-changeset \
            --no-fail-on-empty-changeset \
            --no-disable-rollback \
            --parameter-overrides \
              "GroqApiKey=${GROQ_KEY}" \
              ...
```

- [ ] **Step 3: Validate the workflow YAML**

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca && \
  python -c "import yaml; yaml.safe_load(open('.github/workflows/deploy.yml'))" && echo OK
```

Expected: `OK`.

- [ ] **Step 4: Commit and push**

```bash
git add .github/workflows/deploy.yml && git commit -m "chore(deploy): bump timeout 30→45min, set --no-disable-rollback explicit

Canary deploys can take 15+min when multiple Lambdas are shifting in
parallel. 45min gives headroom for full-stack changes.

--no-disable-rollback is the SAM default but setting it explicit guards
against future samconfig.toml regressions (Phase 3 will parameterize)."
git push origin claude/objective-sanderson-eeedca
```

---

## Task 10: ADR — `docs/superpowers/specs/2026-04-27-canary-strategy-decision.md` (~15 min)

**Files:**
- Create: `docs/superpowers/specs/2026-04-27-canary-strategy-decision.md`

Short ADR — references the roadmap rather than re-justifying decisions. Records four phase-specific judgement calls made during plan execution. Use the structure below; each section is 1-3 short paragraphs.

- [ ] **Step 1: Write the ADR with these required sections**

```
# ADR — Lambda Canary Strategy
Date: 2026-04-27 — Status: Accepted (Phase 2)
Roadmap: docs/superpowers/plans/2026-04-27-deployment-safety-roadmap.md

## Context
Phase 2 requires Lambda canary deploys with auto-rollback. Tier choices and alarm
thresholds are locked in the roadmap; this ADR records the 4 delegated judgement
calls made during plan execution.

## Decision 1: AllAtOnce is set explicitly (not implicit via omission)
SAM default is AllAtOnce; setting it explicitly makes the choice reviewable. Required
by roadmap success criterion ("so the choice is documented, not implicit").

## Decision 2: monitoring/alarms.yaml is inlined into template.yaml
Considered Fn::Transform: AWS::Include — rejected (S3 staging dependency). Phase 4
introduces enough alarms to justify a CFN macro; defer the macro pivot until then.
alarms.yaml is canonical authoring location; template.yaml mirrors it; on edit, change
both. Bounded duplication (15 resources).

## Decision 3: JobHuntApi (FastAPI container Lambda) deferred from Phase 2
Roadmap's tier list does not enumerate it. PackageType: Image deploys interact with
ECR lifecycle in ways the roadmap does not specify; a stuck-rollback risk if the prior
image tag is overwritten. Action: roadmap follow-up to design image-tag versioning
(content-addressable digest vs mutable :latest, ECR TTL). Until then, deploys via
standard CFN update.

## Decision 4: StepFunctionsRole IAM keeps both .Arn and alias refs during rollout
Tasks 6/7/8 progressively migrate functions to aliases. Keeping both ARN forms in
the IAM Resource list avoids cross-task coupling — each deploy is independent. Drop
the unqualified ARNs as a fast-follow.

## Why not blue/green for everything?
Blue/green doubles cost during the window and needs a load balancer SAM doesn't
provide for Lambda. CodeDeploy canary captures 90% of the value at 0% extra cost.

## Why is AllAtOnce safe for read-only / idempotent?
Scrapers write to staging tables (re-runnable). WS connect/disconnect rows are
idempotent on connectionId. ChunkHashes is pure. NotifyError's worst case is a
duplicate email, not data corruption. None have failure modes where traffic-level
granularity helps. Saves 5min × 15 functions = 75min of canary wait per deploy.

## Drill evidence (Task 5 of plan)
[Paste output of `aws cloudwatch describe-alarm-history` from Task 5 Step 10 here.]
[Paste alias version + deploy run ID from Task 5 Step 7-8 here.]

## Consequences
- 32/33 Lambdas now deploy via CodeDeploy (JobHuntApi excepted)
- Regression in any critical-tier fn auto-rolls back within ~6min
- Full-stack canary deploys: ~15-20min wall-clock (within deploy.yml 45min timeout)
- Phase 4 alarms layer onto existing DeploymentPreference.Alarms without restructure
- Phase 6 PreTraffic hook plugs into DeploymentPreference.Hooks.PreTraffic (empty today)
```

- [ ] **Step 2: Commit and push**

```bash
git add docs/superpowers/specs/2026-04-27-canary-strategy-decision.md && \
git commit -m "docs(adr): canary strategy decision record (Phase 2)" && \
git push origin claude/objective-sanderson-eeedca
```

---

## Task 11: Open the PR (~10 min)

**Files:** none — git only.

- [ ] **Step 1: Check for PR template**

```bash
ls /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca/.github/PULL_REQUEST_TEMPLATE.md 2>/dev/null
```

If the file doesn't exist, follow recent PRs (e.g. PR #10) for tone.

- [ ] **Step 2: Open PR**

Use the title `feat(infra): Phase 2 — Lambda canary deploys via SAM DeploymentPreference` and a body covering:

- **Summary**: 32 in-scope Lambdas get AutoPublishAlias + tiered DeploymentPreference (5 critical Canary, 12 pipeline Linear, 15 read-only AllAtOnce); WS / Step Functions / EventBridge repointed at `:live`; `deploy.yml` timeout 30→45min + `--no-disable-rollback`; ADR linked.
- **Drill evidence**: cite Task 5's CodeDeploy rollback timing (T+90s alarm trip, T+~4min full rollback) and `aws cloudwatch describe-alarm-history` snapshot; note drill commit reverted.
- **Compatibility notes for Phase 3 / 4 / 6**: copy verbatim from the "Cross-Phase Coordination" section of this plan.
- **Test plan checklist**: copy from the "Self-Review" spec coverage list.

```bash
cd /Users/ut/code/naukribaba/.claude/worktrees/objective-sanderson-eeedca && \
gh pr create --title "feat(infra): Phase 2 — Lambda canary deploys via SAM DeploymentPreference" --body-file /tmp/pr-body.md
```

(Author the body in `/tmp/pr-body.md` first to avoid HEREDOC escaping issues with `${Stage}` references.)

- [ ] **Step 3: After CI green, merge via gh API** (per memory: `gh pr merge` with worktree fails)

```bash
gh api -X PUT "repos/UT07/daily-job-hunt/pulls/<NUMBER>/merge" -f merge_method=squash
```

---

## Cross-Phase Coordination

### Phase 3 (Staging) compatibility note
When `template.yaml` is parameterized with `Stage` (`staging` | `prod`), all alarm names must include `${Stage}` to avoid duplicate-alarm-name conflicts at deploy time. Phase 3 plan must:

1. Suffix every `AlarmName` in `monitoring/alarms.yaml` and `template.yaml`'s inlined alarm blocks with `-${Stage}`.
2. Verify a deliberate failure in staging triggers staging-only alarms (not prod).
3. Re-run a scaled-down version of Task 5's drill against staging on first deploy.

### Phase 4 (Observability) compatibility note
Pipeline-tier functions currently have **no alarms** in `DeploymentPreference.Alarms`. Phase 4 will:

1. Define EMF-backed composite alarms (e.g. `apply_failed_rate > 20% over 5min`) per the roadmap's Phase 4 section.
2. Append the new alarm `!Ref`s to each pipeline-tier function's existing `DeploymentPreference.Alarms` list.
3. Optionally upgrade Critical-tier alarms from raw `Errors > 0` to composite EMF alarms — but the existing alarm names must remain green-able, so any replacement must keep the alarm-name stable or update the `DeploymentPreference.Alarms` `!Ref` simultaneously.

### Phase 6 (Smoke) compatibility note
`DeploymentPreference.Hooks.PreTraffic` is intentionally **left empty** in Phase 2. Phase 6 plugs in the `naukribaba-canary-prehook` Lambda there:

```yaml
      DeploymentPreference:
        Type: Canary10Percent5Minutes
        Alarms:
          - !Ref WsRouteFunctionErrorsAlarm
          ...
        Hooks:
          PreTraffic: !Ref CanaryPreHookFunctionAliaslive  # ← Phase 6 adds this
```

No restructure of Phase 2's tier blocks needed.

### Independence from other phases
Phase 2 uses **default Lambda metrics only** (`AWS/Lambda/Errors`, `AWS/Lambda/Throttles`, `AWS/Lambda/Duration`). No dependency on Phase 4's structlog / EMF / X-Ray. Phase 2 can ship independently and benefits the system from day one.

---

## Self-Review

**Spec coverage check** (against roadmap Phase 2 section):

- [x] AutoPublishAlias: live on every (in-scope) function — Tasks 2, 3, 6, 7, 8
- [x] DeploymentPreference per tier — Tasks 3, 6, 7, 8
- [x] API Gateway / WS / Step Functions / EventBridge integrations point at `:live` — Tasks 4, 6, 7, 8
- [x] `monitoring/alarms.yaml` created — Task 1
- [x] 3 alarms per critical-tier function — Tasks 1, 3, 6
- [x] `deploy.yml`: timeout 30→45min + `--no-disable-rollback` — Task 9
- [x] ADR `docs/superpowers/specs/2026-04-27-canary-strategy-decision.md` — Task 10
- [x] Step-1 sanity (one function first) — Task 2
- [x] Roll-out drill (deliberate failure → auto-rollback) — Task 5
- [x] Sequential roll-out: critical → pipeline → AllAtOnce — Tasks 6, 7, 8
- [x] Cross-phase coordination notes (3, 4, 6) — Cross-Phase section

**Placeholder scan:** no TBD / TODO / "appropriate alarms" / "fill in" — every snippet is shown literally; every shell command is exact; expected outputs are stated.

**Type / name consistency:** Function logical IDs (`WsRouteFunction`, `TailorResumeFunction`, etc.) and `FunctionName`s (`naukribaba-ws-route`, etc.) are checked line-by-line against `template.yaml` in the inventory. SAM auto-generated alias logical IDs follow `<FunctionLogicalId>Aliaslive` consistently across Tasks 4, 6, 7, 8. Alarm logical IDs follow `<FunctionLogicalId>{Errors,Throttles,DurationP99}Alarm` consistently across Tasks 1, 3, 6.

**Realism:** ~half day of focused work, ~5 deploys totaling ~45min wall-clock waiting on CFN. Drill in Task 5 is the highest-risk step but is deliberately scoped to one function so it's contained.

**Out-of-scope items confirmed deferred:** JobHuntApi container Lambda (Decision 3 in ADR), pipeline-tier alarms (Phase 4), PreTraffic smoke hooks (Phase 6), staging-stage alarm-name suffixing (Phase 3).

---

## Execution Handoff

**Plan complete and saved to `docs/superpowers/plans/2026-04-27-deployment-safety-phase2-canary.md`. Two execution options:**

**1. Subagent-Driven (recommended)** — Dispatch a fresh subagent per task; review between tasks. Tasks 5 (drill) and 9 (workflow change) are the highest-risk steps and benefit from isolated sessions.

**2. Inline Execution** — Execute tasks in this session via `superpowers:executing-plans`. Faster but less guarded.

**Which approach?**
