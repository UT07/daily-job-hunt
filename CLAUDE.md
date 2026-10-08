# CLAUDE.md — Project Context for Claude Code

> **Current state, verified numbers and the active roadmap live in
> [`docs/ROADMAP.md`](docs/ROADMAP.md).** This file describes conventions,
> the module map and how to work in the repo. Where the two disagree about
> what is true *today*, ROADMAP.md wins.

## Project Overview

**NaukriBaba** is an automated job search pipeline + self-service web app.
It scrapes 7 job boards, matches jobs using 3-perspective AI scoring, generates
tailored LaTeX resumes and cover letters, uploads PDFs to Google Drive, and
sends email summaries. A React landing page lets users paste any JD and get
tailored resumes on demand.

## Verification rules

Every rule below is here because it was broken, on 2026-09-29, at a cost. Each
names the incident so it can be checked rather than believed. Read this before
reporting that anything works.

### 1. Green CI and a successful deploy are not evidence the feature works

Every single failure that day passed CI and deployed cleanly. Regenerate was
broken by the PR that "fixed" it; Add Job failed for six weeks of cold starts;
the résumé pipeline stored 9% of an uploaded document. All green.

**Do:** exercise one real user action against the deployed system and read the
result before saying it works. `scripts/smoke_prod.py` exists for this and
`deploy.yml` runs it. Running the flow as the user takes two minutes:

```bash
SMOKE_API_URL=$(grep -hoE '^VITE_API_URL=.+' web/.env.production | cut -d= -f2-)   .venv/bin/python scripts/smoke_prod.py
```

**Never:** report "merged and deployed" as though it meant "working".

`sam build` is not `sam deploy`. On 2026-09-30 six consecutive deploys failed on
a CloudFormation circular dependency while `sam build` and the whole Deploy
Readiness job stayed green, because no changeset is created at build time. Plain
`sam validate` does not check dependency cycles either; `sam validate --lint`
does (cfn-lint E3004) and now runs in CI. Do not remove `--lint`, and do not add
`E3004` to `.cfnlintrc.yaml`.

### 2. A status that cannot distinguish "did the work" from "did nothing" is a lie

Three instances the same day:

- the daily Step Function ended in `Succeed` after catching an error, so
  `ExecutionsFailed` could never tick — green alarms over a dead pipeline
- `scripts/smoke_prod.py` printed **"5/5 passed"** for a run where one check
  never executed
- `sections_have_content()` passed a conversion that kept 772 of 8,490
  characters, because it only needs ONE of five sections to be non-empty

**Do:** ask of any success signal — *what would this report on a no-op run?*
If the answer is "success", it is not a status.

### 3. Confirm the metric exists before reading zero as good news

API Gateway v2 publishes `5xx`; the REST API publishes `5XXError`. Querying the
wrong one returns no datapoints, which reads exactly like no errors. It hid two
user-facing 503s, and `PipelineHealthDashboard` had charted an empty line for
weeks for the same reason.

**Do:** `aws cloudwatch list-metrics` first, or check a known-nonzero metric
(`Count`) in the same call, before concluding a metric is zero.

### 4. A prompt-level constraint is a request; only a check is a guarantee

`tailor_resume.py` said "EXACTLY 3 PROJECTS. No more, no less." and nothing
counted `\projectentry`. It still says the résumé must be two pages and nothing
measures the compiled PDF.

**Do:** if a rule is countable, count it after generation (see
`shared/composition_policy.check_output`). Feed the real numbers into any
repair retry — "you emitted 5 projects, the limit is 3" is actionable; repeating
the rule at a model that already ignored it is not.

### 5. A mock can only confirm what its author already believed

The Studio's S3 key had a **0% hit rate** in production — 0 of 1,794 objects —
after two rounds of green unit tests and a six-lens review, because every one of
them mocked S3. Only listing the real bucket found it.

**Do:** for anything crossing a boundary (S3, Postgres, a provider, API
Gateway), verify once against the real thing.

### 6. A test double that fails the way the bug fails is worse than no test

The council double was wrong four times in a row — no `rate_limiter`, no
`complete_with_retry`, a keyword-only signature, and a bare `MagicMock` cache
whose `get_with_info` returned a truthy mock (a phantom cache hit). Every one
presented as *"the healthy provider was never tried"*: the exact production
symptom.

**Do:** when a test fails with the symptom you are hunting, prove the double is
sound before concluding anything about the code.

### 7. Scope a check to the population it is meant to judge

`tex-key-resolves` sampled any row with a `resume_s3_key`, found 50%, and
passed — because the bar had been set low enough to accommodate rows that were
never supposed to have a tailored `.tex` (C-tier jobs use `default_base.pdf`).
Scoped correctly the real figure is 145/145.

