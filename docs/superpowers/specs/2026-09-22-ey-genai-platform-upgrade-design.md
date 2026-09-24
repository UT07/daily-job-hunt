# NaukriBaba GenAI Platform Upgrade — Design

- **Date:** 2026-09-22
- **Status:** Approved for planning
- **Driver:** EY Ireland final-round technical deep-dive (< 1 week)
- **Approach:** "C" — two flagships (LangGraph, pgvector RAG) plus guardrails,
  eval release gate, and MCP server

## 1. Context and goal

NaukriBaba already implements several things the target role asks for, but
implements them under private names. The "AI council" in
`lambdas/pipeline/ai_helper.py` is an ensemble-with-critic pattern; the LaTeX
validators in `lambdas/pipeline/tailor_resume.py` are output guardrails; the
15-check CI pipeline is a release process. None of it is *legible* as such.

The goal is therefore not to add capability for its own sake. It is to:

1. Close three genuine capability gaps — no graph orchestration, no vector
   retrieval, no evaluation harness.
2. Give existing sophistication its industry-standard name, so it can be
   discussed in the vocabulary the role uses.
3. Produce artifacts that survive scrutiny: a graph that can be drawn, a trace
   that can be shown, an eval number that can be quoted, a bug that was fixed.

Every item below must be running in production on naukribaba.com and must be
defensible under ten minutes of questioning. An item that cannot meet both
tests is cut rather than shipped shallow.

## 2. Non-goals

- **Kubernetes.** The workload is bursty and scale-to-zero matters at this cost
  base. Serverless is the correct choice here; a toy cluster would be a worse
  answer than the tradeoff explanation.
- **Fine-tuning / PyTorch / Hugging Face training.** Not achievable to a
  defensible standard in the time available.
- **Rewriting the working JD to resume flow.** Everything is additive and
  flag-guarded. If all five workstreams were disabled, current production
  behaviour would be unchanged.
- **Replacing the existing provider failover.** LangGraph orchestrates;
  `_call_provider` still executes. The 11-provider resilience stays.

## 3. Decisions

### 3.1 Embedding provider: Google Gemini

`ai_client.py:237` already contains a `GeminiProvider` calling the
`generativelanguage` REST API directly over httpx with no SDK. Embeddings use
the same endpoint family, auth scheme and transport, so the integration cost is
hours rather than a day.

`text-embedding-004`, 768 dimensions. The dimension choice is deliberate:
768 halves index size and HNSW probe cost versus 1536, and retrieval quality on
a corpus of this size does not justify the larger vector.

Caveat on record: `ai_client.py:1200` notes Gemini is reserved for other
purposes. Embedding requests draw on a separate quota from generation and are
content-hash cached in the existing `ai_cache` table, so expected volume is low.
If the reservation still stands, substitute Jina embeddings v3 (1024 dims, free
tier, no card); nothing else in this design changes.

Rejected: `sentence-transformers` locally — torch is roughly 800MB and cannot
fit in a Lambda package.

### 3.2 Sequencing: LangGraph before pgvector

LangGraph is the spine. Guardrails wire in as graph nodes, the eval harness
measures the graph, and the MCP `score_job` tool calls it — three of five
workstreams depend on it. It is also the highest-risk item, because of Lambda
layer size. Discovering on Day 3 that the council must move to the
container-image Lambda would leave insufficient time to act on it.

Sequencing is therefore by dependency depth and technical risk, not by ease of
delivery.

### 3.3 Migration safety: engine flag with legacy default

`council_complete()` retains its exact signature. A `COUNCIL_ENGINE` parameter
read from SSM selects `legacy` or `langgraph`. Legacy remains the default
through deployment; the flag is flipped in production only after a parity test
passes and a live smoke run succeeds.

## 4. Architecture

Five new top-level packages, each independently testable:

```
agents/            LangGraph orchestration
  state.py         CouncilState TypedDict
  graph.py         build_council_graph()
  nodes.py         plan, generate, critique, gate, repair, finalize
  providers.py     adapter onto existing _call_provider

guardrails/        named safety layer
  input_guards.py  prompt-injection detection, PII scrub
  output_guards.py fabrication, banned phrases, LaTeX structure, fairness cap
  policy.py        declarative per-task policy
  types.py         GuardResult, Violation

retrieval/         vector layer
  embeddings.py    Gemini embedding client + ai_cache-backed cache
  store.py         pgvector queries
  dedup.py         semantic dedup (tier 4)
  bullets.py       bullet retrieval for tailoring

evals/             evaluation + release gate
  golden/          25 JD fixtures with expected outcomes
  harness.py       runner
  metrics.py       tier accuracy, fabrication rate, variance, latency, cost
  baseline.json    committed baseline the CI gate compares against

mcp_server/
  server.py        MCP tools over existing FastAPI endpoints
```

### 4.1 Deploy-path integrity

