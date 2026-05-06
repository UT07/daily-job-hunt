# NaukriBaba — Unified Grand Plan

> # 🟢 ABSOLUTE SOURCE OF TRUTH
>
> **Read this doc first** in every new session, before any other plan/memory file.
>
> All other planning artifacts (per-phase specs, tactical task lists, the Apr 30 memory file `grand_plan_2026_04_30.md`, the per-area `backlog_*.md` memory files, `phase1_followups.md`) are now **superseded by this doc** for status. Those files remain valuable for their detail and history, but **the consolidated Master Backlog and Sequence-to-Beta sections below are the binding view of what's done, what's next, and in what order.**

**Date**: 2026-04-03 · **Last updated**: 2026-05-06 (re-plan after Plan 3c.full ship)
**Status**: Layers 1, 2, 3 ✅ COMPLETE · Layer 2.5 Phase B ✅ COMPLETE (B.7 deferred) · Layer 2.5 Phase A 70% (operator items pending) · **Layer 4 / 3.4 Apply ✅ SHIPPED (pending live smoke)** · **Sequence reset to: Backlog clear → 3.3 Tailor+ → Beta Launch**
**Integration**: career-ops (github.com/santifer/career-ops)
**Supersedes**: v2 design spec (2A-2G), `grand_plan_2026_04_30.md` memory file, individual `backlog_*.md` memory files (preserved for detail; this doc owns priority).

---

## Status Snapshot — 2026-05-06

### What shipped today (in this single session)

| PR | Theme | Outcome |
|----|-------|---------|
| #56 | Plan 3c.full — live cloud-browser streaming UI | Merged + auto-deployed via the new push:main trigger |
| #57 | `deploy.yml` push:main auto-trigger + paths-ignore | Merged. Root cause of "PRs sit on main but never reach Lambda" is now fixed |
| #58 | FinishSetupBanner → Settings + wizard validates required profile fields | Merged. Breaks the "complete profile loop" for users with `onboarding_completed_at` set but missing required fields |
| #59 (open) | Settings handleSave strips read-only fields + `NoticePeriodPicker` dropdown | Open — fixes 422 "email: Extra inputs are not permitted" + adds dropdown UX for notice period |

