# Agent orchestration: LangGraph, LangSmith, MCP and a ReAct loop

**Date** 2026-09-30 · **Status** design · **Supersedes nothing**

## 1. The problem, stated from measurements

The request was "orchestrate LangGraph properly, get LangSmith working, do MCP,
and demonstrate a ReAct loop — everything is flaky and feels like spaghetti."

Three of those four already exist in some form. What follows is what each one
actually does today, measured, because the fix order depends on it.

### 1.1 The council deliberates 26% of the time

`agents/graph.py` is a real `langgraph.graph.StateGraph` (langgraph 1.2.12, in
the deployed layer): `guard_input → plan → generate (N parallel via Send) →
critique → guard_output → {finalize | repair} → plan`. That is a deliberation
loop and it is live on tailoring and cover letters.

The critic is where it fails. `critique_node` (agents/nodes.py:120-156) has
**four paths that silently return `candidates[0]`**:

| # | condition | logged? |
|---|---|---|
| 1 | `len(candidates) == 1` — a generator failed | info |
| 2 | `select_critic(used)` returns `None` — no unused family available | **nothing** |
| 3 | the critic call returns falsy | warning |
| 4 | the critic's output does not parse | warning |

Measured over 80 rounds: **the critic produced a usable verdict in 21. The
other 59 took candidate 1 unadjudicated.** 74%.

The return shape is identical in every case except `scores: []`, so no caller
can distinguish "the council chose this" from "the council gave up". That is
the same defect as a Step Function ending in `Succeed` after catching an error
— see rule 2 in CLAUDE.md's Verification rules.

**This is the orchestration bug.** Not a missing loop.

### 1.2 LangSmith is wired and dead

`_configure_langsmith_tracing()` (agents/graph.py:252) is called, and
`LANGCHAIN_TRACING_V2: "true"` is set on four functions in template.yaml. The
API key is rejected with 403 and tracing self-disables once per container.
**Zero traces have ever been exported.**

So the 74% above had to be measured by grepping CloudWatch for log strings.
Every question about the graph's behaviour currently costs a log-archaeology
session.

### 1.3 MCP is deployed, authenticated, and has no clients

`/mcp/sse` is live behind `RequireSupabaseJWT` — verified by an unauthenticated
401 and a bad-token 401 against the deployed host. Three tools
(`search_jobs`, `score_job`, `get_job`) are registered. **Neither transport has
ever been connected by anything.**

Two defects inside it:
- `score_job` never applies `apply_geo_score_cap` and passes no location, so it
  can return S-tier where the pipeline caps at B.
- `search_jobs` depends on an RPC that was never applied to the production
  database, so every call falls through to an `ilike` keyword search — after
  paying for a Gemini embedding it then discards.

### 1.4 Two of everything

| concern | copy A | copy B |
|---|---|---|
| AI client | `ai_client.py` (API/Studio) | `lambdas/pipeline/ai_helper.py` |
| council engine | LangGraph graph | frozen legacy serial, env-selected |
| LaTeX escaping | `parse_sections._escape_tex` | `latex_compiler._sanitize_latex` |
| résumé parsing | `resume_parser.parse_resume_sections` | `parse_sections.parse_resume_sections` |
| artifact key | `web/{date}/resumes/…` | `users/{uid}/resumes/{hash}_tailored.*` |

This is the "spaghetti". It is not aesthetic: **every defect found on
2026-09-29 was "the guard existed, on the other copy."** #135's two retired
model defaults survived because the guard tested `ai_helper` only. `post_score`
ran the legacy engine because only it lacked an `Environment` block.

## 2. Principle

A loop you cannot observe is not an architecture, it is a guess. Every phase
below produces a **measurement** before it produces a feature, and no phase
claims success without a number that was not available before it.

This follows CLAUDE.md's Verification rules; rules 1, 2 and 5 are the ones this
work is most likely to violate.

## 3. Phases

Each is independently shippable and independently useful. Later phases depend
on earlier measurements, not merely on earlier code.

### Phase 0 — See the loop (LangSmith)

*Rationale: everything after this is cheaper once traces exist.*

1. Establish why the key 403s. It is read from SSM
   `/naukribaba/LANGSMITH_API_KEY`; confirm whether the parameter is absent,
   stale, or the wrong project. **Do not handle the key** — report exactly what
   the user must rotate and where.
2. `_configure_langsmith_tracing` must report its outcome once per container at
   a level that reaches CloudWatch, naming the failure. Currently a 403
   disables tracing and the system looks identical to one with tracing off.
3. Add a `trace_id` to `CouncilState` if absent, and log it alongside the
   existing `[council]` lines so a CloudWatch line can be tied to a trace.
4. **Exit criterion:** one real tailoring run appears in LangSmith with all
   seven nodes, or a precise statement of what the user must rotate.

### Phase 1 — Make the degradation countable

*No behaviour change. Measurement only. This is how Phase 2 is judged.*

