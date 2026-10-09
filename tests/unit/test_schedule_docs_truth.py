"""What the docs say about the EventBridge schedules must match template.yaml.

#93 (339ccb3, 2026-09-26) set all four rules back to State: ENABLED, but each
rule kept a "RETIRED 2026-09-02 ... parked" comment above that line, and
CLAUDE.md said in two places that the schedules were disabled. Anyone reading
either would conclude the pipeline (and its Bright Data / Apify spend) was off.
The template is the source of truth, so the prose is checked against it.
"""
from __future__ import annotations

import re
from pathlib import Path

from tests.unit.test_deploy_path_parity import _load_template_tolerant_of_cfn_tags

ROOT = Path(__file__).resolve().parents[2]


def _rule_states() -> dict[str, str]:
    res = _load_template_tolerant_of_cfn_tags()["Resources"]
    return {n: r["Properties"]["State"] for n, r in res.items()
            if isinstance(r, dict) and r.get("Type") == "AWS::Events::Rule"}


def _rule_blocks() -> dict[str, str]:
    text = (ROOT / "template.yaml").read_text()
    out = {}
    for name in _rule_states():
        m = re.search(rf"(?ms)^  {name}:\n(.*?)(?=^  \S)", text)
        assert m, name
        out[name] = m.group(1)
    return out


def test_there_are_four_schedules():
    assert len(_rule_states()) == 4, _rule_states()


def test_an_enabled_rule_is_not_described_as_retired():
    states = _rule_states()
    wrong = [n for n, block in _rule_blocks().items()
             if states[n] == "ENABLED" and re.search(r"RETIRED|pipeline is parked", block)]
    assert not wrong, f"State: ENABLED but the comment says retired/parked: {wrong}"


def test_claude_md_agrees_with_the_template():
    text = (ROOT / "CLAUDE.md").read_text()
    states = set(_rule_states().values())
    if states == {"ENABLED"}:
        assert not re.search(r"schedules were disabled|currently DISABLED", text), (
            "CLAUDE.md says the schedules are disabled; template.yaml has them ENABLED")
    elif states == {"DISABLED"}:
        assert "ENABLED since" not in text
