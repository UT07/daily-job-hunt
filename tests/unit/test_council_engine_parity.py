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


# ---------------------------------------------------------------------------
# 3. The API container is outside _council_callers()' scope entirely.
#
# JobHuntApi is PackageType: Image with no Handler and no CodeUri, so the
# discovery above skips it at `if not handler or not isinstance(code_uri,
# str)`. It sets no COUNCIL_ENGINE, and the tests above pass anyway -- not
# because the container is safe but because nothing looked. A check that
# silently excludes part of the population it is meant to judge reports a
# clean result about the wrong set.
#
# It happens to be safe today: app.py reaches only
# score_batch.score_single_job_deterministic and suggest_sections, and
# neither calls the council (suggest_sections uses ai_complete_cached, a
# single-model call). That is a fact about today's imports, not a property of
# the deployment, and it is what these tests pin.
#
# The stakes if it changes: the container would run the frozen legacy engine,
# which imports no guardrails at all, so injection detection, PII scrub and
# the fabrication check would all be absent on a user-facing path -- and the
# obvious one-line fix is a trap. requirements-web.txt carries no langgraph,
# so setting COUNCIL_ENGINE=langgraph on this container alone would raise
# ImportError at the call site rather than enable the graph.
# ---------------------------------------------------------------------------

import ast  # noqa: E402  (grouped with the section it serves)

APP = ROOT / "app.py"
PIPELINE = ROOT / "lambdas" / "pipeline"


def _calls_council(path):
    """True if the module CALLS council_complete, by AST rather than substring.

    score_batch.py contains the string in prose and in an import it does not
    invoke; ai_helper.py defines it. A text match flags all three, so a
    substring version of this test would fail on arrival and get deleted or
    weakened rather than believed.
    """
    try:
        tree = ast.parse(path.read_text())
    except (OSError, SyntaxError):
        return False
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = fn.attr if isinstance(fn, ast.Attribute) else getattr(fn, "id", None)
        if name == "council_complete":
            return True
    return False


def _pipeline_modules_reachable_from_app():
    """Pipeline modules app.py imports, plus what those import, within pipeline."""
    tree = ast.parse(APP.read_text())
    frontier, seen = [], set()
    for node in ast.walk(tree):
        mod = None
        if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("lambdas.pipeline"):
            tail = (node.module or "").removeprefix("lambdas.pipeline").lstrip(".")
            mod = tail or None
            if mod is None:  # `from lambdas.pipeline import x, y`
                frontier.extend(a.name for a in node.names)
                continue
            frontier.append(mod)
    while frontier:
        mod = frontier.pop()
        if mod in seen:
            continue
        seen.add(mod)
        path = PIPELINE / f"{mod.replace('.', '/')}.py"
        if not path.exists():
            continue
        sub = ast.parse(path.read_text())
        for node in ast.walk(sub):
            if isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                cand = node.module.split(".")[0]
                if (PIPELINE / f"{cand}.py").exists() and cand not in seen:
                    frontier.append(cand)
    return sorted(seen)


def test_the_api_container_is_skipped_by_the_caller_scan():
    """Guard the gap, so the next reader knows the clean result above is partial."""
    props = _template_resources()["JobHuntApi"]["Properties"]
    assert props.get("PackageType") == "Image"
    assert "Handler" not in props and "CodeUri" not in props
    assert "JobHuntApi" not in [n for n, _ in _council_callers()]


def test_the_discovery_finds_real_pipeline_modules():
    """Guard this guard too -- an empty walk would make the next test vacuous."""
    mods = _pipeline_modules_reachable_from_app()
    assert "suggest_sections" in mods and "score_batch" in mods, mods


def test_no_council_caller_is_reachable_from_the_api_container():
    """Either the API never calls the council, or it must select the engine.

    If this fails, adding COUNCIL_ENGINE to JobHuntApi is only half the fix:
    langgraph and langchain-core have to reach the image via
    requirements-web.txt as well, because the shared-deps layer does not
    serve the container.
    """
    offenders = [
        m for m in _pipeline_modules_reachable_from_app()
        if _calls_council(PIPELINE / f"{m}.py")
    ]
    if offenders:
        variables = (
            _template_resources()["JobHuntApi"]["Properties"].get("Environment") or {}
        ).get("Variables") or {}
        assert "COUNCIL_ENGINE" in variables, (
            f"app.py now reaches council_complete via {offenders}, but JobHuntApi "
            "sets no COUNCIL_ENGINE, so those calls run the guard-free legacy "
            "engine on a user-facing path. Set it AND add langgraph to "
            "requirements-web.txt -- the container does not use the shared layer."
        )


def test_the_ast_check_can_tell_a_call_from_a_mention():
    """Pins the distinction the substring version got wrong."""
    assert _calls_council(PIPELINE / "tailor_resume.py") is True
    assert _calls_council(PIPELINE / "suggest_sections.py") is False