**Do:** if a check needs a low threshold to pass, the population is probably
wrong. Fix the population, then raise the bar.

### 8. Configuration must be explicit, never an import side effect

`smoke_prod.py` had no credentials of its own: the first check ran
`import app`, which loads `.env`, and every later check lived off that.
Rewriting that one check broke the rest with `KeyError: 'SUPABASE_URL'` — an
error that reads like a missing secret.

### 9. A presigned URL is a key plus a signature; only the signature expires

Three separate defects came from treating one as the other, including a
migration that declared 672 rows unbackfillable while the recovery data sat in
the column beside it. All 673 were recovered from the URL path.

### 10. Search the whole repo for a guard before trusting it

`RETIRED_MODEL_IDS` had listed `meta/llama-3.3-70b-instruct` as "410 Gone" since
August, and the test only ever checked `ai_helper`. `ai_client.py` defaulted two
providers to retired models the whole time. The guard existed, the data existed,
and they never met.

Two more instances, both 2026-09-30. `pick_latest_tailorable` was added after a
PDF upload broke tailoring, and wired into one of the three modules that read
`user_resumes` — the other two kept `.limit(1)` with no validity check.
`test_council_engine_parity.py` asserted every council caller sets
`COUNCIL_ENGINE`, and silently skipped the API container because it is
`PackageType: Image` and has no `Handler`; three assertions passed about the
wrong set.

### 11. Deploys must serialise

CloudFormation executes one change set per stack. Two merges seconds apart make
the later deploy die with `ChangeSet ... OBSOLETE`, and `main` silently moves
ahead of production. `deploy.yml` now has a `concurrency` group — do not remove
it.

### 12. Fix the instrument before trusting the reading

Half of that day was spent chasing a symptom the tooling was misreporting. When
a measurement is surprising, check the measurement first.

### 13. A guard's severity is part of its implementation

`check_fabrication` correctly detected a resume claiming Rust that the base
resume has none of. It shipped anyway: the violation carried severity `"warn"`,
`GuardResult.passed` is `not any(severity == "block")`, and the repair loop built
for exactly this was armed, correctly wired, and never told. One string literal
disarmed the whole mechanism.

**Do:** when adding a check, say what happens when it fires and test that, not
just that it fires. A test asserting the detector returns a violation passes
identically whether the violation stops anything.

### 14. When a comparison decides whether to accept output, both sides must count the same things

`tailor_resume`'s quality retry was accepted when
`len(retry_quality) < len(quality_warnings)`. The first list counted banned
phrases, `\textbf` preservation and fabrication; the second omitted fabrication.
So a retry that kept every fabricated skill scored as an improvement whenever it
dropped one banned phrase, and the fabrication shipped.

**Do:** if two expressions are compared, assert they are built from the same
set of checks — structurally, so the next omission fails CI instead of review.

### 15. Vary the baseline, not just the rule

Substring matching let the corpus's "TypeScript/JavaScript" whitelist a
standalone "Java" claim permanently — 482 of 707 real outputs, none flaggable.
The obvious fix, word-boundary matching, measured 97 of 140 resumes flagged
versus 23, so it was documented as a deliberate known miss. That was wrong. The
97 were Java, which is in the candidate's April resume row and missing only from
the degraded September row being compared against. Word boundaries against the
UNION of all rows: 23 of 140, same as substring, with the hole closed.

**Do:** when a fix looks too expensive, check whether you varied one input and
held the other fixed. A comparison has two sides.

### 16. Measure a detector's false-positive rate before shipping it, not after

A whole-document fabrication detector was the obvious next step once fabrication
became blocking. Measured against 707 real resumes first: it fires on 533
(75.4%) after every safe normalisation, with a hand-adjudicated ~52%
false-positive rate, and its most frequent finding is the candidate's own
degree. It would have blocked three of four resumes and spent two repair rounds
on each. It was not built.

Also measured, and each must stay refused: substring containment as an
exoneration (clears "scala" via "scalable"), common-English-word suppression
(69% of real fabrications ARE dictionary words), and "the claim is in the job
description" — 92% of real fabrications are, because lifting the JD's
requirement IS the failure mode. A wrong exculpation produces a miss, and a miss
is the outcome these checks exist to prevent.

### 17. Two bugs stack, and the first one hides the second

The 2026-09-30 deploy outage was an unset `CEREBRAS_API_KEY` making `sam deploy`
reject its own `--parameter-overrides` at CLI parse. Fixing it did not fix the
deploy — it revealed a CloudFormation circular dependency introduced by a
different PR, which had never deployed once because the parse error killed every
run before CloudFormation was called.

**Do:** after a fix lands, verify the outcome you actually wanted, not that the
error you understood is gone.

## Architecture

