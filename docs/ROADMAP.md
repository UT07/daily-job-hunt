# NaukriBaba — Roadmap & Current State

**Last verified: 2026-09-26.** Every number here was checked against the live
system or the code on that date, not copied from an older document. Where
something could not be verified it says so.

This file supersedes the status claims scattered across `CLAUDE.md`, the nine
specs in `docs/superpowers/specs/` and the fourteen plans in
`docs/superpowers/plans/`. Those remain useful as design history; this is the
only file that claims to describe the present.

---

## 1. What this is

A job-hunt automation system with two modes that share one core:

- **Batch** — a Step Functions pipeline (live; daily at 07:00 UTC weekdays) scrapes job boards, deduplicates,
  scores each job against the user's resume with an ensemble of LLMs, and
  generates tailored LaTeX resumes and cover letters as PDFs.
- **Interactive** — a React dashboard and FastAPI backend where a user pastes
  any job description and gets a tailored resume on demand.

The shared core is: multi-provider LLM orchestration with failover, LaTeX
tailoring, and a pgvector retrieval layer.

**What it is not:** it does not run on GitHub Actions (that was replaced by
Step Functions over a year ago), and it does not use Google Docs (tried and
reverted). Older docs claim both.

---

## 2. Honest current state

### The AI council — say this accurately

The system samples **2 generator models from distinct model families**, then
has a **critic from a third family** score their outputs; the highest-scoring
candidate wins. A failed generator falls back to another family.

| Measure | Value |
|---|---|
| LLM calls per decision | **3** (2 generators + 1 critic) |
| Live scoring pool | **7 models across 6 families** (`ai_helper._build_provider_list`) |
| Legacy `ai_client.py` config | 12 distinct models |
| Failover pool reported by `/api/health` | 11 providers |

Earlier documents claimed "32 LLMs" and "24 LLMs". **Neither was ever true.**
Corrected in `README.md` and `resumes/fullstack.tex` on 2026-09-25. The
interesting property is model-family diversity to decorrelate errors, not a
headcount.

### What is live in production

- LangGraph `StateGraph` council, `COUNCIL_ENGINE=langgraph`, serving real
  traffic since 2026-09-23. Parallel generator fan-out via the `Send` API.
- Measured **32% faster p50** (5.12s → 3.47s) on a 5-run local benchmark.
  Small N, measured locally, and the critic call is still serial — quote it
  with those caveats.
- pgvector schema and RPCs applied to the live database.
- 1,232 of 1,243 jobs have embeddings.

### What is built but NOT enabled

| Feature | Flag | State |
|---|---|---|
| Semantic dedup (tier 4) | `SEMANTIC_DEDUP` | off, undeployed |
| Bullet-retrieval RAG | `BULLET_RAG` | off, undeployed |
| Guardrails (types, policy, input, output) | n/a | built, not wired into the graph |

### The numbers that matter

| | |
|---|---|
| Jobs in database | 1,243 |
| **Jobs actually scored (`match_score > 0`)** | **1,243 — all of them** |
| Not expired | 64 |
| Test suite | 1,373 passing, 46 skipped |

**Two corrections to the 2026-09-25 version of this file.** It claimed a
1,137-job scoring backlog and that 0 of 64 live jobs were scored. Both were
wrong — they queried `score_status`, a column nothing reliably writes and
nothing reads. The app renders `match_score` and sorts on it. `final_score` is
null for all 1,243 rows. **Both columns are dead; do not plan around them.**

### Which columns are authoritative

The `jobs` table carries several generations of near-duplicate score and
artifact columns — the `score_status` / `final_score` trap above is one
symptom of a wider problem. Re-verified against the live database and the
code on 2026-09-26, later the same day as the count above: row count had
grown to **1,251** (+8, from the supervised run under §3) and `final_score`
was no longer null everywhere — a narrower helper had started populating it
for a minority of rows. Full rationale lives in the `COMMENT ON COLUMN`
migration `supabase/migrations/20260926150000_document_authoritative_job_columns.sql`;
the set below is pinned by `tests/unit/test_authoritative_columns.py`, which
parses `app.py` and `web/src` rather than trusting this table to stay in sync.

| Column | Populated / 1,251 | Status | Use instead |
|---|---:|---|---|
| `match_score` | 1,251 | **Authoritative** — the score | — |
| `resume_s3_url` | 921 | **Authoritative** — resume artifact link | — |
| `cover_letter_s3_url` | 658 | **Authoritative** — cover letter link | — |
| `score_status` | 1,251 | Legacy — stuck at default `'pending'` | `match_score` (`>0`) |
| `score_version` | 1,251 | Legacy — rescore-script bookkeeping only | n/a |
| `final_score` | 129 | Legacy/partial — mirrors tailored `match_score` on one narrow path | `match_score` |
| `scored_at` | 111 | Legacy — rescore/backfill scripts only | `first_seen` / `last_seen` |
| `tailored_pdf_path` | 39 | Legacy — local path from `main.py` dry-runs | `resume_s3_url` |
| `cover_letter_pdf_path` | 35 | Legacy — local path from `main.py` dry-runs | `cover_letter_s3_url` |
| `resume_doc_url` | 12 | Legacy — pre-LaTeX Google Docs link | `resume_s3_url` |

### Throughput, re-derived from CloudWatch

The earlier "80-120 jobs/day" figure was off by an order of magnitude.
Real `naukribaba-score-batch` metrics:

| Date | Invocations | Avg duration | Max |
|---|---|---|---|
| 2026-09-01 | 15 | 133s | 275s |
| 2026-08-31 | 18 | 403s | **900s — Lambda timeout** |

At `chunk_size` 10 and `MaxConcurrency: 1`, 2026-09-01 scored **150 jobs in
~33 minutes** — about **4.5 jobs/minute**. The binding constraint is not a
daily token budget; it is how long a run stays inside the 900s per-chunk
timeout before Groq's 8k TPM throttling stretches a chunk past it. At 150 jobs
that is comfortable; at 180 (2026-08-31) chunks died at exactly 900,000ms.

Observed on the first live run after re-enabling (2026-09-26):
`2,000 scraped -> 1,129 unique -> 53 passed filter -> 27 new for scoring`.
Well inside capacity. The filter may now be too tight rather than too loose.

### Cost

AWS is ~$1–3/month attributable to this project (verified via Cost Explorer;
the account is shared with unrelated projects whose RDS costs dominate the bill).
Off-AWS cost — Bright Data Web Unlocker and Apify — **could not be verified**
from the development environment and needs a console login.

---

## 3. Roadmap to 2026-10-01

Ordered by dependency, not by preference.

### Done since this file was written

- **Schedules re-enabled** (#93). First automatic daily run is Monday
  2026-09-28 07:00 UTC — the cron is weekdays only, so nothing fires at the
  weekend. One supervised manual run was triggered on 2026-09-26.
- **Scoring intake capped** at the measured-capacity volume (#92), backfill
  window widened 7 -> 30 days so jobs that miss a run are no longer lost.
- **Guardrails wired as graph nodes and armed per task** (#91). Before this,
  every call resolved the `default` policy because nothing set `task`, so the
  LaTeX-structure and fabrication guards were unreachable through the node path.
- **`ArtifactsCompiled` metric fixed** (#90). It read a field `save_job` never
  returns, reported 0 on 74/74 sampled days, and had been stuck in ALARM since
  2026-04-30 against a topic with zero subscribers.
- **Three schedule-blocking bugs fixed** (#90), including
  `send_followup_reminders` querying a nonexistent column — 100% failure since
  2026-08-12, on one of the four schedules now live.
- **Dashboard** default view, dead nav and Card View actions (#90).
- **Bounded checkpointer** replacing the unbounded `MemorySaver`; there is no
  `SUPABASE_DB_URL` in SSM, so the durable Postgres option is unavailable
  rather than merely undone.

### Outstanding, needing a human

- **Confirm the SNS email subscription.** Still `PendingConfirmation`. The
  alarms now compute correctly and notify nobody until the link is clicked.
- **Watch Monday's 07:00 UTC run.** Nothing has run at full scale
  unsupervised since 2026-09-02.

### Should land before the interview

- Wire the guardrails into the council graph as `guard_input` / `guard_output`
  nodes (Task 22). *Partially complete, stashed.*
- Evaluation harness and CI release gate (Tasks 23–25). **No code yet.** This
  is the highest-value remaining item: designing evaluation frameworks and
  release gates is explicitly on the target role's "ideally you'll also have"
  list, and few candidates have one.
- Postgres checkpointer for the council (Task 9). The current `MemorySaver`
  accumulates one thread per invocation with no eviction — a slow leak in a
  warm Lambda, live in production today.
- LangSmith tracing with `trace_id` persisted to the job row, so "click a job,
  see the trace that produced it" works.
- MCP server (Tasks 26–27). Cheap, and MCP is called "highly valuable" in the
  target role's description. The plan marks it cut-first if time runs out.

### Deliberately not doing

- Kubernetes. The workload is bursty and scale-to-zero matters at this cost
  base; serverless is the right answer and the tradeoff is worth explaining.
- Fine-tuning. Not achievable to a defensible standard in the time available.

---

## 4. Known defects carried forward

Verified present on 2026-09-25 and consciously deferred.

| Defect | Impact |
|---|---|
| `_check_fabrication` only inspects a ~15-skill blocklist in the Skills section | The plan's "fabrication before/after" metric is **not measurable** with it. Do not claim a RAG fabrication win. |
| Indeed scraper ~90% timeout rate over 30 days | One of nine sources is effectively dead |
| Three duplicate migration version prefixes (`20260409` ×2, `20260430` ×3) | `supabase db push` cannot run; migrations must go through the dashboard |
| CI never runs the frontend's own `lint` or `test` | Two confirmed UI bugs shipped because of this |
| `apply_geo_score_cap` lives in `shared/work_auth.py`, outside the guardrails package | The `fairness_cap` policy flag does not control it |
| Cover-letter compile count has no metric | Half of artifact generation is unmonitored |

---

## 5. Where the other documents stand

- `CLAUDE.md` — module map and conventions are useful; its **status table and
  deployment section are out of date** and are superseded by this file.
- `docs/superpowers/specs/` — design history and rationale. Valuable for "why",
  unreliable for "what is true now".
- `docs/superpowers/plans/` — task-level implementation plans. The
  2026-09-22 GenAI platform upgrade plan is the one currently being executed.
- `.superpowers/sdd/2026-09-22-*/audit-*.md` — the three audits of 2026-09-25
  (dashboard, system health, spec gaps). These are the evidence behind
  sections 2 and 4 above.