1. `critique_node` returns a `critique_outcome` on state: one of
   `adjudicated | single_candidate | no_critic_family | critic_call_failed |
   critic_unparseable`.
2. Every path logs, including `no_critic_family`, which logs nothing today.
3. `council_complete` surfaces the outcome to its caller, and `tailor_resume`
   records it alongside `tailoring_model` so the rate is queryable from the
   database rather than from log archaeology.
4. Add `agents/*` and `critique_outcome` to the eval harness report.
5. **Exit criterion:** the 26%/74% split is reproducible from one query, and a
   test asserts each of the five outcomes is reachable.

### Phase 2 — Make the critic adjudicate

Fix causes in the order Phase 1's counts say they matter. Expected, from the
earlier sample:

- `no_critic_family` — `select_critic` requires a family unused by the
  generators. With 5 families live and 2 consumed by generators, a cold pool
  with cooled providers can leave none. **Fallback: allow a same-family critic
  and record that it was same-family, rather than skipping adjudication.** A
  same-family verdict is weaker than a cross-family one and much stronger than
  none.
- `critic_unparseable` — `_parse_critic_scores` expects a shape. Log the raw
  output on failure (truncated) so the shape can be fixed rather than guessed.
- `critic_call_failed` — should now be rarer after #141's retry fix; confirm
  against Phase 1 counts before touching it.

**Exit criterion:** adjudication rate measured before and after, on the same
golden set. A number, not an assertion.

### Phase 3 — One client

The duplication is the flakiness. Consolidate deliberately, not wholesale.

1. Inventory every behaviour that exists in one client and not the other
   (quarantine codes, cooldown scoping, retry-on-total-failure from #141,
   rate limiters, the model registry guard).
2. Move the council to a single implementation used by both entry points.
   `ai_client.AIClient` is the older and richer of the two; `ai_helper` owns
   the cooldown table and the registry. Neither is simply the winner — the
   inventory decides per behaviour.
3. Retire the legacy serial engine once the eval gate measures the graph
   (already true as of #134).
4. **Exit criterion:** `test_ai_council_models.py` guards ONE pool, and a test
   asserts no second council implementation exists.

### Phase 4 — A ReAct loop where it earns its place

Not on top of the council. **Inside the repair path**, which is today a fixed
two-attempt retry that cannot explain itself.

Tailoring already has the ingredients of a genuine agent loop:

| tool | exists as |
|---|---|
| `score_resume` | `score_batch.score_single_job_deterministic` |
| `compile_resume` | `latex_compiler.compile_tex_to_pdf` |
| `retrieve_evidence` | `retrieval.bullets.retrieve_evidence` |
| `check_composition` | `shared.composition_policy.check_output` |
| `check_pages` | the compiled-PDF page check |

A ReAct agent over these can do what the fixed retry cannot: observe *why* the
output failed (too many projects? three pages? score dropped?), choose the
tool that addresses that specific failure, and iterate with a budget.

Constraints:
- Bounded: max N tool calls, max wall-clock, and the existing behaviour as the
  fallback when the budget is exhausted. A résumé must still be produced.
- Every tool call traced (Phase 0) so the loop is inspectable.
- The demonstration is a real tailoring run whose trace shows the agent
  choosing different tools for different failures — not a scripted path.

**Exit criterion:** a traced run where the agent recovers from a composition
violation and a page-count violation by different routes, plus the measured
quality delta against the fixed retry on the golden set.

### Phase 5 — MCP: a client, and parity

1. Give it one real client and document the registration, so "deployed" stops
   meaning "unreachable in practice".
2. Fix `score_job`'s divergence: apply `apply_geo_score_cap`, pass location,
   return the same shape as the REST path including `score_spread`.
3. Either apply the missing RPC or remove the discarded embedding round trip
   from `search_jobs`. Paying for an embedding and then ignoring it is worse
   than not embedding.
4. `score_batch` does not import guardrails, so `mcp_server.score_job` passes
   client-supplied `jd_text` and `resume_tex` straight into a prompt. Close
   that, or state precisely why it is acceptable.

**Exit criterion:** a client calls each tool successfully, and `score_job`
returns what the REST path returns for the same input.

## 4. Sequencing and what it costs to get wrong

Phase 0 first is the load-bearing decision. Every later phase is judged by a
measurement, and without traces those measurements cost a log-archaeology
session each.

Phase 4 before Phase 2 would be the expensive mistake: a ReAct loop layered on
a council that adjudicates 26% of the time would inherit that unreliability and
make it harder to see, not easier.

Phase 3 can run in parallel with 1 and 2 — it touches different files — but not
in parallel with 4, which needs a settled client.

## 5. Explicitly not in scope

- Rewriting the pipeline's Step Functions orchestration. It works and its
  failure modes are understood.
- Replacing LangGraph. It is the real library and the graph shape is sound.
- Multi-tenancy. The header block still hardcodes one person's contact details
  (a known, asserted gap) and that is a separate piece of work.