This repository has lost time twice to modules present in one deploy path but
absent from another — most recently `shared/` missing from `Dockerfile.lambda`.
Lazy imports mask the failure until runtime.

Five new packages is five repetitions of that failure mode. Mitigation is a CI
check, not a convention: a test asserts that every top-level application package
appears in both the layer build manifest and the Docker image, and fails the
build otherwise.

## 5. Workstream 1 — LangGraph council

### 5.1 Graph

```
START
  -> guard_input
  -> plan              select diverse providers across model families
  -> generate          fan-out via Send API, one branch per generator
  -> critique          cross-family numeric critic, 0-100 per candidate
  -> guard_output
  -> quality_gate      pass -> finalize -> END
                       fail -> repair -> critique   (max 2 attempts)
                       exhausted -> finalize, flagged best-effort
```

### 5.2 State

`CouncilState` is a TypedDict carrying: task kind, prompt, system, task
description, candidate list, critic scores, winner, guard report, repair attempt
count, and trace id.

### 5.3 Design decisions

**Parallel fan-out.** The current implementation iterates generators serially in
a `for` loop. The `Send` API runs them concurrently. This is a measurable
latency reduction, which is what makes the port an engineering change rather
than a relabelling. Before/after p50 and p95 must be recorded.

**Node bodies delegate to `_call_provider`.** The existing failover, rate
limiting and token-budget logic is preserved unchanged. This also keeps
`langchain-community` and provider SDKs out of the dependency tree, which is
what makes the layer budget work.

**Postgres checkpointer on Supabase.** Enables resume-after-timeout and closes
item 3.2 of the 2026-03-17 pipeline overhaul spec, which has been outstanding.

**The repair loop is Reflexion.** Critic violations are fed back as generator
input on retry, bounded at two attempts to cap cost and latency.

### 5.4 Day-1 gate: layer budget

`layer/` is 116MB unzipped and `layer-tectonic/` is 35MB, against a 250MB
unzipped Lambda limit — roughly 99MB of headroom. `langgraph` plus
`langchain-core` is expected to fit well inside that, but this must be measured
before any graph code is committed.

If it does not fit under a `--no-deps` install, the council moves into the
existing container-image Lambda, which has a 10GB limit. This decision is made
in the first 30 minutes of Day 1, not discovered later.

## 6. Workstream 2 — pgvector RAG

### 6.1 Schema

Migration `20260922000000_pgvector.sql`:

- `create extension if not exists vector`
- `alter table jobs add column embedding vector(768)`
- new table `resume_bullets` — id, user_id, section, text, embedding vector(768),
  source_resume_id, created_at
- HNSW index using `vector_cosine_ops` on both embedding columns
- RLS policies following the existing per-user pattern

### 6.2 Consumer A — semantic dedup as tier 4

`merge_dedup.py` currently applies three tiers: exact hash, exact company plus
title, and fuzzy title match. A fourth tier applies cosine similarity at or
above 0.93 on the job description embedding, scoped within a single company.

This fixes a logged production defect: the same role scraped under different
queries received different hashes and therefore different scores — "Backend
Software Engineer @ TREQS" scored both 78 and 68.

The threshold is tuned against labelled duplicate pairs drawn from existing
production data, not chosen by intuition.

### 6.3 Consumer B — bullet retrieval for tailoring

The job description is embedded and used to retrieve the eight most relevant
bullets (k=8, tuned in section 6.4) from the user's own resume bullet library. Those bullets are injected
into the tailoring prompt as an evidence pool.

Because retrieved bullets are facts already present in the user's resume,
grounding generation in them reduces fabrication. The existing
`_check_fabrication` guard provides the measurement: fabrication rate before and
after retrieval is a number this design must produce.

This is the clearest instance in the project of retrieval used as a
hallucination-mitigation control rather than as a feature.

### 6.4 Similarity search tuning

A benchmark script measures recall@k against latency across `ef_search` values,
sweeps k over {4, 8, 12} to confirm the k=8 default, and compares HNSW to
IVFFlat on this corpus. The output is a small table of real
numbers for the chosen configuration.

## 7. Workstream 3 — Guardrails

### 7.1 Output guards (extraction)

Moved from `tailor_resume.py` and `score_batch.py` into `guardrails/`, behaviour
unchanged and covered by existing tests:

- fabrication detection against base resume skills
- banned phrase detection
- LaTeX macro arity, brace balance, header presence, required sections,
  `\textbf` preservation
- work-authorisation score cap, reframed as a fairness control

### 7.2 Input guards (new)

Job descriptions are scraped from seven external job boards and passed directly
into LLM prompts. This is untrusted input reaching a model, and a description
containing instruction-shaped text is a live vulnerability in the current
system.

Defences:

- heuristic detection of instruction-injection patterns in scraped text
- explicit delimiter fencing around all untrusted content
- an instruction-hierarchy system prompt stating that fenced content is data
- PII scrubbing before dispatch to third-party free-tier providers, consistent
  with the existing `gdpr.py` obligations

