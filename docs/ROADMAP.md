# NaukriBaba — Roadmap & Current State

**Last verified: 2026-09-25.** Every number here was checked against the live
system or the code on that date, not copied from an older document. Where
something could not be verified it says so.

This file supersedes the status claims scattered across `CLAUDE.md`, the nine
specs in `docs/superpowers/specs/` and the fourteen plans in
`docs/superpowers/plans/`. Those remain useful as design history; this is the
only file that claims to describe the present.

---

## 1. What this is

A job-hunt automation system with two modes that share one core:

- **Batch** — a Step Functions pipeline scrapes job boards, deduplicates,
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
| Not expired | **64** |
| Not expired **and scored** | **0** |
| Never scored (`pending`) | **1,137** |
| Jobs with a tailored PDF | 39 |
| Test suite | 1,252 passing, 46 skipped |

**The pipeline ingests roughly twelve times faster than it can evaluate.**
Groq's free tier caps scoring at ~80–120 jobs/day; scrape volume runs ~1,500/day.
This is the binding constraint on everything else, and the reason the dashboard
looks empty.

### Cost

AWS is ~$1–3/month attributable to this project (verified via Cost Explorer;
the account is shared with unrelated projects whose RDS costs dominate the bill).
Off-AWS cost — Bright Data Web Unlocker and Apify — **could not be verified**
from the development environment and needs a console login.

---

## 3. Roadmap to 2026-10-01

Ordered by dependency, not by preference.

### Must land before the EventBridge schedules are enabled

1. **Match intake to scoring capacity.** Tighten the relevance pre-filter in
   `merge_dedup.py` so ~150 jobs/day reach scoring instead of ~1,500. Tune it
   against the real corpus and verify the rejected set does not contain good
   jobs. *In progress.*
2. **Remove the 7-day backfill cutoff.** Jobs that miss their scoring window
   are currently lost permanently rather than delayed. *In progress.*
3. **Rescore the backlog.** 1,137 pending jobs, of which the 64 live ones
   matter most. Needs a resumable, rate-limited script and a deliberate
   decision to spend the quota. *Script in progress; the run is the user's call.*
4. ~~Fix the `ArtifactsCompiled` metric field mismatch~~ — **done** (`0b43dd3`).
5. ~~Subscribe an address to the alarm topic~~ — **done in `template.yaml`**;
   requires `sam deploy` and then clicking AWS's confirmation email.
6. ~~Fix `send_followup_reminders` querying a nonexistent column~~ — **done**
   (`3b9c832`). It had failed 100% of runs since 2026-08-12.
7. **Verify the LangGraph council at pipeline scale.** It was cut over less
   than 48 hours ago and has only been exercised on single jobs.

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
