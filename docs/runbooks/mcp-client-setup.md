# Connecting an MCP client to NaukriBaba

The MCP server (`mcp_server/`) exposes three tools over the same Supabase
data and scoring code that already serves the REST API:

- `search_jobs(query, limit=10)` — semantic search over scraped jobs
  (pgvector, with an automatic keyword fallback — see "Known limitation"
  below), scoped to the single configured account.
- `score_job(jd_text, resume_tex=None)` — 3-perspective AI score of a job
  description against the account's base resume (loaded from `user_resumes`
  when `resume_tex` is omitted).
- `get_job(job_hash)` — fetch one stored job's live score fields
  (`match_score`, `ats_score`, `score_tier`) and status.

## Claude Desktop (stdio, local)

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "naukribaba": {
      "command": "python",
      "args": ["-m", "mcp_server.server"],
      "cwd": "/Users/ut/code/naukribaba",
      "env": { "AWS_DEFAULT_REGION": "eu-west-1" }
    }
  }
}
```

`python` must resolve to the project's `.venv` interpreter (activate it, or
use the venv's absolute `python` path in `command`). Restart Claude Desktop;
the `naukribaba` tools appear in the tool list.

The stdio transport has no authentication of its own — anyone who can run
this command on this machine can call the tools. That is acceptable for a
local, single-operator client only; it is why the remote transport below
requires a bearer token.

## Remote (SSE, deployed)

`/mcp` is mounted on the same FastAPI app as every `/api/*` route and
requires the same Supabase JWT (`RequireSupabaseJWT`, see
`mcp_server/http_auth.py`) — a request without a valid `Authorization:
Bearer <token>` header gets a 401 before it reaches the MCP transport at
all, for every route the transport defines (today `/sse` and `/messages`).

Point an SSE-capable MCP client at:

```
https://<api-host>/mcp/sse
```

with header:

```
Authorization: Bearer <supabase-jwt>
```

## Demo script

1. Ask the client: "search my jobs for platform engineering roles"
2. Ask: "score this JD against my resume" and paste a description
3. Ask: "get job `<hash>`" using a hash from step 1

## Known limitation: search_jobs keyword fallback

`search_jobs` ranks by pgvector cosine similarity via the
`match_jobs_semantic` RPC
(`supabase/migrations/20260926000000_match_jobs_semantic.sql`). That
migration is written but **not yet applied** — this repo's `supabase db
push` is currently blocked by three pre-existing duplicate migration
version prefixes, so migrations go through the Supabase dashboard SQL
editor instead (see the migration file's header). Until it is applied,
`search_jobs` catches the resulting "function not found" error and falls
back to a live keyword (`ilike`) search over the same `jobs` table, so the
tool still returns real results today. Each result's `match_mode` field
says which path served it (`"semantic"` or `"keyword"`). No client-side
change is needed when the migration lands — the same tool call
automatically starts using true semantic ranking.
