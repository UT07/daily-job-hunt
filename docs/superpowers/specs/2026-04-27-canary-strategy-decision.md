# ADR — Lambda Canary Strategy

Date: 2026-04-27 — Status: Accepted (Phase 2)
Roadmap: `docs/superpowers/plans/2026-04-27-deployment-safety-roadmap.md`
Plan: `docs/superpowers/plans/2026-04-27-deployment-safety-phase2-canary.md`

## Context

Phase 2 of the deployment-safety roadmap introduces Lambda canary deploys with
auto-rollback via SAM `DeploymentPreference` + AWS CodeDeploy. Tier choices
(Critical / Pipeline / Read-only) and alarm thresholds are locked in the
roadmap; this ADR records the four delegated judgement calls made during plan
execution and one important deferral.

## Decision 1: `AllAtOnce` is set explicitly (not implicit via omission)

`AllAtOnce` is the SAM default if `DeploymentPreference` is omitted, but the
roadmap requires the choice be **reviewable in the template**. Setting it
explicitly:

- Documents intent (otherwise readers have to know the default).
- Forces a tier decision when a new Lambda is added.
- Lets future tooling (Phase 4 dashboards, Phase 6 PreTraffic hooks) discover
  the tier from the template alone.

Cost: 13 extra `DeploymentPreference: { Type: AllAtOnce }` blocks; ~26 lines.
Worth it.

## Decision 2: `monitoring/alarms.yaml` is inlined into `template.yaml`

Considered three structures:

1. `Fn::Transform: AWS::Include` from `template.yaml` to
   `monitoring/alarms.yaml`. Rejected — adds an S3 staging dependency
   (`AWS::Include` resolves the URL at template-deploy time, requires the
   bucket to be reachable from the deployer's region/role).
2. CFN macro that expands a custom shorthand. Rejected for Phase 2 — adds a
   deploy-time Lambda dependency the roadmap does not call for; useful when
   Phase 4 expands the alarm set 3-4×, not now.
3. Manual inline copy with a comment pointing at the canonical authoring
   location. **Accepted.**

`monitoring/alarms.yaml` is the canonical source; `template.yaml` mirrors the
15 alarm blocks. On any edit, change both. Bounded duplication (15 resources
today) makes the manual sync cheap; Phase 4 will reach the duplication budget
where a CFN macro becomes cheaper than the manual sync.

## Decision 3: `JobHuntApi` (FastAPI container Lambda) deferred from Phase 2

The roadmap's tier list does not enumerate `JobHuntApi`. It is defined with
`PackageType: Image`, which interacts with CodeDeploy traffic-shifting
differently from `PackageType: Zip`:

- Image versions are content-addressable by digest, not auto-incremented.
- ECR lifecycle policy must guarantee the prior image stays accessible for
  rollback. If `:latest` is mutable and overwritten between deploys, a
  CodeDeploy rollback cannot find a target image.
- A stuck-rollback risk if the prior digest is GC'd by ECR before the alarm
  window closes.

Action: roadmap follow-up is required to design image-tag versioning
(content-addressable digest references vs mutable `:latest`, ECR retention
policy, and a `BeforeAllowTraffic` hook that pre-warms the image). Until that
lands, `JobHuntApi` continues to deploy via standard CFN update with no canary
gate.

The 32 in-scope functions (5 Critical, 12 Pipeline, 15 Read-only) cover
every other Lambda in the stack.

## Decision 4: `StepFunctionsRole` IAM keeps both `.Arn` and alias `!Ref`s

Tasks 6 / 7 / 8 progressively migrate functions onto aliases. Step Functions
invokes the **alias** ARN at runtime once a state's `Resource` is rewritten
from `${Fn.Arn}` to `${Fn.Alias}`, but the existing IAM resource list
references `!GetAtt Fn.Arn` (the unqualified ARN).

Two options:

1. Replace `.Arn` with alias `!Ref` in lockstep with the DefinitionString
   change. Cleaner end-state, but couples three commits — IAM, function
   block, DefinitionString — into one change-set per function. A partial
   rollout (Critical tier only) means the IAM list lags the DefinitionString
   for pipeline-tier functions, breaking Step Functions executions during
   the rollout window.
2. **Append** alias `!Ref`s alongside existing `.Arn` entries during
   rollout. **Accepted.** Each tier commit stays independent; runtime
   permissions cover both `$LATEST` and `:live` for the duration of the
   migration.

A fast-follow cleanup (post-Phase 2) drops the redundant unqualified `.Arn`
entries and leaves only alias `!Ref`s.

## Why not blue/green for everything?

Blue/green doubles cost during the deployment window and requires a load
balancer that SAM does not provision for Lambda. CodeDeploy canary captures
~90% of the safety value at zero extra cost — the alias swing is atomic and
near-instant.

## Why is `AllAtOnce` safe for read-only / idempotent functions?

- Scrapers write to staging tables that are re-runnable; an in-flight
  invocation that fails after a partial write is replaced by the next
  scheduled run.
- `WsConnectFunction` / `WsDisconnectFunction` rows are idempotent on
  `connectionId`; a duplicate write is a no-op.
- `ChunkHashesFunction` is pure — no side effects beyond return value.
- `NotifyErrorFunction`'s worst case is a duplicate alert email, not data
  corruption.

None of these have a failure mode where traffic-level granularity helps. The
saving is meaningful: ~5 min × 15 functions = 75 min of canary wait per
full-stack deploy. That gain is taken back as deploy-loop velocity.

## Drill evidence (Task 5 of plan — DEFERRED)

This sub-task was deferred from this branch; it requires a live AWS deploy
plus a deliberate-failure injection that this implementation work does not
own. The drill is captured here for the user to execute before merging:

- Deploy this branch via `gh workflow run deploy.yml --ref feat/lambda-canary`.
- Verify aliases via `aws lambda get-alias --function-name naukribaba-ws-route --name live`.
- Inject failure: `raise RuntimeError("drill")` at top of `lambdas/browser/ws_route.py:handler`.
- Re-deploy, watch CodeDeploy console for alarm trip + automatic alias
  rollback.
- Capture `aws cloudwatch describe-alarm-history --alarm-name naukribaba-ws-route-Errors`
  output for the post-merge addendum to this ADR.

## Consequences

- 32 / 33 Lambdas now deploy via CodeDeploy on the next push (`JobHuntApi`
  excepted — see Decision 3).
- A regression in any critical-tier function auto-rolls back the alias
  within ~6 min of the alarm trip.
- Full-stack canary deploys take ~15-20 min wall-clock (within the
  `deploy.yml` 45-min timeout set in Task 9).
- Phase 4 EMF-backed composite alarms layer onto the existing
  `DeploymentPreference.Alarms` lists without restructure.
- Phase 6 PreTraffic smoke hooks plug into the empty
  `DeploymentPreference.Hooks.PreTraffic` field on the Critical tier.
- Phase 3 (staging) must suffix all `AlarmName` and `FunctionName`
  references with `-${Stage}` to avoid duplicate-name conflicts; this is a
  known follow-up captured in the Phase 2 plan's "Cross-Phase Coordination"
  section.

## Open follow-ups

- Drop redundant `!GetAtt Fn.Arn` entries from `StepFunctionsRole` once the
  rollout is live and validated (Decision 4).
- Design `JobHuntApi` image-tag versioning + canary strategy (Decision 3).
- Run the deliberate-failure drill end-to-end and append alarm-history
  evidence to this ADR's "Drill evidence" section.
