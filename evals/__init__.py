"""Evaluation harness and golden set for the AI scoring / tailoring pipeline.

Phase 5 of docs/superpowers/plans/2026-09-22-ey-genai-platform-upgrade.md
(Tasks 23-25). Full provenance and the corrections applied against that
plan's original assumptions are in
.superpowers/sdd/2026-09-22-ey-genai-platform-upgrade/task-23-25-report.md.

This package is CI/local tooling only — it is never imported by any deployed
Lambda (no CodeUri references it, it is in no layer, no `lambdas/pipeline/*`
module imports it). That is why it lives at the repo root instead of under
`lambdas/pipeline/` the way `agents/`, `guardrails/` and `retrieval/` do:
those three ship inside a Lambda's own package (see
tests/unit/test_deploy_path_parity.py for why), but nothing here ever runs
anywhere except pytest, `python -m evals.harness`, and GitHub Actions.
"""
import json
import pathlib

GOLDEN_DIR = pathlib.Path(__file__).parent / "golden"


def load_golden() -> list[dict]:
    """Every golden fixture, ordered by id for stable reporting."""
    return sorted(
        (
            json.loads(p.read_text())
            for p in GOLDEN_DIR.glob("*.json")
            if p.name != "manifest.json"
        ),
        key=lambda c: c["id"],
    )
