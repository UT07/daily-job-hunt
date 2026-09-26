"""MCP (Model Context Protocol) transport for the NaukriBaba pipeline.

Exposes `search_jobs`, `score_job`, and `get_job` as MCP tools over the same
Supabase-backed data and scoring code that already serves the REST API
(`app.py`) — see `mcp_server.server`. Lives at the repo root (not under
`lambdas/pipeline/`) because its only consumer is `app.py`, which ships in
the container-image Lambda; see `Dockerfile.lambda` and
`tests/unit/test_deploy_path_parity.py` for why that placement is correct
here even though `agents/`, `guardrails/`, and `retrieval/` all had to move
the other way.
"""