Also shipped non-PR:
- **`naukribaba-browser:latest` ECR image** built (linux/amd64, 1 GB) and pushed. Smart Apply cloud_browser path now has the Fargate image it needs.
- **Lambda code deployed** for the first time since 2026-05-01 (covers PRs #52, #54, #55, #25, #56). Auto-deploy chain now live.
- **CapSolver API key** confirmed present in SSM at `/naukribaba/capsolver-api-key`.
- **Bug X1 silent compile_latex** was actually shipped earlier (PR #27 on Apr 30, "Phase A: Bug X1 + 5 stabilization fixes") — Phase A.3 is ✅ complete. The grand plan tracking for that was stale.

### Where Layer 2.5 Phase B actually stands (correcting Apr 30 snapshot)

| Sub-phase | Status | Reality vs Apr 30 snapshot |
|-----------|--------|----------------------------|
| B.1 Deploy Readiness CI gate | ✅ Shipped (PRs #11/12/21) | unchanged |
| B.2 Lambda canary deploys | ✅ Shipped (PR #39 on Apr 30, rebased from #15) | Apr 30 snapshot says "Held" — STALE |
| B.3 Staging environment | ✅ Lean version shipped (PR #41 — Netlify deploy previews) | Apr 30 snapshot says "Pending" — STALE |
| B.4 Pattern-catching CI gates | ✅ Most shipped (#26 cluster fixes + #43 lifespan smoke) | Apr 30 snapshot says "Branch ready" — STALE |
| B.5 Pipeline observability (FailState + alarm) | ✅ Shipped (PR #33) | Apr 30 snapshot says "Pending" — STALE |
| B.6 Trimmed observability (X-Ray + dashboard + structlog) | ✅ Shipped (PR #35) | Apr 30 snapshot says "Pending" — STALE |
| B.7 Auto-rollback wiring | 🟡 Deferred | unchanged |

**Phase B is ~95% done.** B.7 is the only meaningful gap and is lower priority since canary + staging together cover most of the failure modes. **Phase B exit criteria: MET.**

### Where Layer 2.5 Phase A actually stands

| Sub-task | Status | Notes |
|----------|--------|-------|
| A.1.1 Rotate Lambda exec-role creds + CloudTrail audit | 🔴 OPERATOR PENDING | Code fix shipped (F1 sanitizer); rotation is your-only |
| A.1.2 EventBridge cron `Input` UUID fix | 🟡 STATUS UNCERTAIN | Need to verify deployed `template.yaml` references `${DailyPipelineUserId}` correctly. Code looks right; verify in CFN |
| A.1.3 Three Plan-3b backfills (eligibility / apply slug / geo+work-auth) | 🟡 PARTIAL | Geo+work-auth shipped via PR #24. Apply-slug backfill: 794 jobs still unclassified — but they fall in the long-tail (Teamtailor/Recruitee/etc), not GH/Ashby. Classifier expansion needed (see Bugs 2026-05-06 below) |
| A.1.4 IAM additions (`ecs:RunTask/StopTask/PassRole`, `WsDisconnect:StopTask`) | 🟡 STATUS UNCERTAIN | Likely shipped via PR #50 ("grant DescribeExecution + raise 502") and/or with Plan 3a infra. Verify against template.yaml |
| A.1.5 `resume_versions UNIQUE` constraint | 🟡 OPERATOR PENDING | Migration file present at `supabase/migrations/20260430_resume_versions_unique.sql`. Run via Supabase dashboard or CLI |
| A.1.6 WS auth token TTL 60s → 5min | 🟡 STATUS UNCERTAIN | Verify in `shared/ws_auth.py` |
| A.1.7 `backfill_missing_artifacts.py` | 🟡 OPERATOR PENDING | Deferred |
| A.2 Session B branch consolidation | ✅ Mostly done via PRs #26, #27 | Cluster-bc-cleanup work shipped |
| A.3 Bug X1 fix | ✅ Shipped (PR #27, Apr 30) | Apr 30 snapshot called this "Highest remaining priority" — DONE |
| A.4 PR #25 postmortem | ✅ Merged today | done |
| A.5 PR #23 verify | ✅ done | unchanged |

**Phase A is ~70% done.** The remaining 30% is operator actions (creds, DB migration) + verification of items that may already be shipped. The architectural narrative is settled; this is execution residue.

### Where Layer 4 / 3.4 Apply actually stands

**Plan 3c.full shipped today (PR #56) means the cloud-browser auto-apply UI is feature-complete.** Backend (3a/3b) + classifier + frontend (3c.full) + Fargate image are all in place. Smart Apply works in:

- **Hand-paste mode** for any platform — modal opens, lists AI-prefilled answers if the platform is Greenhouse/Ashby (preview API), shows EmptyPreviewState otherwise; user clicks Open ATS, marks applied. Submission method `hand_paste` in DB. ✅
- **Cloud-browser mode** for Greenhouse + Ashby jobs — modal calls `/api/apply/start-session`, opens WS to Fargate Chrome, streams screenshots, user clicks "Fill all" or manual click/type, modal records submission. ⚠️ Untested live.

**Three blocking gaps for "Smart Apply works for everything":**
1. **Live runtime smoke**: nobody has watched a real Fargate Chrome session apply to a real Greenhouse/Ashby job through this UI. There may be a runtime bug (IAM, env var, JS handler) that unit tests didn't catch.
2. **`apply_platform` long-tail coverage**: 794 of 921 prod jobs are unclassified because the classifier only handles 10 known ATSes. Adding Teamtailor/Recruitee/BambooHR/Ashby-Hire/Workable/etc would unlock cloud-browser for hundreds more jobs.
3. **Mode 3 assisted-manual fallback** (cloud_browser for unknown platforms via AI-vision form-detection) — explicitly out of scope per Plan 3c.full plan; future work.

### Bugs surfaced 2026-05-06 (new since the Apr 30 snapshot)

These are real bugs found while smoke-testing the post-deploy state. They're not in the Apr 30 snapshot's "still open" list:

| # | Bug | Severity | Fix status |
|---|-----|----------|-----------|
| 1 | `FinishSetupBanner` linked to `/onboarding`, looping users with `onboarding_completed_at` already set | High UX | ✅ PR #58 |
| 2 | Onboarding wizard's "Complete Setup" didn't validate required profile fields → users could finish wizard with blank profile, then loop on the banner | High UX | ✅ PR #58 |
| 3 | `Settings.handleSave` POSTed full profile incl. `email` → 422 "Extra inputs are not permitted" (same bug as wizard's 70a91a5 fix, missed for Settings) | High UX (blocks save) | ✅ PR #59 (open) |
| 4 | Notice Period was free-text only; users had no guidance on format | UX polish | ✅ PR #59 (open) — `NoticePeriodPicker` |
| 5 | `Settings → Save Sources` → 400 "No valid fields. Accepted: [...]" because `enabled_sources` isn't in the backend `_FIELD_MAP` | Functional bug | ❌ Pending |
| 6 | `enabled_sources` toggle UI is a frontend mirage — pipeline scrapers don't actually filter by it. Even if save succeeded, the toggle has no runtime effect | Functional bug | ❌ Pending (separate, larger fix) |
| 7 | `apply_platform` classifier only matches 10 known ATSes; 794 of 921 prod jobs are unclassified (not GH/Ashby/Lever/etc) → Smart Apply cloud_browser doesn't activate for them | Feature gap | ❌ Pending (`shared/apply_platform.py` needs ~10 more regex patterns) |
| 8 | Lazy boto3 in `ai_helper.py` not yet shipped — module-level SSM client at line 13 forces AWS_DEFAULT_REGION on importers | Tech debt | ❌ Pending (`backlog_lazy_boto3.md` from Apr 29) |

### Active sequence (next focus)

**Immediate (this week):**
1. Merge PR #59 (Settings save fix + Notice Period dropdown) — already open
2. Phase A.1 operator actions (creds rotation, DB migration, EventBridge verification)
3. **Live smoke** of Smart Apply on a real Greenhouse/Ashby job → confirm cloud_browser path works end-to-end
4. Bug 5 fix (Save Sources backend) — small migration + `_FIELD_MAP` entry
5. Bug 7 fix — extend `shared/apply_platform.py` with ~10 long-tail ATS patterns + run backfill

**Next major (pick one):**
- **Layer 4 / 3.1 Discover+ — manual JD UI polish** (Add Job page redesign, post-add-job review flow)
- **Layer 4 / 3.2 Research** (CompanyLens, Glassdoor company data, news, salary). This is the largest Layer 4 build remaining.
- **Layer 4 / 3.3 Tailor+** (split-pane LaTeX editor, PDF-to-LaTeX, version history)
- **Phase 2.6 Resume Quality continued** (ATS keyword injection, proof-point extraction — career-ops integration row says "Partial")
- **Phase 2.7 Data Quality continued** (apply_platform classifier expansion is a high-leverage 1-day task that unlocks a lot of Smart Apply value)

---

## 🎯 Sequence to Beta Launch

This is the binding execution order. Items inside each phase can be parallelized, but the phase order is fixed.

### Phase A: Backlog & Bug Clear (immediate, ~1-2 weeks)

Goal: zero known bugs, all backlog items either shipped or explicitly archived. **No new feature work begins until Phase A is clean.** Beta-quality means we can't ship to outside users while known broken things still exist.

See "Master Backlog" section below for the canonical list of what must be cleared.

### Phase B: Tailor+ — User-Controlled Resume Editor (3.3, ~1-2 weeks)

The headline feature for beta. **Why this is the gate to beta launch:** AI-generated resumes aren't 100% reliable. Today users either accept the AI output or click "Regenerate" — neither lets them fix specific things. The only way to ship a beta where users actually trust the output is to give them a real editor where the AI is an assistant, not the author.

Design constraints (binding):
- **AI is an assistant, not the author.** Final say belongs to the user.
- **Inline editing** of every AI-generated bullet/section, not just whole-resume regen.
- **Per-section regenerate** with optional steering ("more impact / shorter / more keyword X").
- **Side-by-side preview** (LaTeX source ↔ rendered PDF) so users see what they're shipping.
- **Version history** — every edit is a saved revision; can roll back.
- **Keyword score** updates live as the user edits (carry over the existing 0-100 ATS score).

Sub-spec to be written: `docs/superpowers/specs/2026-05-XX-tailor-plus-editor-design.md` (incorporates 3.3 Tailor+ row from Layer 4 table + career-ops integration row + the existing "PDF-to-LaTeX, version history" notes).

### Phase C: Beta Launch Readiness (~1 week, parallelizable with end of Phase B)

Before pointing real users at the app, three pillars need to land:

1. **Deploy safety re-assessment** — revisit Layer 2.5 Phase B with beta usage in mind:
   - **B.7 Auto-rollback**: ship now (was deferred). Failing alarm during canary → CodeDeploy reverts.
   - **Preview environment robustness**: today's lean staging is Netlify deploy previews + a preview banner. For beta, we need: Supabase staging project (separate DB), SAM stack stage variable, E2E smoke against staging gating every PR (not just CI green).
   - **Stricter merge policy** ("no bugs shall pass"):
     - All PRs must pass: stale-base-check, lint-and-build, unit-tests, integration-tests, e2e-tests, Deploy Readiness, plus a NEW required CR review or human review checkbox.
     - Auto-rollback wiring for every Lambda (not just the canary tier).
     - PRs that touch hot paths (`app.py`, `lambdas/pipeline/*`, `lambdas/browser/*`, `web/src/components/apply/*`) must run a manual smoke-test step listed in the PR template.

2. **In-app error reporting** — give users a one-click "Report a bug" button that captures:
   - Current URL + auth state + active job context
   - Last 50 console messages (client errors, warnings)
   - Last 20 network requests (path, status, latency)
   - User-supplied free-text description + screenshot
   - Submission method: client → `/api/bug-report` → Supabase `bug_reports` table

3. **Automated bug pipeline** — every report fires a notification + auto-triage:
   - **Sentry** for unhandled exceptions (frontend SDK + backend SDK). Already partially integrated via PostHog error tracking; promote to Sentry where helpful.
   - **PostHog** for user-flow signals (where in the funnel did the user get stuck before reporting).
   - **GitHub issue auto-creation** from `bug_reports` rows: per-report new issue with `bug` + severity labels, assigned to the maintainer.
   - **AI triage Lambda** (optional, post-beta): inspects new bug report, attempts to classify (frontend/backend/data/infra), assigns severity, drafts a fix-PR for trivial cases.
   - **Daily digest** to maintainer email summarizing open bugs.

### Phase D: Beta Launch (1 day)

- Public-facing beta tag at `naukribaba.netlify.app/beta` or new domain
- Onboarding email for first cohort of users
- Active monitoring of bug reports + PostHog funnel
- Daily review of new bugs → triaged + fixed within 24h SLA during beta

### Phase E (Post-beta): Layer 4 remainder — gated on user traction

After beta is live AND we have measurable user activity (target: ≥10 active beta users for ≥2 weeks, ≥50 jobs scored per active user, ≥1 application submitted per active user). The user-traction gate matters because each of these features is high-cost-to-build and only worth shipping if real users will engage with them. Order:

1. **3.1 Discover+ polish** — manual JD UI improvements (Add Job page redesign, post-add-job review flow). Low effort, ships to existing users without needing traction.
2. **3.2 Research (CompanyLens)** — Glassdoor company data, GDELT news, salary ranges, A-F evaluation framework. Highest-impact post-beta feature.
3. **3.6 Analytics dashboard** — funnel viz, score trends, scraper health. Needs the data Phase D collects to be meaningful; ships after beta has accumulated 4+ weeks of data.
4. **3.5 Interview Prep** — coding bank, system design rubrics, STAR stories, mock AI. Independent surface; can ship in parallel with 3.6.

These are all "make the experience richer" — none of them block beta.

---

## 📋 Master Backlog

**Status legend:** 🔴 P0 (blocks beta) · 🟡 P1 (should fix before beta) · 🟢 P2 (nice-to-have, can ship in beta) · ✅ done · 📁 archived (decision: won't fix)

Every item below is currently open or deferred. Items are sourced from: today's session bugs, `phase1_followups.md`, the 8 `backlog_*.md` memory files, and `grand_plan_2026_04_30.md` Phase A.1.

### A1. Operator-only items (you, not me)

| # | Item | Severity | Source |
|---|------|----------|--------|
| A1.1 | 🔴 Rotate Lambda exec-role creds + audit CloudTrail since 2026-04-22 | P0 SECURITY | grand_plan_2026_04_30 A.1.1 |
| A1.2 | 🟡 Verify EventBridge cron uses `${DailyPipelineUserId}` not literal "default" in deployed CFN | P1 | grand_plan_2026_04_30 A.1.2 |
| A1.3 | 🟡 Run `supabase/migrations/20260430_resume_versions_unique.sql` against prod DB | P1 | grand_plan_2026_04_30 A.1.5 |
| A1.4 | 🟡 Verify WS auth token TTL is 5min in deployed `shared/ws_auth.py` | P1 | grand_plan_2026_04_30 A.1.6 |
| A1.5 | 🟢 Run `scripts/backfill_missing_artifacts.py` (re-tailor old jobs missing resume PDFs) | P2 | grand_plan_2026_04_30 A.1.7 |
| A1.6 | 🟡 **Live runtime smoke of Smart Apply cloud_browser** on a real Greenhouse + Ashby job. Confirm Fargate launches, WS streams, Fill all → Submit → record completes | P1 | This session |
| A1.7 | 🟢 Verify `JobHuntApi` IAM has `ecs:RunTask`, `ecs:StopTask`, `iam:PassRole` (likely shipped via #50; verify in template.yaml) | P2 | grand_plan_2026_04_30 A.1.4 |

### A2. Bug fixes (code work)

| # | Item | Severity | Source | Notes |
|---|------|----------|--------|-------|
| A2.1 | 🟡 Save Sources 400 — `enabled_sources` not in backend `_FIELD_MAP` | P1 | Today's session | DB migration + 1-line backend change |
| A2.2 | 🟡 Pipeline doesn't actually filter by `enabled_sources` — toggle UI is a frontend mirage | P1 | Today's session | Each scrape Lambda needs to read user config + early-return if disabled |
| A2.3 | 🟡 `apply_platform` classifier covers only 10 ATSes; 794 of 921 prod jobs unclassified (Teamtailor, Recruitee, BambooHR, Workable, Lever-self-hosted, Smartrecruiters-self-hosted, custom career sites) | P1 | Today's session | Add ~10 regex patterns to `shared/apply_platform.py` + run backfill |
| A2.4 | 🟡 Lazy-init boto3 SSM client in `lambdas/pipeline/ai_helper.py:13` | P1 | `backlog_lazy_boto3.md` | Module-level `boto3.client("ssm")` forces AWS_DEFAULT_REGION on every importer |
| A2.5 | 🟡 LinkedIn/Indeed scrapers returning only known roles (dedup too aggressive or pagination cursor stuck) | P1 | `backlog_linkedin_indeed_dedup.md` | 2-3 hr investigation; possible architectural change to "active tracking" with `last_scraped_at` |
| A2.6 | 🟡 Step Function `Catch → SucceedState` masks all-zero-artifact runs; B.5 alarm partially shipped, verify it actually fires | P1 | `backlog_pipeline_silent_success.md` | Run a synthetic 0-artifact day, confirm alarm fires |
| A2.7 | 🟡 Work-auth scoring: prompt-side fix not yet shipped (post-score cap shipped via PR #24, but score_batch prompt doesn't read user.work_authorizations) | P1 | `backlog_work_auth_scoring.md`, `phase1_followups #6` | AI-cache invalidation cost is the reason it was deferred |
| A2.8 | 🟢 Onboarding wizard doesn't collect `default_referral_source` | P2 | `phase1_followups #7` | Currently dropped from REQUIRED_FIELDS; needs a "How did you hear about us?" field in wizard or Settings |
| A2.9 | 🟢 `AutoApplyButton` uses imperative `document.querySelector` instead of callback props | P2 | `phase1_followups #10` | Code-smell, not a bug |
| A2.10 | 🟢 Onboarding wizard keeps `email` in local state for display; verify the strip-before-PUT stays defensive | P2 | `phase1_followups #9` | Defensive verification, not active bug |

### A3. Test infra gaps

| # | Item | Severity | Source |
|---|------|----------|--------|
| A3.1 | 🟡 Frontend integration test that mounts `<App>` with mocked auth (would have caught 2 prod bugs) | P1 | `phase1_followups #11` |
| A3.2 | 🟡 Backend contract test pinning `application_status="Applied"` Title-Case write | P1 | `phase1_followups #12` |
| A3.3 | 🟢 Pre-commit hook activation + 342-file format-drift cleanup | P2 | Phase 0 follow-up; `dev-setup.sh` already ships, hook is opt-in pending drift cleanup |

### A4. Feature gaps surfaced from prior usage

| # | Item | Severity | Source |
|---|------|----------|--------|
| A4.1 | 🟡 `apply_platform` column has zero population logic for older jobs (related to A2.3 but originally captured 2026-04-26) | P1 | `backlog_apr26_walkthrough.md` |
| A4.2 | 🟡 Resume writing quality — AI strips `\textbf`, adds filler, fabricates skills (continues into 3.3 Tailor+) | P1 | `backlog_apr9_issues.md` |
| A4.3 | 🟡 Cover letters read like LLM prompts ("Hays is a company that specializes in...") — quality gate needed | P1 | `backlog_apr9_issues.md` |
| A4.4 | 🟡 Profile autofill from onboarding resume + cover letter (lower the FinishSetupBanner friction) | P1 | `backlog_profile_autofill.md` |
| A4.5 | 🟡 Score-and-improve loop missing from Lambda (exists locally) | P1 | `backlog_apr9_issues.md` |
| A4.6 | 🟢 Dashboard date-range filter, S+A backfill stalled at 22% coverage | P2 | `backlog_apr9_issues.md` |
| A4.7 | 🟢 Old phase 2A UX polish (status dropdown, job card layout, contacts column width, Add Job page redesign, live pipeline status) | P2 | `backlog_phase2a_remaining.md` |

### Decision: archive these

| # | Item | Why archived |
|---|------|--------------|
| ARCH.1 | 📁 GradIreland scraper | Returns 0 jobs since template change; deprioritized — Greenhouse/Ashby/LinkedIn cover the gap |
| ARCH.2 | 📁 Glassdoor *job* scraping (Fargate + Playwright) | Deprioritized — Greenhouse/Ashby/LinkedIn cover the gap. **Glassdoor *company* data still in scope for 3.2 Research.** |
| ARCH.3 | 📁 DeepSeek free tier | Returns 402 (empty balance); use NVIDIA NIM as the alternative free provider |

### Phase A1+A2+A3+A4 totals

- **Total open**: 27 items (1 P0, 17 P1, 9 P2)
- **Operator-only**: 7 items (A1.1-A1.7) — your action; I cannot do these
- **Code work**: 13 items (A2.1-A2.10, A3.1-A3.3) — I can do all of these
- **Feature gaps**: 7 items (A4.1-A4.7) — mostly P1, some compound with Tailor+

**Phase A exit criteria:** all 🔴 P0 + 🟡 P1 items either shipped or explicitly re-archived. P2 items can ship during beta as bug-pipeline absorbs them.

---

## 🛡️ Layer 5 — Beta Launch Readiness (NEW)

Inserted between Layer 4 (Product Features) and "Live to public" milestone. Beta-launch readiness is a layer of its own because shipping to outside users requires hardening that none of the prior layers individually delivered.

### 5.1 Stricter merge gates ("no bugs shall pass")

| Gate | Status | Plan |
|------|--------|------|
| Stale-base check (PRs >10 commits behind main fail) | ✅ Live (Phase 0 / PR #55) | unchanged |
| Deploy Readiness (sam validate + sam build + layer build + lifespan smoke) | ✅ Live (B.1 / PR #21, #43) | unchanged |
| Unit / lint / integration tests | ✅ Live (test.yml) | unchanged |
| **Required CR review** (CodeRabbit or human) on every PR | 🟡 Pending | Currently CR runs only when manually invoked. Make it auto-run on every PR via `.coderabbit.yaml` or GitHub App. Human reviewer required-checkmark on hot-path PRs. |
| **Hot-path manual smoke step** for PRs touching `app.py`, `lambdas/pipeline/*`, `lambdas/browser/*`, `web/src/components/apply/*` | 🟡 Pending | Add `[ ] Manual smoke checklist completed` to `.github/PULL_REQUEST_TEMPLATE.md`; describe paths-touched logic in CONTRIBUTING.md |
| **No bypass merging** (no admin override, no skip-CI) without postmortem entry | 🟡 Pending | Branch protection rule: required status checks + required reviewer; remove admin merge bypass for `main` |

### 5.2 Auto-rollback (Layer 2.5 B.7 reactivated)

The B.7 work was deferred at end of April; resume it for beta. The hooks: failing CloudWatch alarm during canary → CodeDeploy reverts the Lambda alias to the previous version. Plan was already in `docs/superpowers/plans/2026-04-27-deployment-safety-phase2-canary.md`; reactivate.

### 5.3 Staging environment hardening

Today's lean staging is Netlify deploy previews. For beta we need:
- **Supabase staging project** — separate DB, separate URL. Migrations land there first.
- **SAM stack stage variable** — `--stack-name job-hunt-api-staging` deploys against staging Supabase
- **E2E smoke gating** every PR — Playwright runs against staging URL on every PR, blocks merge on failure (not just CI green)

### 5.4 In-app error reporting

**Frontend:**
- "Report a bug" button — always visible (footer or floating bottom-right)
- Modal captures:
  - User-supplied free-text description (required)
  - Screenshot (optional, via `html2canvas` or browser screenshot API)
  - Auto-captured: current URL, auth state, active job context (if on JobWorkspace), last 50 console messages, last 20 network requests
  - Severity self-rating: "blocking" / "annoying" / "minor"

**Backend:**
- New `bug_reports` Supabase table: `id, user_id, url, description, severity, screenshot_s3_key, console_log_jsonb, network_log_jsonb, auth_state_jsonb, browser_user_agent, created_at, status (new/triaged/fixed/wontfix), github_issue_url`
- New `POST /api/bug-report` endpoint
- New `GET /api/bug-report` (admin) listing reports

**Storage:**
- Screenshots → S3 with 90-day retention, signed URLs only

### 5.5 Automated bug pipeline

**Sentry** as the canonical error tracker:
- Frontend SDK in `web/src/main.jsx` with auto-capture of unhandled exceptions, unhandled promise rejections, React error boundaries
- Backend SDK in `app.py` `_initialize_state` with auto-capture of FastAPI exceptions, structured tags for `user_id`, `path`, `lambda_name`
- Pipeline Lambdas: Sentry + structlog from B.6 already shipped; integrate Sentry alongside

**GitHub issue auto-creation:**
- A new Lambda (`bug-report-router`) consumes `bug_reports` inserts (Supabase Realtime or scheduled cron)
- For each new report: creates a GitHub issue with `bug` + severity labels, links the bug_report row, assigns to the maintainer
- AI triage step (post-beta enhancement): the Lambda calls Claude/Groq to classify the bug (frontend/backend/data/infra), assign severity if user didn't, suggest a likely fix, optionally draft a PR for trivial cases

**Notifications:**
- Sentry → email maintainer on P0 alerts
- GitHub issue → email + Slack webhook (if Slack ever wired)
- Daily digest: cron Lambda summarizes open bugs to maintainer email

### 5.6 Beta launch checklist

Each item must be ✅ before going live:

- [ ] All P0 + P1 items in Master Backlog cleared or explicitly archived
- [ ] 3.3 Tailor+ user-controlled editor live + tested
- [ ] Auto-rollback wired (5.2)
- [ ] Staging environment with E2E smoke gating PRs (5.3)
- [ ] In-app bug-report button visible + working (5.4)
- [ ] Sentry + GitHub issue auto-creation tested with synthetic bug (5.5)
- [ ] **Cost observability live (5.7) — every $ tracked, no surprise bills**
- [ ] Privacy policy / Terms of service pages updated for beta
- [ ] Beta cohort identified (initial 10-50 users)
- [ ] Onboarding email template ready
- [ ] Maintainer SLA agreed: P0 fixed within 24h, P1 within 1 week

### 5.7 Cost Observability

**Why**: as we scale to beta users, per-user variable costs explode. Today there's NO single dashboard that says "this run cost $X" or "user Y has consumed $Z this month". User has reported significant Alibaba Cloud spend on Qwen specifically. Without tracking we can't budget-cap, can't price the eventual paid tier, and can't notice runaway spend until the bill arrives.

**What needs to be tracked:**

| Service | What | Source of truth |
|---------|------|-----------------|
| **Alibaba Cloud / Qwen API** | Per-call cost (input + output tokens) | Alibaba console → CSV export (no API yet); add per-call cost estimate from Qwen pricing in our own ledger |
| **OpenRouter** | Per-call cost | OpenRouter API has a `cost` field in each completion response — already there, just not aggregated |
| **Groq** | Free tier — track usage to catch when we exceed | Groq dashboard (manual), our own request counter |
| **NVIDIA NIM** | Free tier — same as Groq | Manual + counter |
| **Anthropic Claude** | Per-token cost | Anthropic API response includes `usage` block; pipe to ledger |
| **AWS** | Lambda invocations + duration, Fargate task hours, S3 storage + egress, CloudWatch logs, Step Functions transitions, EventBridge, SSM, ECR storage | Cost Explorer API (programmatic) + CloudWatch metrics |
| **Supabase** | DB storage, edge function calls, auth seats, bandwidth | Supabase project usage page (no API yet — manual) |
| **Netlify** | Build minutes, bandwidth, function invocations | Netlify API |
| **Bright Data Web Unlocker** | Per-request cost | Bright Data dashboard CSV |
| **Apify** | Per-actor-run cost (LinkedIn contacts) | Apify API |
| **CapSolver** | Per-captcha-solve cost | CapSolver dashboard |
| **PostHog** | Event volume tier | PostHog plan dashboard |
| **Sentry** | Event volume tier (once integrated, 5.5) | Sentry plan dashboard |

**Architecture:**

1. **Per-call cost ledger** — new Supabase table `cost_events`:
   ```
   id, service (qwen|openrouter|groq|nvidia|claude|apify|bright_data|capsolver|aws_lambda|...),
   user_id (nullable, for shared infra), pipeline_run_id (nullable), feature_area (apply|tailor|score|...),
   input_units (tokens|requests|seconds|bytes), output_units, estimated_cost_usd, currency, created_at
   ```
2. **Lambda emit-cost helper** — every AI call site (`ai_helper.py`, scrapers, tailor, etc.) calls `emit_cost(service, units, estimated_usd)` after each provider call. The helper writes to `cost_events`.
3. **Daily roll-up** — scheduled Lambda computes per-day per-service totals, writes to `cost_summaries`.
4. **Cost dashboard** — `/admin/costs` route in the React app: 3 charts (today / 7-day / 30-day), broken down by service AND by feature area, filterable by user.
5. **Spike alarms** — CloudWatch alarms on aggregate spend (e.g., > $20/day across all services) → email maintainer.
6. **Alibaba Cloud / Qwen audit (immediate)** — before the rest of the cost system ships, do a one-shot audit:
   - How many Qwen calls per day?
   - What's the average tokens per call?
   - Estimated $ per day?
   - Are any callers using Qwen when Groq (free) would have worked? If yes, reroute.
   - Set a budget cap in `ai_helper`: if Qwen-day-spend > $X, fall back to Groq/free providers.

**Sequence:**
- **Immediate (Phase A)**: Qwen audit + budget cap (small, ~half day)
- **Phase C (beta launch prep)**: full cost ledger + dashboard + spike alarms
- **Post-beta**: integrate into 3.6 Analytics dashboard so users can see their own per-job cost (would-be-paid-tier framing)

---

## Status Snapshot — 2026-04-30 (historical)

### Production state

- **Backend**: Live in `eu-west-1` since 2026-04-21. SAM deploys via `deploy.yml` from `main` after Deploy Readiness gate. Daily pipeline runs weekdays 07:00 UTC via EventBridge → Step Functions.
- **Frontend**: Live on Netlify, auto-deployed from `main`.
- **Database**: Supabase prod with RLS; ~850 jobs across all sources; auto-apply tables in place.
- **Auto-apply backend**: Cloud-browser pipeline shipped through Plan 3b (PR #17). Plan 3c frontend not started.

### Done since the last snapshot (2026-04-06 → 2026-04-30)

**Auto-apply / cloud-browser pipeline (Phase 3.4 sub-plans):**

- PR #5 (Apr 21) — CFN EventBridge ARN fix that unblocked the deploy workflow
- PR #7 (Apr 22) — **Plan 2 browser session** + Fargate task def
- PR #8 (Apr 24) — **Plan 3a WebSocket + apply endpoints** (3 WS Lambdas, 5 `/api/apply/*` endpoints, idempotent record)
- PRs #10/11/12 (Apr 27) — **Apply platform classifier** + Deploy Readiness CI gate + `Dockerfile.lambda` `shared/`/`lambdas/` COPY fix; eligibility gate flipped from `apply_platform` to `apply_url`; 831 jobs backfilled; live eligibility >0 for the first time
- PR #17 (Apr 29) — **Plan 3b backend**: AI preview, platform metadata fetchers (Greenhouse/Ashby), question classifier
- PR #21, #22 (Apr 29) — Plan 3b hotfixes (`lambdas/` COPY, JSONB string handling in `get_preview_cache`)

**Backlog clearances:**

- PR #23 (Apr 29) — Lazy boto3 SSM client in `ai_helper`, drops AWS_DEFAULT_REGION import-time dependency
- PR #24 (Apr 29) — Geography + work-auth aware score cap; demotes wrongly-S-tier UK/US-visa-required jobs

**Deploy-safety + prod-health initiative (parallel session):**

- PR #14 (Apr 28) — **Deployment-safety roadmap**: master spec + 6 phase sub-plans (canary, staging, observability, auto-rollback)
- PR #16 (Apr 29) — Bug 1: pipeline status ARN reconstruction (kills "Poll failed: HTTP 404")
- PR #18 (Apr 29) — Bug 3+4: JD location plumbing through 5 request models + `_Job`
- PR #19 (Apr 29) — Bug 2: rename "Score Resume" → "Save & Score"
- PR #20 (Apr 29) — Bug 5+6: SFN `job_hash` plumbing + `score_batch` prompt fields
- PR #25 (in review, Apr 29) — Postmortem of Apr 22-29 prod-health incident (doc-only)

### Findings still open (priority order)

1. **🔴 P0 SECURITY — AWS STS tokens leaked into `pipeline_tasks.error`** via `str(e)` flattening of boto3 `ClientError`; tokens were user-visible via `GET /api/tasks/{id}` and rendered as resume content. Code fix (creds sanitizer F1) is in `fix/comprehensive-prod-health/artifact-pipeline` branch. **Operator action required**: rotate Lambda execution-role creds + audit CloudTrail since 2026-04-22.
2. **🔴 EventBridge cron `Input` fix** — `template.yaml` daily cron uses `Input: {"user_id":"default"}`. New jobs land under synthetic user; real user never sees them. Root cause of "no artifacts since Apr 22." **Operator + template.yaml fix.**
3. **🔴 Bug X1 — silent `compile_latex` failures**: returns error dict (no `pdf_s3_key`) on tectonic failure → `save_job` silently sets `application_status="scored"` with no `resume_s3_url`. Pairs with Bug X2 (header-marker validation falling back to base resume) for "regenerate produces same resume." **Code fix not yet shipped.**
4. **8 cross-cutting bug patterns** documented in Session B's audit (`docs/audit/2026-04-29-deep-pass-2.md`, 45 findings: 11 P0, 25 P1):
   1. Pydantic `extra='ignore'` field-strip-on-undeclared
   2. `str(e)` AWS error body leakage into user-visible fields
   3. Frontend↔backend route drift (e.g. `ResumeEditor.jsx` typo'd `/api/resume/upload-pdf`)
   4. Silent UI error swallows (`.catch(err => console.error)` 12+ sites)
   5. Lambda↔local code drift (Lambda `tailor_resume` lacks guards from local `tailorer`)
   6. Hardcoded user info ("Utkarsh / Stamp 1G / 254utkarsh@gmail.com" in 3+ places — multi-tenant blockers)
   7. EventBridge↔SFN input-contract drift
   8. Missing IAM policies (`ecs:RunTask`, `ecs:StopTask`, `iam:PassRole`)
5. **Infrastructure follow-ups**: `JobHuntApi` IAM, `WsDisconnect` `ecs:StopTask` (Fargate task leak / cost runaway), `resume_versions` `UNIQUE(user_id, job_id, version_number)`, WS auth token TTL 60s → 5min.

### Active sequence

Layer 2.5 Phase A (Stabilization) → Layer 2.5 Phase B (Deploy Safety, B.1–B.7) → Layer 4 / Plan 3c (Frontend Auto-Apply UI). See **Layer 2.5** below for the architectural form; see `memory/grand_plan_2026_04_30.md` for tactical task breakdown.

---

## Why This Document Exists

Multiple overlapping specs accumulated with inconsistent phase numbering:

- v2 design spec (2026-03-30) defined phases 2A–2G
- Testing spec (2026-03-31) defined 7 QA tiers
- Playwright migration spec (2026-04-01) defined Phase 2.5
- Quality pipeline spec (2026-04-03) defined Phases 2.6–2.9
- Cloud-browser auto-apply spec (2026-04-12) added Plan 2/3a/3b/3c
- Deployment-safety spec (2026-04-28, PR #14) added Phases B.1–B.7

Actual work diverged from the original 2A–2G plan because the pipeline needed reliability fixes before features made sense, then a deploy-safety net before more product features could land safely. This document is the single source of truth for what's done, what's next, and how it all connects.

---

## Architecture

```
React Frontend (Netlify)
       │ REST + WebSocket
AWS Step Functions (orchestration)
       │
Lambda Functions (compute) ──── Fargate (cloud-browser auto-apply)
       │
Supabase PostgreSQL + S3 Storage
```

- No n8n. Step Functions orchestrates the daily pipeline.
- Lambda for all compute (scrapers, AI, compilation).
- Fargate hosts the cloud-browser auto-apply task; was originally scoped for Glassdoor scraping (deprioritized).
- `main.py` for local development runs.
- GitHub Actions for CI/CD; SAM-based deploy via `deploy.yml`.

---

## Grand Phase Structure

### Layer 1: Foundation — ✅ COMPLETE

| Phase | Name | Status | What Was Built |
|-------|------|--------|----------------|
| 1.0 | Core Pipeline | ✅ Done | Scrapers, AI matching, LaTeX PDFs, multi-provider failover, SQLite cache |
| 2.0 | Landing Page | ✅ Done | FastAPI backend, React frontend, SAM template, GCP/Drive integration |
| 2.5 | Web Unlocker | ✅ Done | LinkedIn, Indeed, Irish portals via Bright Data Web Unlocker on Lambda |

---

### Layer 2: Reliability — ✅ COMPLETE (2026-04-29)

| Sub-phase | What shipped | When |
|-----------|--------------|------|
| 2.5b Scraper fixes | OpenRouter free models (5 verified), AI council expanded 18 → 32 providers, IrishJobs JSON-LD descriptions, Groq IP-block workaround. GradIreland fix deferred. Glassdoor deprioritized (Greenhouse/Ashby/LinkedIn cover). | 2026-04-05 → ongoing |
| 2.6 Writing quality | Compilation fallback (page-length validation, header-marker check), AI council retry on dead providers, hard-gate substring matching, LaTeX brace/macro sanitization | 2026-04-09 (PR #2 marathon) |
| 2.7 Data quality | Canonical hash dedup (177 → 159 jobs), deterministic 3-call median scoring, `score_status` tracking, cross-source dedup audit | 2026-04-05 → 2026-04-06 |
| 2.8 QA foundation | 712+ tests in CI, fixtures, golden dataset; Tier 4b/4c data + writing tests | 2026-04-09 |
| 2.9 Self-improvement | Council retry logic, prompt versioning, pipeline metrics → Supabase, score recalibration | 2026-04-09 |
| 2.10 Score tiering | `score_tier` column + thresholds + index, downstream Lambda gating, **geography + work-auth post-score cap** | 2026-04-05 → 2026-04-29 (PR #24) |

Some sub-phases continue to receive incremental hardening as patterns surface (e.g. Lambda↔local code drift caught in Session B's audit lands in Layer 2.5 Phase A.2). The original Layer 2 parallel-execution diagram is preserved below for historical reference.

```
┌─────────────────┐  ┌─────────────────┐  ┌─────────────────┐
│  2.7 Data        │  │  2.6 Writing     │  │  2.8 QA          │
│  Quality ✅      │  │  Quality ✅      │  │  Foundation ✅   │
└────────┬─────────┘  └────────┬─────────┘  └────────┬─────────┘
         │  PARALLEL           │  PARALLEL           │  PARALLEL
         └────────────┬────────┘─────────────────────┘
                      ▼
         ┌─────────────────────┐
         │  2.9 Self-           │
         │  Improvement ✅      │
         └──────────────────────┘

         ┌─────────────────────┐
         │  2.5b Scraper Fixes  │  ← INDEPENDENT — partial ✅
         └──────────────────────┘
```

---

### Phase 2.10: Score-Based Job Tiering & Prioritization — ✅ COMPLETE

**Status**: Column shipped 2026-04-05 (backfilled, `score_batch`+`rescore_batch` write tier). Downstream Lambda gating (tailor/cover/contacts) shipped 2026-04-09. **Geography + work-auth post-score cap shipped 2026-04-29 (PR #24)** — prevents wrongly-S-tier UK/US visa-required jobs.

**Score Tiers**:

| Tier | Score Range | Action | AI Cost |
|------|------------|--------|---------|
| S — Must Apply | 90-100 | Tailor resume, generate cover letter, find contacts, priority email | High (~10 calls/job) |
| A — Strong Match | 80-89 | Tailor resume, generate cover letter | Medium (~7 calls/job) |
| B — Worth Trying | 70-79 | Tailor resume only, no cover letter | Low (~4 calls/job) |
| C — Long Shot | 60-69 | Score only, no artifacts | Minimal |
| D — Skip | <60 | Score only, hide from default dashboard view | Minimal |

**Tier thresholds are user-configurable** per-user via `user_profiles.score_tier_config` JSON column.

**Self-improvement integration**: When thresholds shift (e.g. 80% of jobs below 70), Phase 2.9 generates a medium-risk adjustment to recalibrate.

---

### Layer 2.5: Stabilization & Deploy Safety — IN PROGRESS (2026-04-29 → ongoing)

**Why this layer exists.** Two forces made it necessary in late April:

1. **Stabilization (Phase A)** — by 2026-04-29, two parallel Claude sessions in one day shipped 10 PRs: 5 Plan 3b PRs from Session A and 5 prod-health PRs from Session B. The work surfaced a P0 security finding (AWS STS tokens in user-visible fields), the actual root cause of "no artifacts since Apr 22" (EventBridge cron `Input: {"user_id":"default"}`), and 45 audit findings across 8 cross-cutting bug patterns. Session B has 4 unmerged branches (3 fix branches + 1 audit doc). Phase A consolidates all of this.
2. **Deploy Safety (Phase B)** — PR #14 (2026-04-28) defined a 6-phase deploy-safety roadmap. With 3 prod 500s caught only by *manual* smoke testing on 2026-04-29 and 8 bug-class patterns discovered post-merge, ad-hoc smoke + CI green is no longer enough. Phase B turns the patterns into PR-time gates so the next 50 findings become predictable AND blocked.

The user's verbatim instruction (2026-04-30): *"integrate the other spec of other agent for deployment safety in the grand plan preferably after 3.2 is done as it is very important to catch the bugs on the website."* Plan 3b ≈ "3.2" in the user's numbering.

```
Plan 3a (done) → Plan 3b "3.2" (done 2026-04-29) → Layer 2.5 → Layer 4 / Plan 3c
                                                   ──────────
                                                   Phase A → Phase B
```

#### Phase A — Stabilization

| Sub-task | Type | Status | Notes |
|----------|------|--------|-------|
| A.1.1 Rotate Lambda exec-role creds + CloudTrail audit since 2026-04-22 | 🔴 P0 operator | Pending | Closes the STS-token leak window |
| A.1.2 Fix EventBridge cron `Input` to real user UUID (not "default") | 🔴 operator + `template.yaml` | Pending | Root cause of "no artifacts since Apr 22" |
| A.1.3 Run 3 Plan-3b backfills (eligibility recompute, apply slug, geo+work-auth cap) | 🟡 operator | Pending | Scripts in `scripts/`; commit-mode |
| A.1.4 Add IAM: `JobHuntApi` (`ecs:RunTask`/`StopTask`/`iam:PassRole`), `WsDisconnect` (`ecs:StopTask`) | 🟡 `template.yaml` | Pending | Apply session start/stop currently fail silently; Fargate task leak risk on disconnect |
| A.1.5 Add `UNIQUE(user_id, job_id, version_number)` to `resume_versions` | 🟡 DB migration | Pending | Root cause of "multiple v1 entries" |
| A.1.6 Extend WS auth token TTL 60s → 5min | 🟡 code | Pending | Token expires before Fargate cold-start completes |
| A.1.7 Run `scripts/backfill_missing_artifacts.py` reassign + retailor | 🟢 operator | Pending | Run after A.1.2 unblocks |
| A.2 Consolidate Session B's 4 branches into single `fix/comprehensive-prod-health` PR | 🟡 code | Pending | `artifact-pipeline` (3 commits: F1 creds sanitizer, X2 header markers, A1 apply_url backfill); `dashboard-state` (3 commits: F5 applied count, F6 URL filter persistence, F7 title search); `cluster-bc-cleanup` (12 commits: useApiMutation, Pydantic strict mode, contract route diff, require_db, pre-commit hook); `deep-audit-2` (audit doc — separate doc-only PR) |
| A.3 **Bug X1 fix** — `compile_latex` raises instead of returns error dict; `save_job` marks `application_status="failed"` with `failure_reason` | 🔴 code | Pending | **Highest remaining priority.** Pairs with X2 header-marker fix already in `artifact-pipeline` branch |
| A.4 PR #25 postmortem review + merge | 🟢 doc | In review | Doc-only, mergeable |
| A.5 Verify PR #23 (lazy SSM) unblocks Session B's F4 work on rebase | 🟢 verify | Pending | PR #23 already merged |

**Phase A exit criteria:** prod is healthy, no silent failures, backfills complete, all today's branches merged, no open PRs except deferred (#13 PostHog, #15 canary).

#### Phase B — Deploy Safety (PR #14 phase numbering)

| Sub-phase | What | Status | Source |
|-----------|------|--------|--------|
| **B.1** | Deploy Readiness CI gate (`sam validate` + `sam build` + layer build), runtime-import smoke (`docker run --entrypoint python` exercises lazy imports), `shared/` and `lambdas/` COPY in `Dockerfile.lambda` | ✅ Shipped | PRs #11/12/21 |
| **B.2** | Lambda canary deploys (CodeDeploy AllAtOnce/LinearShift on 13 read-only Lambdas, 12 pipeline-tier, 3 critical-tier WS Lambdas; CloudWatch alarms + auto-rollback) | Held | PR #15, blocked on Phase A clearing PR queue |
| **B.3** | Staging environment: Supabase staging project + SAM stack stage variable + Netlify branch deploys; E2E smoke against staging gates every PR | Pending | PR #14 Phase 3 |
| **B.4** | Pattern-catching CI gates: Pydantic `extra='forbid'` globally, contract route diff test, `useApiMutation` hook, `require_db` helper, pre-commit hook | Branch ready | Session B `cluster-bc-cleanup` (consolidates into A.2 PR) |
| **B.5** | Pipeline observability: Step Function ASL change `Catch → SucceedState` → `Catch → FailState`; alarm on `pipeline_metrics.artifacts_compiled = 0` for 24h; weekly email funnel summary | Pending | Backlog `pipeline_silent_success`; surfaced in Session A 2026-04-29 |
| **B.6** | Trimmed observability: structlog throughout pipeline lambdas, X-Ray on the API container, CloudWatch dashboard for the 5 most-watched infra metrics. **Note:** infra-dashboards only; PR #13 PostHog covers business analytics — no overlap. | Pending | PR #14 Phase 4 (trimmed) |
| **B.7** | Auto-rollback wiring: failing alarm during canary → CodeDeploy reverts; staging smoke gates prod deploy | Pending | PR #14 Phase 6 |

**Phase B exit criteria:** an engineer (or Claude) can ship a feature and have CI catch what would have been a prod incident. The 3 prod 500s on 2026-04-29 (PR #17 + 2 hotfixes) would never have hit prod with Phase B in place.

**Held PRs gating on this layer:**

- **PR #13** — PostHog full integration (analytics + flags + frontend), held until prod health is stable enough for the new event volume
- **PR #15** — Phase 2 Lambda canary deploys, held pending PR queue clear (consolidates into B.2)

---

### Layer 3: Deploy — ✅ LIVE (since 2026-04-21)

| Phase | Name | Status | Notes |
|-------|------|--------|-------|
| 3.0 | Go Live | ✅ Live | SAM deploys via `deploy.yml` from `main` after Deploy Readiness gate; EventBridge weekday cron 07:00 UTC; Netlify auto-deploys frontend; daily pipeline tested end-to-end |

What's actually running in prod:

- **30+ Lambdas**: scrapers (LinkedIn, Indeed, Adzuna, YC, HN, Irish portals); pipeline (`ScrapeRouter`, `ScoreBatch`, `MergeDedup`, `TailorResume`, `GenerateCoverLetter`, `FindContacts`, `EmailNotifier`, `NotifyError`); API (`JobHuntApi` container image); WS (`WsConnect`, `WsRoute`, `WsDisconnect`); auto-apply preview Lambdas
- **Step Functions**: `naukribaba-daily-pipeline` (orchestrator), `naukribaba-run-single-job` (manual JD)
- **API Gateway**: REST + WebSocket
- **Fargate**: cloud-browser auto-apply task definition
- **Supabase**: prod project with RLS on all user tables
- **S3**: artifact storage (resumes, cover letters, screenshots)

**Deploy-safety hardening for the deploy itself** (canary, staging, auto-rollback, observability) lives in Layer 2.5 above.

---

### Layer 4: Product Features — IN PROGRESS

These map to the 6 v2 product stages (Discover → Research → Tailor → Apply → Interview → Analytics).

| Phase | Name | v2 Stage | Was (old) | Key Features | Feeds From |
|-------|------|----------|-----------|--------------|------------|
| 3.1 | Discover+ | Stage 1 | Part of 2A | Manual JD submission, "+Add Job" button, enhanced dedup | 2.7 unified hash |
| 3.2 | Research | Stage 2 | 2D | CompanyLens, GDELT news, salary data, red flags, deeper AI job analysis | 2.6 keyword analysis |
| 3.3 | Tailor+ | Stage 3 | 2B + 2C | PDF-to-LaTeX conversion, Overleaf-style split-pane editor, resume version history | 2.6 quality gates, PDF validation |
| 3.4 | Apply | Stage 4 | Part of 2A | **Cloud-browser auto-apply** (Fargate Chrome + WS streaming + AI prefill), contact finder, follow-ups, outcome tracking → feeds 2.9 | 2.9 user feedback, career-ops apply mode. **Backend ✅; frontend (3c) pending.** |
| 3.5 | Interview Prep | Stage 5 | 2F | Coding bank (Blind 75), system design rubrics, STAR stories, mock AI | — |
| 3.6 | Analytics | Stage 6 | 2G | Funnel viz, score trends, scraper health dashboard, self-improvement viz | 2.9 pipeline_runs data |

**Dependency chain within Layer 4**:

```
3.1 Discover+ ──→ 3.2 Research ──→ 3.3 Tailor+ ──→ 3.4 Apply
                                                        │
                                                        ▼
3.5 Interview Prep (independent)              3.6 Analytics
                                              (needs data from 3.1-3.4)
```

#### Stage 3.4 Apply — Sub-Plan Index (updated 2026-04-30)

Stage 3.4 evolved beyond the original "semi-auto Playwright" framing into a cloud-browser auto-apply system. Sub-specs and sub-plans below; consult these (not the row in the Layer 4 table) for current status.

| Doc | Status | Description |
|-----|--------|-------------|
| Spec: [auto-apply mode 1 design](2026-04-11-auto-apply-mode-1-design.md) | Approved (superseded) | Original mode-1 design for known-ATS apply |
| Spec: [auto-apply cloud-browser design](2026-04-12-auto-apply-cloud-browser-design.md) | Approved | Universal cloud-browser approach (Fargate Chrome + WS streaming) — supersedes mode-1 framing |
| Plan 2: [browser session](../plans/2026-04-20-auto-apply-plan2-browser-session.md) | ✅ Shipped (PR #7, 2026-04-22) | `browser/browser_session.py` + Fargate task def |
| Plan 3a: [WebSocket + backend](../plans/2026-04-24-auto-apply-plan3a-websocket-backend.md) | ✅ Shipped (PR #8, 2026-04-24) | 3 WS Lambdas + 5 `/api/apply/*` endpoints + idempotent record |
| Spec: [apply platform classifier](2026-04-26-apply-platform-classifier-design.md) | ✅ Shipped (PRs #10/11/12, 2026-04-27) | URL → platform classifier; eligibility flag flipped from `apply_platform` to `apply_url`; 831 jobs backfilled; live eligibility >0 for the first time |
| Plan 3b: [AI preview](../plans/2026-04-24-auto-apply-plan3b-preview-ai.md) | ✅ Shipped (PR #17 + #21/#22 hotfixes, 2026-04-29) | AI answer prefill, platform metadata fetchers (greenhouse/ashby), question classifier |
| Plan 3c.0: [Smart Apply Phase 1 hand-paste](../plans/2026-05-01-smart-apply-phase1-plan.md) | ✅ Shipped (PR #52, 2026-05-05) | React UI: Apply button, hand-paste modal, eligibility, AI preview rendering. Submission method `hand_paste` in DB. |
| Plan 3c.full: [frontend UI — live cloud-browser](../plans/2026-05-05-auto-apply-plan3c-full-frontend.md) | ✅ Shipped (PR #56, 2026-05-06) | React UI: `BrowserSessionView`, `useBrowserSession` hook, `AutoApplyContext`, `SessionStatusBadge`, telemetry, manual intervention (Pause/Type/Manual click), modal mode-switch. Backend WS subprotocol auth, ECR image (`naukribaba-browser:latest`, 1 GB), CapSolver SSM key — all in place. **⚠️ Pending live runtime smoke against a real Greenhouse/Ashby form.** |

**Note on Layer placement:** Stage 3.4 originally lived in Layer 4 (AFTER DEPLOY). In practice it was built in Layer 2/3 timeframe alongside reliability work — the four-layer ordering in this doc is a logical narrative, not a strict execution schedule.

---

### Phase 3.2 Enhancement: Glassdoor Company Research

**Status**: Backlog — Glassdoor *job* scraping deprioritized (covered by LinkedIn/Greenhouse/Ashby), but their **company data** is unique.

**What Glassdoor uniquely provides**:

- Company ratings (overall, culture, compensation, career opportunities)
- Salary ranges by role and location
- Interview reviews and difficulty ratings
- Employee reviews (pros/cons/advice)
- CEO approval ratings

**Integration plan** (Phase 3.2 Research):

- Use Bright Data's Glassdoor dataset API or Web Unlocker for company pages
- Store company data in `company_intel` Supabase table
- Display on job cards as "Company Intel" section
- Feed into the A-F evaluation framework (Section D: compensation + market demand)

---

### Cross-Cutting Concerns (Not Phases)

| Concern | How It's Handled |
|---------|------------------|
| **UI Revamp** (was 2A) | Neo-Brutalist styling applied incrementally as each feature ships. Not a standalone phase. Design tokens already defined in Tailwind v4 `@theme`. |
| **Testing** (was 2E) | QA foundation (CI config, fixtures, golden dataset) shipped in 2.8. Tier 4b/4c tests with 2.6/2.7. Tier 4d tests with 2.9. Layer 2.5 Phase B.4 adds pattern-catching gates (Pydantic strict, contract route diff, `useApiMutation`). |
| **Security** | RLS on every user table. Layer 2.5 Phase A.1.1 closes the AWS STS token leak. Pydantic `extra='forbid'` (B.4) prevents future field-strip drift. |
| **Multi-tenancy** | Built for single user now. Hardcoded user info (Pattern #6 from the audit) cleared piecewise in Layer 2.5 A.2; cover-letter and matcher prompts still pending. RLS ensures isolation when multi-tenant. |
| **Career-ops integration** | Reference architecture from github.com/santifer/career-ops. ATS keyword extraction in 2.6, A-F evaluation framework feeds 3.2 Research, "filter not firehose" philosophy drives 2.10 tiering. |

---

## Career-Ops Integration Map

Reference: `github.com/santifer/career-ops` — 740+ job evaluations, 100+ tailored CVs.

Philosophy: **"A filter, not spray-and-pray."** Only top matches get full treatment.

| Our Phase | Career-Ops Feature | Integration | Status |
|-----------|-------------------|-------------|--------|
| 2.6 Writing Quality | ATS keyword injection, proof-point extraction | Extract 15-20 JD keywords → inject into existing bullets (never fabricate) | Partial |
| 2.7 Data Quality | Cross-source dedup | Description-independent `dedup_hash`, Tier 0 exact match | ✅ |
| 2.10 Tiering | "Don't apply below 4.0" | D-tier hidden, C-tier no artifacts, S+A get full treatment | ✅ |
| 3.1 Discover+ | 3-tier scanning (Playwright→API→WebSearch), 60+ companies | Greenhouse API + Lever API + company watchlist | Pending |
| 3.2 Research | A-F Evaluation (10 dimensions), compensation data | Multi-dimension scoring | Pending |
| 3.3 Tailor+ | ATS-optimized PDF, template system | Keyword-first tailoring, regen button | Partial |
| 3.4 Apply | Cloud-browser auto-apply | Fargate Chrome + WS streaming + AI prefill | Backend ✅, frontend pending |
| 3.5 Interview Prep | STAR+Reflection stories, behavioral mapping | Story bank in Supabase, per-job prep auto-generated | Pending |
| 3.6 Analytics | Application outcome tracking → feedback loop | Ground truth feeds scoring accuracy | Pending |

---

## How Current Spec Feeds Into Future Phases

| This Spec | Feeds Into | How |
|-----------|------------|-----|
| 2.7 Unified hash | 3.1 Discover+ | Manual JD submission uses same canonical dedup |
| 2.7 Before/after scoring | 3.3 Tailor+ | Resume version comparison in editor workspace |
| 2.6 Keyword analysis | 3.2 Research | Structured JD extraction feeds company intel |
| 2.6 PDF validation | 3.3 Tailor+ | Quality gates carry into editor + PDF-to-LaTeX |
| 2.10 Geo + work-auth cap | 3.4 Apply | Eligibility-aware tier prevents wasting AI on ineligible jobs |
| 2.5 (Layer 2.5) Phase A creds sanitizer | All future Lambda error paths | F1 sanitizer in shared error handler closes the str(e) leakage class |
| 2.5 (Layer 2.5) Phase B.4 gates | All future PRs | Pydantic strict + contract route diff + useApiMutation catch the 8 patterns at PR time |
| 2.9 Self-improvement loop | 3.6 Analytics | Scraper health + score trends power dashboard |
| 2.9 User feedback | 3.4 Apply | "Flag score" feeds back from application tracking |
| 2.9 Pipeline metrics | 3.6 Analytics | `pipeline_runs` table powers funnel viz |
| 2.8 QA tiers | All phases | Test infrastructure scales as stages are added |

---

## Old Phase Mapping (2A-2G → New)

For reference, how the original v2 phases map to the new structure:

| Old Phase | Old Scope | New Location | Notes |
|-----------|-----------|-------------|-------|
| 2A: UI Revamp | Neo-Brutalist redesign | Cross-cutting | Applied incrementally, not standalone |
| 2B: Editor | Overleaf-style LaTeX editor | 3.3 Tailor+ | Combined with PDF-to-LaTeX |
| 2C: PDF-to-LaTeX | Upload PDF → convert | 3.3 Tailor+ | Combined with editor |
| 2D: Company Intel | CompanyLens, GDELT, salary | 3.2 Research | Renamed to match v2 stage |
| 2E: Testing | 7-tier QA suite | 2.8 + cross-cutting | Foundation in 2.8, incremental after |
| 2F: Interview Prep | Coding, system design, STAR | 3.5 Interview Prep | Unchanged |
| 2G: Analytics | Funnel, trends, health | 3.6 Analytics | Unchanged |

**New additions** (not in original 2A-2G):

- 2.5b: Scraper fixes
- 2.6: Writing quality
- 2.7: Data quality
- 2.9: Self-improvement loop
- 2.10: Score-based tiering
- **2.5 (Layer 2.5): Stabilization & Deploy Safety**
- 3.0: Deploy
- 3.1: Discover+ (manual JD)
- 3.4: Apply (now cloud-browser auto-apply)

---

## Success Criteria Per Layer

**Layer 2 (Reliability) — ✅ MET (2026-04-29)**:

- ✅ Zero duplicate jobs in dashboard for same company+title (canonical hash dedup live)
- ✅ Same job scored twice within ±2 (deterministic 3-call median scoring)
- 🟡 Every tailored resume has before/after score delta — deferred to Layer 4 / 3.3 Tailor+
- ✅ Writing quality fallback gate prevents broken AI output reaching users
- ✅ Self-improvement loop running with tiered adjustments
- ✅ QA suite 712+ tests in CI
- 🟡 Glassdoor returning jobs via Fargate — deprioritized (Greenhouse/Ashby/LinkedIn cover)

**Layer 2.5 (Stabilization & Deploy Safety) — IN PROGRESS**:

- 🟡 Phase A.1 operator actions complete (creds rotation, EventBridge cron, IAM, DB constraints, WS TTL)
- 🟡 Phase A.2 Session B branch consolidation merged
- 🟡 Phase A.3 Bug X1 + X2 fixed (compile_latex visibility, header-marker fallback)
- ✅ Phase B.1 Deploy Readiness CI gate live (PRs #11/12/21)
- 🟡 Phase B.2 Lambda canary live for read-only + pipeline tier
- 🟡 Phase B.3 Staging environment: Supabase + SAM + Netlify branch deploys; E2E smoke gating prod
- 🟡 Phase B.4 Pattern-catching CI gates merged (Pydantic strict, contract route test, useApiMutation, require_db, pre-commit)
- 🟡 Phase B.5 Pipeline silent-success eliminated (Step Function ASL `FailState` + alarm on 0 artifacts)
- 🟡 Phase B.6 structlog + X-Ray + infra dashboards live
- 🟡 Phase B.7 Auto-rollback wired

**Layer 3 (Deploy) — ✅ MET (2026-04-21)**:

- ✅ Lambda functions deployed and responding
- ✅ Frontend live on Netlify with production API URL
- ✅ Daily pipeline triggered via EventBridge → Step Functions
- ✅ Email notifications (per-run summary)

**Layer 4 (Features) — partial**:

- 🟡 User can paste a JD and get same pipeline treatment (3.1 Discover+) — `run_single_job` SFN exists; UI partial
- 🟡 Company intel card on each job (3.2)
- 🟡 Split-pane LaTeX editor (3.3 Tailor+) — basic editor in dashboard, no split-pane yet
- ✅ Application tracking with outcome feedback (3.4 Apply) — backend, frontend (3c.0 + 3c.full), cloud-browser image all shipped 2026-05-05/06; ⚠️ pending live runtime smoke against real Greenhouse/Ashby form
- 🟡 Interview prep for any job (3.5)
- 🟡 Analytics dashboard with funnel + trends (3.6)

---

## Cost Projection

| Layer | Monthly Cost | Notes |
|-------|--------------|-------|
| Foundation (Layer 1) | ~$1 | Free AI tiers, local pipeline, no infra |
| Layers 2 + 3 (current state) | ~$15-25 | Lambda (free tier mostly), Fargate (~$5 — auto-apply task on demand), Supabase (free), S3 (<$1), Netlify (free), Bright Data Web Unlocker (~$5-10) |
| After Layer 2.5 | +$5-10 | Staging Supabase project, CloudWatch dashboards, X-Ray traces. Largely free-tier eligible. |
| After Layer 4 | ~$30-50 | More AI calls (interview prep, deeper research), CompanyLens API, additional Lambda invocations from frontend Auto-Apply |

---

## Historical Status Snapshots

Preserved for reference — current state is in the "Status Snapshot — 2026-04-30" section at the top of this document.

### Status Snapshot — 2026-04-06 (historical)

**✅ Done that day:**
- 3.0 Deploy: SAM deployed (4×), EventBridge ENABLED (weekdays 07:00 UTC), Step Functions pipeline tested end-to-end
- ScoreBatch Map batching: 421 jobs split into 25-job chunks, 5 parallel, no timeout
- Data quality audit: 149 scores fixed, 59 expired, 205 dupes removed, 117 tiers realigned, 18 descriptions backfilled
- IrishJobs JSON-LD: detail page descriptions extracted via structured data
- API 500 fix: `utils/` added to `Dockerfile.lambda`
- Page length validation: fallback to base if AI output too short

**🔴 Issues found (since resolved unless noted):**
- Lambda tailoring quality gaps (Lambda↔local code drift) — became audit Pattern #5, ongoing in Layer 2.5
- 190 cross-source dupes — addressed via canonical hash dedup
- 687 → 467 jobs in DB — score-tier filtering shipped
- Cover letters: 99/467 jobs only — gating + S/A-tier focus shipped

**🎯 Immediate-next list at the time** (all addressed): cross-source dedup, port tailoring guards to Lambda (still partial — Layer 2.5 A.2 finishes), dashboard declutter, Greenhouse/Lever scrapers, career-ops A-F framework integration.

### Status Snapshot — 2026-04-05 evening (historical)

**✅ Done that week:**
- 2.5b Scraper Fixes (partial): OpenRouter 404 fixed (5 verified free models); AI council 18 → 32 providers; DeepSeek disabled (NVIDIA NIM alternative)
- 2.7 Data Quality: canonical hash dedup live (177 → 159 jobs); deterministic 3-call median scoring; `score_version=2`; `score_status` tracking
- 2.10 Score Tiering: `score_tier` column + CHECK + index shipped; all 159 jobs tiered (S=14, A=41, B=26, C=51, D=27)
- Council retry logic with fresh-provider failover
- Hard gate relaxed: substring matching for section completeness

**🔴 Discovered then (since addressed in Apr 9 marathon):**
- AI-generated LaTeX produced invalid output (undefined macros, unbalanced env blocks) — fixed via brace/macro sanitizer
- Tailoring prompt drops sections — fixed via prompt v2
- Groq IP-block from Singapore VPN — deprioritized in council

**⏸️ Blockers at the time:**
- SAM deploy: Docker daemon stuck — resolved (deploy live since 2026-04-21)
- Apify budget exhausted — Bright Data contact finder approach taken
