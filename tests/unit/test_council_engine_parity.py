"""Every council caller runs the same engine, and CI measures that engine.

Two gaps, both found by reading the deployed stack rather than the template.

1. PostScoreFunction had no Environment block, so COUNCIL_ENGINE was unset and
   ai_helper.py:746 fell through to its "legacy" default. Measured against the
   live functions on 2026-09-29:

       naukribaba-tailor-resume          langgraph
       naukribaba-generate-cover-letter  langgraph
       naukribaba-score-batch            langgraph  (never calls the council)
       naukribaba-post-score             None       -> legacy

   Legacy never imports guardrails. post_score.py:129 passes task="score"
   expressly to get injection detection and PII scrubbing, and
   post_score.py:108-114 is the one handler that pipes raw third-party
   job-description text straight into a prompt. The caller with the strongest
   need for the guards was the only one running without them.

2. The CI ai-eval job set no COUNCIL_ENGINE either, so the gate measured the
   legacy serial engine — no guard nodes, no repair loop — while production
   runs the graph. Every guard_pass_rate the gate has reported describes an
   engine that is not deployed.

Note on the template default: CouncilEngine defaults to "legacy" in
template.yaml and is overridden to "langgraph" in samconfig.toml, which is
gitignored. A comment in ai_helper.py reasoned from the default and concluded
legacy was what ran in production. It was reading the template, not the stack.
That is why these assertions are about wiring — every caller gets the SAME
value from the SAME parameter — rather than about which engine is correct.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "template.yaml"
CI = ROOT / ".github/workflows/ci.yml"


def _template_resources():
    """Parse template.yaml with CFN's short tags stubbed out.

    yaml.load with a SafeLoader SUBCLASS is safe: SafeLoader's constructor
    table has no !!python/object handler, and the only constructors added below
    are for CloudFormation's own short tags, each of which returns plain
    scalars, lists and dicts. The unsafe forms are yaml.load with the default
    Loader, and yaml.unsafe_load. Keep the base class as SafeLoader — changing
    it is what would make this dangerous.
    """
    class Loader(yaml.SafeLoader):
        pass

    def passthrough(loader, node):
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        return loader.construct_mapping(node)

    for tag in ("!Ref", "!Sub", "!GetAtt", "!Join", "!Select", "!Split",
                "!FindInMap", "!ImportValue", "!Equals", "!If", "!Not", "!Condition"):
        Loader.add_constructor(tag, passthrough)
    return yaml.load(TEMPLATE.read_text(), Loader=Loader)["Resources"]


def _council_callers():
    """Lambda handlers whose source reaches council_complete."""
    callers = []
    for res_name, res in _template_resources().items():
        if res.get("Type") != "AWS::Serverless::Function":
            continue
        props = res.get("Properties", {})
        handler = props.get("Handler", "")
        code_uri = props.get("CodeUri", "")
        if not handler or not isinstance(code_uri, str):
            continue
        module = handler.rsplit(".", 1)[0]
        src = ROOT / code_uri / f"{module}.py"
        if not src.exists():
            continue
        text = src.read_text()
        if "council_complete" in text:
            callers.append((res_name, props))
    return callers


def test_at_least_one_council_caller_is_found():
    """Guard the guard — if the discovery breaks, the rest is vacuous."""
    names = [n for n, _ in _council_callers()]
    assert len(names) >= 2, f"only found {names}; the source scan is probably broken"


def test_every_council_caller_sets_the_engine():
    missing = []
    for name, props in _council_callers():
        variables = (props.get("Environment") or {}).get("Variables") or {}
        if "COUNCIL_ENGINE" not in variables:
            missing.append(name)
    assert not missing, (
        f"{missing} call council_complete but set no COUNCIL_ENGINE, so they "
        "silently run the frozen legacy engine — which never imports "
        "guardrails, whatever task= the caller asks for"
    )


def test_every_council_caller_reads_the_same_parameter():
    """One knob. Two functions on different engines is worse than either."""
    values = {}
    for name, props in _council_callers():
        variables = (props.get("Environment") or {}).get("Variables") or {}
        if "COUNCIL_ENGINE" in variables:
            values[name] = variables["COUNCIL_ENGINE"]
    assert len(set(values.values())) == 1, (
        f"council callers disagree on the engine source: {values}"
    )


def test_post_score_specifically_has_it():
    """Named because it is the regression, and the guard-neediest caller."""
    props = _template_resources()["PostScoreFunction"]["Properties"]
    variables = (props.get("Environment") or {}).get("Variables") or {}
    assert "COUNCIL_ENGINE" in variables, (
        "PostScoreFunction runs the guard-free legacy engine while asking for "
        'task="score" guardrails'
    )


def test_the_eval_gate_measures_a_named_engine():
    """A gate that measures an engine you do not deploy reports nothing useful."""
    text = CI.read_text()
    m = re.search(r"^\s*COUNCIL_ENGINE:\s*(\S+)\s*$", text, re.M)
    assert m, (
        "the ai-eval job sets no COUNCIL_ENGINE, so it measures ai_helper's "
        '"legacy" default while production runs the graph'
    )
    assert m.group(1) in ("legacy", "langgraph"), m.group(1)
