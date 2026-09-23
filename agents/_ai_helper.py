"""Resolves `ai_helper` across this repo's two Lambda deploy paths.

Why this exists: `template.yaml` gives the pipeline Lambda functions
`CodeUri: lambdas/pipeline/`, which SAM/CFN FLATTENS into `/var/task/` at
deploy time. In that deployed reality there is no `lambdas` package and no
`lambdas.pipeline` package on the path — `ai_helper.py` sits flat at the task
root, and the only import that resolves is the bare `import ai_helper`. The
repo's own sibling modules already do it this way: `lambdas/pipeline/
tailor_resume.py` and `lambdas/pipeline/score_batch.py` both use
`from ai_helper import ...`.

Everything under `agents/` is copied into the shared layer by
`layer/build.sh` and runs inside that same flattened Lambda (imported at call
time from `ai_helper.council_complete` when `COUNCIL_ENGINE=langgraph`), so it
must resolve `ai_helper` the same flat way. But `agents/` is also imported
directly by this repo's test suite and by local tooling running from the
repo root, where `lambdas/` is an ordinary package (`lambdas/__init__.py`,
`lambdas/pipeline/__init__.py` both exist) and only
`from lambdas.pipeline import ai_helper` is guaranteed to resolve to the
right module.

A previous version of `agents/providers.py` and `agents/nodes.py` imported
`lambdas.pipeline.ai_helper` directly. That resolved fine under pytest (which
runs from the repo root) and in the container-image Lambda (`jobhuntapi`,
which gets the full `lambdas/` package tree via `Dockerfile.lambda`), but
cannot resolve in the zip-based pipeline Lambdas once `CodeUri` flattens the
directory — `ModuleNotFoundError: No module named 'lambdas'`. Every test
passed; production failed. See tests/unit/test_deploy_path_parity.py for the
regression test and incident write-up.

This module tries the flat import FIRST, since that is the deployed-Lambda
reality this shim exists to serve, and falls back to the repo-root/test
import. Re-exports exactly the names `agents/` consumes today — no more —
so this stays a thin resolution seam, not a second copy of ai_helper's
public API.
"""
try:
    import ai_helper  # flat /var/task import — the zip Lambda deploy shape
except ImportError:
    from lambdas.pipeline import ai_helper  # repo-root / test / container shape

_build_provider_list = ai_helper._build_provider_list
_call_provider = ai_helper._call_provider
_model_family = ai_helper._model_family
_select_diverse_providers = ai_helper._select_diverse_providers
_parse_critic_scores = ai_helper._parse_critic_scores
build_critique_prompt = ai_helper.build_critique_prompt
CRITIQUE_SYSTEM = ai_helper.CRITIQUE_SYSTEM
CRITIC_MAX_TOKENS = ai_helper.CRITIC_MAX_TOKENS
