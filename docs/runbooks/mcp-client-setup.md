# Connecting an MCP client to NaukriBaba

The MCP server (`mcp_server/`) exposes three tools over the same Supabase
data and scoring code that already serves the REST API:

- `search_jobs(query, limit=10)` — semantic search over scraped jobs
  (pgvector, with an automatic keyword fallback — see "search_jobs is keyword
  search until one migration is applied"), scoped to the calling
  user (see "Whose data the tools see"). Each result carries `match_mode`, so you can always tell which
  path served it.
- `score_job(jd_text, title="", company="", location=None, remote=None, resume_tex=None)`
  — 3-perspective AI score of a job description against the account's base
  resume (loaded from `user_resumes` when `resume_tex` is omitted). Returns
  `match_score`, `ats_score`, `hiring_manager_score`,
  `tech_recruiter_score`, `score_spread`, `tier`, `gaps`, `reasoning` — the
  same numbers under the same names as the REST rebuild path.
  **Pass `location`.** It feeds the scoring prompt *and* decides the
  geography / work-authorisation cap; omitting it gets you an uncapped score
  the dashboard will disagree with. Three uncached model calls per invocation
  — that is what the `score_spread` band is measured from.
- `get_job(job_hash)` — fetch one stored job's live score fields
  (`match_score`, `ats_score`, `score_tier`) and status.

`jd_text` and `resume_tex` come from the client, so both are treated as
untrusted: injection-checked (the call is **refused**, not quietly scored),
PII-scrubbed and fenced, with the instruction hierarchy declared in the
system prompt. See `score_batch._guard_untrusted_scoring_input`.

## Whose data the tools see

Every tool acts as exactly one user, and there is no default
(`mcp_server/identity.py`; a tool with no caller raises `NoCallerIdentity`).
Until 2026-10-08 every tool ran as one hard-coded owner account while the
HTTP gate only checked that a JWT was valid, so any signed-up user could read
the owner's jobs and spend the owner's model budget.

- **Remote (SSE):** the caller is the `sub` of the verified Supabase JWT.
  The session is also bound to that user: a `POST /mcp/messages/` carrying
  another user's token gets 404 for a session it did not open.
- **Local (stdio):** set `NAUKRIBABA_MCP_USER_ID` to the Supabase user id the
  process should act as. The server refuses to start without it. For
  `scripts/mcp_client_check.py --stdio`, put it in `.env`, which that script
  loads and passes to the child process.

## Verify it before you trust it

```bash
.venv/bin/python scripts/mcp_client_check.py            # both transports
.venv/bin/python scripts/mcp_client_check.py --stdio
.venv/bin/python scripts/mcp_client_check.py --sse      # mints an HS256 JWT
.venv/bin/python scripts/mcp_client_check.py --score    # also spends model calls
```

This drives the real `mcp` client SDK — the same library Claude Desktop and
Claude Code speak — against the real Supabase tables. It reads
`SUPABASE_URL`, `SUPABASE_SERVICE_KEY` and `SUPABASE_JWT_SECRET` from `.env`.

The stdio transport (the default run, and `--stdio`) also needs
`NAUKRIBABA_MCP_USER_ID`, from `.env` or the environment. Without it the
script exits before doing anything, with a message naming the variable;
`--sse` alone does not need it, because SSE acts as the minted JWT's `sub`.

Last run, 2026-09-30, on this branch:

| transport | tools/list | search_jobs | get_job | score_job |
|---|---|---|---|---|
| stdio | 3 tools | `match_mode: keyword`, real rows | real row | `n=3` spread, cap marker present |
| sse (uvicorn + minted JWT) | 3 tools | real rows | real row | not exercised |

## Claude Code (stdio, local)

```bash
claude mcp add naukribaba \
  --env NAUKRIBABA_MCP_USER_ID=<your supabase user id> \
  --env SUPABASE_URL=... --env SUPABASE_SERVICE_KEY=... --env GEMINI_API_KEY=... \
  -- /Users/ut/code/naukribaba/.venv/bin/python -m mcp_server.server
```

Run it from the repo root so the server's working directory is the repo.

## Claude Desktop (stdio, local)

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "naukribaba": {
      "command": "/Users/ut/code/naukribaba/.venv/bin/python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/Users/ut/code/naukribaba",
      "env": {
        "AWS_DEFAULT_REGION": "eu-west-1",
        "SUPABASE_URL": "https://<project>.supabase.co",
        "SUPABASE_SERVICE_KEY": "<service key>",
        "GEMINI_API_KEY": "<key>",
        "NAUKRIBABA_MCP_USER_ID": "<your supabase user id>"
      }
    }
  }
}
```

Use the venv's absolute interpreter path, not bare `python` — a system
`python` has none of the project's dependencies. Restart Claude Desktop; the
`naukribaba` tools appear in the tool list.

The credentials in `env` are what `ai_helper.get_param` reads first; without
them it falls back to SSM, which then needs AWS credentials and
`AWS_DEFAULT_REGION` in the same block. Either is fine — pick one and give it
everything it needs. `GEMINI_API_KEY` is only used by `search_jobs`, and only
once the semantic RPC exists.

The stdio transport has no authentication of its own — anyone who can run
this command on this machine can call the tools. That is acceptable for a
local, single-operator client only; it is why the remote transport below
requires a bearer token.

## Remote (SSE) — what works, and what still has to change

`/mcp` is mounted on the same FastAPI app as every `/api/*` route and
requires the same Supabase JWT (`RequireSupabaseJWT`, see
`mcp_server/http_auth.py`): a request without a valid `Authorization: Bearer
<token>` header gets a 401 before reaching the MCP transport, for every route
the transport defines (`/sse` and `/messages/`).

Point an SSE-capable MCP client at `https://<api-host>/mcp/sse` with
`Authorization: Bearer <supabase-jwt>`. Verified working against
`uvicorn app:app` on localhost with a minted HS256 token (see the script
above).

Two things stood between that and the deployed endpoint.

**1. `421 Invalid Host header` — fixed in this change, needs a deploy.**
Measured against production on 2026-09-30:

```
$ curl -s https://<api-host>/prod/mcp/sse
{"detail": "Missing authorization header"}                       # 401
$ curl -s -H 'Authorization: Bearer nope' https://<api-host>/prod/mcp/sse
{"detail": "Invalid or expired token"}                           # 401
$ curl -s -H "Authorization: Bearer <valid HS256 JWT>" https://<api-host>/prod/mcp/sse
Invalid Host header                                              # 421
```

The 421 is the MCP SDK's own DNS-rebinding protection
(`mcp/server/transport_security.py`). `FastMCP()` defaults `host="127.0.0.1"`
and auto-enables that check for a loopback host, allowing *only*
`127.0.0.1:*`, `localhost:*` and `[::1]:*`. So the JWT gate was never what
kept clients out — the transport rejected every request whose Host was not
localhost, which is every request that reaches API Gateway. That is why the
tool list had never been fetched by anything.

`mcp_server.server._transport_security` now reads `MCP_ALLOWED_HOSTS`
(comma-separated) and allows those hosts in addition to localhost, with the
protection still **on**; `template.yaml` passes the API Gateway host from the
`HttpApi` id. An unset variable fails closed to localhost-only.

**2. API Gateway buffering — not fixed, and not verified either way.**
`JobHuntApi` is served by `AWS::Serverless::HttpApi` with a Lambda proxy
integration, which does not support response streaming — only a Lambda
Function URL with `InvokeMode: RESPONSE_STREAM` does. An SSE `GET /mcp/sse`
holds its response open for the life of the session, so the expectation is
that it never flushes the first `event: endpoint` frame and dies on the
integration timeout instead.

This has **not** been confirmed against the deployed endpoint, because the
421 above returns before any streaming is attempted. After deploying the host
fix, run:

```bash
curl -N --max-time 40 -H "Authorization: Bearer <jwt>" \
  https://<api-host>/prod/mcp/sse
```

- an `event: endpoint` line within a second or two → streaming works, remote
  SSE is usable as documented
- a hang followed by a 5xx and no output → buffering confirmed; the remote
  transport needs a Lambda Function URL with `InvokeMode: RESPONSE_STREAM`
  (or an ALB) in front of the same app, and `MCP_ALLOWED_HOSTS` extended to
  that hostname

Until that check is run, **stdio is the supported client path** and the
remote endpoint is authenticated-but-unproven. Do not report the remote
transport as working on the strength of a successful deploy — CLAUDE.md
verification rule 1.

## search_jobs is keyword search until one migration is applied

`search_jobs` ranks by pgvector cosine similarity via the
`match_jobs_semantic` RPC
(`supabase/migrations/20260926000000_match_jobs_semantic.sql`). Confirmed live
on 2026-09-30, that RPC is **not in the production database**:

```
POST /rest/v1/rpc/match_jobs_semantic -> 404 PGRST202
  "Searched for the function public.match_jobs_semantic with parameters
   p_embedding, p_k, p_user_id ... but no matches were found in the schema
   cache"   (hint: "Perhaps you meant ... match_jobs_in_company")
```

`supabase db push` is blocked by three pre-existing duplicate `20260430`
migration version prefixes, so **apply that one file through the Supabase
dashboard SQL editor.** It is `create or replace`, so re-running it is a
no-op. 1,232 of this account's 1,280 job rows already carry an embedding, so
semantic ranking starts working on the next call — no client-side change.

Until then the tool falls back to an `ilike` keyword search over the same
`jobs` table and returns real results, with `match_mode: "keyword"` on every
row. It no longer buys a Gemini embedding first: whether the RPC exists is
settled with a `p_k=0`, zero-vector probe (one database round trip, no vector
work, no model call) whose verdict is cached for the process. A long-running
process can pick the migration up without a restart via
`mcp_server.server.reset_semantic_probe()`.

## Demo script

1. Ask the client: "search my jobs for platform engineering roles"
2. Ask: "score this JD against my resume, the role is in Dublin" and paste a
   description
3. Ask: "get job `<hash>`" using a hash from step 1
