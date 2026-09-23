"""Resolves `ai_helper` across the three places this package actually runs.

Why this exists, and why `agents/` lives at `lambdas/pipeline/agents/`
(moved here 2026-09-23, previously a repo-root package): it makes `agents/`
a flat sibling of `ai_helper.py` in the two contexts that matter for a
plain `import ai_helper` to resolve:

1. Tests: `tests/conftest.py` does
   `sys.path.insert(0, PROJECT_ROOT / "lambdas" / "pipeline")`, so both
   `import agents` and `import ai_helper` resolve as flat top-level modules
   — no `lambdas` package involved at all.
2. The zip-based pipeline Lambdas (`score_batch`, `tailor_resume`,
   `generate_cover_letter`, ...): `template.yaml` gives them
   `CodeUri: lambdas/pipeline/`, which SAM/CFN FLATTENS into `/var/task/` at
   deploy time. `agents/` and `ai_helper.py` land there as flat siblings,
   identically to the test shape above — `ai_helper.council_complete`
   imports `agents.graph` at call time when `COUNCIL_ENGINE=langgraph`. The
   repo's own sibling modules already resolve `ai_helper` this same flat
   way: `lambdas/pipeline/tailor_resume.py` and
   `lambdas/pipeline/score_batch.py` both use `from ai_helper import ...`.

Both (1) and (2) are covered by the flat `import ai_helper` in the try
branch below — that is the whole point of moving `agents/` here: the test
shape and the deployed zip-Lambda shape are now identical.

3. The container-image Lambda (`jobhuntapi`, the FastAPI backend):
   `Dockerfile.lambda` does `COPY lambdas/ ${LAMBDA_TASK_ROOT}/lambdas/`,
   which keeps `lambdas/` as a real, importable package tree
   (`lambdas/__init__.py`, `lambdas/pipeline/__init__.py` both exist and
   ship). There is no flat `ai_helper.py` at `/var/task` in this shape —
   only `from lambdas.pipeline import ai_helper` resolves. This is the
   except branch below.

A previous version of `agents/providers.py` and `agents/nodes.py` imported
`lambdas.pipeline.ai_helper` directly. That resolved fine under pytest (which
runs from the repo root) and in the container-image Lambda (`jobhuntapi`,
which gets the full `lambdas/` package tree via `Dockerfile.lambda`), but
cannot resolve in the zip-based pipeline Lambdas once `CodeUri` flattens the
directory — `ModuleNotFoundError: No module named 'lambdas'`. Every test
passed; production failed. See tests/unit/test_deploy_path_parity.py for the
regression test and incident write-up.

This module tries the flat import FIRST, since that now covers both tests
and the deployed zip Lambda, and falls back to the qualified container-image
import only on ImportError. Re-exports exactly the names `agents/` consumes
today — no more — so this stays a thin resolution seam, not a second copy of
ai_helper's public API.
"""
try:
    import ai_helper  # flat import — resolves under pytest and in the zip Lambda (contexts 1-2 above)
except ImportError:
    from lambdas.pipeline import ai_helper  # container-image shape only (context 3 above)

_build_provider_list = ai_helper._build_provider_list
_call_provider = ai_helper._call_provider
_model_family = ai_helper._model_family
_select_diverse_providers = ai_helper._select_diverse_providers
_parse_critic_scores = ai_helper._parse_critic_scores
build_critique_prompt = ai_helper.build_critique_prompt
CRITIQUE_SYSTEM = ai_helper.CRITIQUE_SYSTEM
CRITIC_MAX_TOKENS = ai_helper.CRITIC_MAX_TOKENS