- **Pipeline**: AWS Step Functions state machines (`DailyPipelineStateMachine`,
  `SingleJobPipelineStateMachine`) defined in `template.yaml`. `main.py` is the
  legacy local-run orchestrator, retained for local dry-runs only.
  **All four EventBridge schedules are ENABLED** — disabled 2026-09-02 (#87),
  re-enabled 2026-09-26 (#93). Each weekday run spends on Bright Data, Apify
  and LLM calls; see ROADMAP.md.
- **API** (`app.py`): FastAPI backend with 5 endpoints, deployable to AWS Lambda via Mangum
- **Frontend** (`web/`): React + Vite + Tailwind, deployable to Netlify
- **Self-improvement** (`self_improver.py`): Post-run analysis that detects weak spots

## Key Design Decisions

- **LaTeX over Google Docs**: LaTeX + tectonic gives pixel-perfect ATS-friendly PDFs in ~15s. Google Docs approach was tried and reverted (commit abc0fe9).
- **Multi-provider AI**: Groq → DeepSeek → OpenRouter → Claude failover chain. All free tiers. SQLite response cache with 72h TTL.
- **AI council**: 2 generator models from distinct families + a cross-family
  critic = 3 LLM calls per decision, over a 7-model live pool. Earlier docs
  claimed "32 LLMs"/"24 LLMs"; neither was ever true.
- **3-perspective scoring**: Every resume is evaluated as ATS (keyword match), Hiring Manager (impact), and Technical Recruiter (skills depth). All 3 must score 85+ or the resume is iteratively improved.
- **Google Drive for sharing**: Service account uploads PDFs, shares with user's Gmail. Permanent links (unlike S3 presigned URLs which expire in 30 days).

## Module Map

| Module | Purpose |
|--------|---------|
| `main.py` | Pipeline orchestrator |
| `app.py` | FastAPI backend (5 REST endpoints) |
| `ai_client.py` | Multi-provider AI with failover, rate limiting, caching |
| `matcher.py` | Batch job matching (5 jobs/prompt), 3-score evaluation |
| `tailorer.py` | LaTeX resume tailoring with cache-invalidating resume hash |
| `resume_scorer.py` | Score + iterative improvement loop (up to 3 rounds) |
| `cover_letter.py` | LaTeX cover letter generation |
| `contact_finder.py` | LinkedIn contact finder with intro messages |
| `latex_compiler.py` | LaTeX → PDF via tectonic (fallback: pdflatex) |
| `excel_tracker.py` | Excel tracker with color-coded scores |
| `drive_uploader.py` | Google Drive upload with shareable links |
| `s3_uploader.py` | S3 upload with 30-day presigned URLs |
| `email_notifier.py` | Gmail HTML notification with top 15 jobs table |
| `self_improver.py` | Post-run analysis: scores, keywords, scraper health |
| `lambdas/pipeline/agents/` | LangGraph council: state, nodes, graph, provider adapter |
| `lambdas/pipeline/guardrails/` | Safety layer: injection detection, output guards, per-task policy |
| `lambdas/pipeline/retrieval/` | pgvector: Gemini embeddings, similarity queries, semantic dedup, bullet retrieval |
| `scrapers/` | Lambda scrapers: LinkedIn, Indeed, Glassdoor (blocked), Irish (Jobs.ie+IrishJobs+GradIreland), Adzuna, YC, HN. Uses httpx + Bright Data Web Unlocker proxy. |
| `scrapers/playwright/` | DORMANT — Scrapling/Fargate scrapers, superseded by Web Unlocker. Kept as fallback for JS-heavy sites like Glassdoor. |

## Config

- `config.yaml`: Profiles, search queries, API keys (via `${ENV_VAR}`), scraper settings, Google Drive config
- `.env`: Local environment variables (gitignored)
- `google_credentials.json`: GCP service account (gitignored)

## Development Commands

```bash
# Run pipeline
python main.py                    # Full run
python main.py --dry-run          # Scrape + match only
python main.py --scrape-only      # Just scrape

# Run API locally
uvicorn app:app --reload --port 8000

# Run frontend locally
cd web && npm run dev

# Build frontend
cd web && npm run build

# Test Google Drive connection
python scripts/test_drive_connection.py

# Run self-improvement analysis
python self_improver.py
```

### Pre-commit hooks

One-shot dev env setup (works from main repo OR any git worktree):

```bash
bash scripts/dev-setup.sh
```

That script creates the shared `.venv` and installs `requirements.txt` +
`requirements-dev.txt` (which pins `pre-commit` and `ruff` to the versions
in `.pre-commit-config.yaml`).

Hook activation is opt-in because the repo carries format drift the hook
would otherwise reject. To enable safely:

```bash
pre-commit run --all-files            # inspect / commit cleanup
pre-commit install --install-hooks    # activate (covers all worktrees;
                                      # they share core.hooksPath)
```

Once active, `git commit` runs ruff + ruff-format and catches the
"unused pytest import" class of CI failures locally before push.

## Deployment

- **Frontend**: Netlify (`netlify.toml` configured, set `VITE_API_URL` env var)
- **Backend**: AWS Lambda via SAM (`template.yaml`, use `sam deploy --guided`)
- **Pipeline**: Step Functions, triggered by EventBridge (ENABLED since 2026-09-26, #93).
  `.github/workflows/daily_job_hunt.yml` still exists and still invokes `main.py`,
  but is not the production path.

## Implementation Status

See `docs/superpowers/specs/2026-03-19-google-docs-landing-page-plan.md` for the full 6-phase plan.

| Phase | Status |
|-------|--------|
| 1. GCP + Drive upload | ✅ Complete |
| 2. Pipeline integration | ✅ Complete |
| 3. FastAPI backend | ✅ Complete |
| 4. React frontend | ✅ Complete |
| 5. AWS SAM template | ✅ Complete (deploy = user action) |
| 6. Testing + self-improvement | ✅ Self-improver done, E2E = user action |
| 2.5 Web Unlocker scrapers | ✅ Complete — LinkedIn, Indeed, Irish portals working. Glassdoor needs Fargate (backlog). |
| 2.6 Resume quality (planned) | Backlog — improve AI writing quality in tailoring + cover letters |

## Previous Design Spec

The original pipeline overhaul (3 phases: Foundation, Quality, Production-Grade) is at
`docs/superpowers/specs/2026-03-17-pipeline-overhaul-design.md`. All items from Phases 1-2
are complete. Phase 3 items 3.1 (logging) and 3.4 (retry backoff) are done. Items 3.2
(checkpointing) and 3.3 (SQLite job database integration) remain as future work.

## Important Notes

- The `Job` class is defined in `scrapers/base.py` — all modules use it
- AI responses are cached in SQLite (`output/.ai_cache.db`) — delete to force fresh calls
- `seen_jobs.json` tracks processed jobs across runs — don't delete unless you want full re-processing
- Service account is from GCP project `job-automation-490716` (owned by utkarsh45689@gmail.com), shares files with 254utkarsh@gmail.com

## Backlog

### Data Quality & Scoring Reliability (Phase 2.7 — Priority 1)
Systematic issues found during Phase 2.5 testing (Apr 3, 2026):

- **Duplicate jobs with different scores**: Same job scraped across queries gets different hashes → different AI scores (e.g. "Backend Software Engineer @ TREQS" scored 78 and 68). Need cross-query dedup using company+title similarity, not just hash equality.
- **Missing descriptions**: 18 jobs (mostly IrishJobs) have 0-char descriptions — detail pages return 403. Jobs without descriptions get inaccurate scores. Either skip scoring for description-less jobs, or enrich descriptions from other sources.
- **Score inconsistency**: Non-deterministic AI scoring — same job gets different scores across runs. Need multi-call averaging or deterministic prompting (temperature=0).
- **No original vs tailored score**: Only one score at match time. Need before/after comparison: score base resume against JD, then score tailored resume, show delta.
- **Score accuracy**: User reports scores feel inaccurate/low. Review scoring prompt quality, consider multi-perspective scoring (ATS + Hiring Manager + Tech Recruiter as in original pipeline).

### Resume & Cover Letter Quality (Phase 2.6)
- Current AI-generated resumes need better writing quality — more impactful language, stronger action verbs, better tailoring depth
- Design a quality improvement loop: (1) score existing outputs for writing quality, (2) build exemplar prompts with high-quality samples, (3) integrate quality checks into `self_improve.py` to detect and flag weak outputs, (4) iterative prompt refinement based on scoring feedback
- This should be a dedicated phase, not a quick fix

### Infrastructure & Deployment
- **SAM deploy blocked**: `sam build` needs Docker for `JobHuntApi` container image. Lambda functions build fine. Start Docker Desktop, then `sam build && sam deploy --guided`.
- **Glassdoor Scraper (Fargate/Playwright)**: Glassdoor requires JavaScript rendering — login wall blocks httpx. Genuine use case for dormant `scrapers/playwright/` + `Dockerfile.playwright`. When needed: build/push Docker image to ECR, wire `PlaywrightTaskDef` into Step Functions.
- **DeepSeek provider**: Returns 402 (empty balance). Either top up or remove from failover chain.
- **OpenRouter provider**: Returns 404. Config issue — check API key and model name in SSM.
- **GradIreland scraper**: Returns 0 jobs — Drupal template likely changed. Needs HTML inspection and pattern update.
