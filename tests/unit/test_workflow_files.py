"""Workflow files must parse the way GitHub Actions parses them.

On 2026-10-07 commit 82159a2 added a second `env:` block to a step in
`.github/workflows/ci.yml` that already had one. Every push to main from that
commit onward died instantly:

    X This run likely failed because of a workflow file issue.

with no jobs, no logs and no annotations -- so unit-tests and the AI eval gate
stopped running on main entirely, for four consecutive merges, while the PR
checks for each of those merges were green.

Two things made it invisible, and the second is the one worth keeping:

  * it fails on `push` and not on `pull_request`, so a PR cannot see it
  * `yaml.safe_load` accepts duplicate mapping keys and silently keeps the
    LAST one. So local validation reported the file was fine AND quietly
    discarded the key that had just been added -- `EVAL_DEADLINE_S` was never
    set, which is the opposite of what the commit claimed to do.

A validator that tolerates what the real consumer rejects is worse than no
validator: it converts a loud failure into a silent one. CLAUDE.md #12 -- fix
the instrument before trusting the reading.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

WORKFLOWS = sorted((Path(__file__).resolve().parents[2] / ".github/workflows").glob("*.y*ml"))


class _RejectsDuplicateKeys(yaml.SafeLoader):
    """SafeLoader with Actions' rule: a duplicate mapping key is an error."""


def _no_duplicate_keys(loader, node, deep=False):
    seen: dict = {}
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.YAMLError(
                f"duplicate key {key!r} at line {key_node.start_mark.line + 1} "
                f"(first seen at line {seen[key]}) — GitHub Actions rejects the "
                f"whole file for this; yaml.safe_load would silently keep the last"
            )
        seen[key] = key_node.start_mark.line + 1
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_RejectsDuplicateKeys.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _no_duplicate_keys)


def test_there_are_workflow_files_to_check():
    """A glob that matches nothing makes every test below vacuously true."""
    assert WORKFLOWS, "no workflow files found — this suite is asserting nothing"


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_no_duplicate_keys(path: Path):
    """The defect class that took CI off main for four merges."""
    try:
        yaml.load(path.read_text(), _RejectsDuplicateKeys)
    except yaml.YAMLError as exc:
        pytest.fail(f"{path.name}: {exc}")


@pytest.mark.parametrize("path", WORKFLOWS, ids=lambda p: p.name)
def test_every_job_has_at_least_one_step(path: Path):
    """A job with no steps is accepted by YAML and rejected by Actions, and is
    the shape a bad merge leaves behind."""
    doc = yaml.load(path.read_text(), _RejectsDuplicateKeys)
    for name, job in (doc.get("jobs") or {}).items():
        if "uses" in job:   # a reusable-workflow call has no steps by design
            continue
        assert job.get("steps"), f"{path.name}: job {name!r} has no steps"


def test_the_detector_actually_detects():
    """CLAUDE.md #6 in miniature: prove the instrument fires before trusting a
    clean result from it. Without this, a broken loader makes every test above
    pass on any file at all."""
    with pytest.raises(yaml.YAMLError, match="duplicate key"):
        yaml.load("step:\n  env:\n    A: 1\n  env:\n    B: 2\n", _RejectsDuplicateKeys)
    # ...and does not fire on the merged form the fix produced
    assert yaml.load("step:\n  env:\n    A: 1\n    B: 2\n", _RejectsDuplicateKeys) == {
        "step": {"env": {"A": 1, "B": 2}}}