### 7.3 Placement

Guards run as `guard_input` and `guard_output` nodes inside the graph, so the
safety layer is part of the architecture rather than appended to call sites.
Policy is declarative, versioned, and selected per task kind. Guards return a
`GuardResult` carrying passed status, violations and severity; the quality gate
consumes it.

## 8. Workstream 4 — Eval harness and release gate

### 8.1 Golden set

25 job description fixtures spanning score tiers and role archetypes, each with
expected outcomes. Scoring fixtures assert an expected tier within a one-tier
tolerance. Tailoring fixtures assert guardrail pass, required keyword presence
and absence of fabrication.

Judging combines deterministic assertions with LLM-as-judge for writing quality,
reusing the existing `score_writing_quality` function.

### 8.2 Metrics

Tier accuracy, guardrail pass rate, fabrication rate, p50 and p95 latency, cost
per run, and score variance across three repeated runs. The variance metric
directly measures the "score inconsistency" defect recorded in the project
backlog.

### 8.3 CI gate

A new `ai-eval` job in `ci.yml` runs on pull requests touching `agents/`,
`guardrails/`, `retrieval/` or prompt definitions. It compares results against
`evals/baseline.json` and fails the build if tier accuracy drops by more than 5
percentage points or if the fabrication rate rises at all.

The baseline moves only by deliberate, reviewed commit. Runs use free-tier
providers with caching enabled to keep cost negligible.

## 9. Workstream 5 — MCP server

Tools exposed over existing FastAPI endpoints:

- `search_jobs(query, tier, limit)` — semantic search over the pgvector store
- `score_job(jd_text)` — invokes the LangGraph council
- `tailor_resume(job_id or jd_text)` — enqueues work, returns a task id
- `get_job(job_id)`

Transport is stdio for local use plus an HTTP endpoint mounted on the existing
FastAPI application, so the server is genuinely deployed rather than a local
demo. Authentication reuses the existing Supabase JWT validation.

## 10. Observability

LangSmith tracing is enabled by environment variable once LangChain is present.
The council `trace_id` is persisted onto the `jobs` row, so any job in the
dashboard links to the trace that produced it.

## 11. Sequencing and cut line

| Day | Work |
| --- | --- |
| 1 (first 30 min) | Layer-size spike; layer versus container decision |
| 1 | LangGraph state, graph, nodes, provider adapter; parity tests green locally |
| 2 | Deploy behind flag, flip in prod, smoke, LangSmith traces live |
| 2 (PM) | pgvector migration, embeddings, semantic dedup to prod |
| 3 | Bullet retrieval RAG in tailoring; retrieval benchmark numbers |
| 4 | Guardrails extraction plus input injection guard, wired as nodes |
| 5 | Eval harness and CI release gate |
| 5.5 | MCP server — stretch |

**Cut line.** If Day 3 work is incomplete at end of Day 3, drop the MCP server
first, then the eval harness. Approach C carries no schedule slack by
construction; this clause makes the tradeoff explicit in advance rather than
discovering it on the final day.

## 12. Testing strategy

- Test-driven per repository norms; tests precede implementation.
- Unit coverage for each guard, each graph node, the embedding cache, and the
  dedup threshold.
- A legacy-versus-LangGraph parity test over shared fixtures is the safety net
  for the port, and is the precondition for flipping `COUNCIL_ENGINE`.
- One real job description driven end to end to a compiled PDF through the
  deployed Lambda before any batch run, per the standing "E2E before batch" rule.
- The existing 15 CI checks continue to gate merges, joined by `ai-eval`.

## 13. Risks

| Risk | Mitigation |
| --- | --- |
| LangGraph exceeds Lambda layer budget | Day-1 30-minute spike; fallback to container-image Lambda |
| Cold-start regression from larger imports | Measure; pipeline is asynchronous so a 1-2s increase is tolerable |
| Embedding API rate limits | Content-hash caching in `ai_cache`, batched requests |
| Production breakage during the port | Feature flag, legacy default, parity test, smoke before flip |
| New package missing from a deploy path | CI check asserting presence in both layer and Docker image |
| Schedule overrun | Cut line fixed in advance (section 11) |
| Gemini reservation conflict | Jina embeddings v3 as drop-in substitute |

## 14. Success criteria

1. `COUNCIL_ENGINE=langgraph` serving production traffic, with a recorded
   before/after latency comparison from the parallel fan-out.
2. Semantic dedup deployed, with the count of duplicate pairs it caught in
   production data.
3. Bullet-retrieval RAG deployed, with fabrication rate before and after.
4. Guardrails running as graph nodes, with an injection-attempt detection
   demonstrated against a crafted job description.
5. `ai-eval` gating pull requests against a committed baseline.
6. MCP server reachable, demonstrated by driving the live pipeline from an MCP
   client.

Each criterion is a number or a demonstration, not a completed checkbox.
