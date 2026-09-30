"""The template must stay deployable, not merely buildable.

On 2026-09-30, six consecutive deploys failed. `MCP_ALLOWED_HOSTS` had been set
to `!Sub "${HttpApi}.execute-api.${AWS::Region}.amazonaws.com"` inside the
JobHuntApi function's Environment, so the FUNCTION referenced the API — while
SAM's generated API body already references the function's ARN. CloudFormation
refuses that at changeset creation:

    Circular dependency between resources: [ApiGateway5xxAlarm,
    PipelineHealthDashboard, JobHuntApi, HttpApi,
    JobHuntApiApiGatewayPermission, JobHuntApiTaskWorker, HttpApiprodStage]

Nothing caught it. `sam build` succeeds because no changeset is created, and
the Deploy Readiness job ran bare `sam validate`, which does not check
dependency cycles. cfn-lint's E3004 reports it in under a second, and
`sam validate --lint` was one word away the whole time (CLAUDE.md rule 12).

Worse, the failure was invisible for hours because a SECOND bug — an unset
CEREBRAS_API_KEY making `sam deploy` reject its own --parameter-overrides —
killed the deploy at CLI parse, before CloudFormation was ever called. Fixing
the first bug is what revealed this one.

CI now runs `sam validate --lint`, which is the real gate. These tests guard
the wiring around it: that the gate is switched on, that it has not been
silenced for the rule that matters, and that the specific Ref is not
reintroduced.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "template.yaml"
CFNLINTRC = ROOT / ".cfnlintrc.yaml"
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"
READINESS = ROOT / ".github" / "workflows" / "test.yml"


def _template() -> dict:
    class Loader(yaml.SafeLoader):
        pass

    def passthrough(loader, node):
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        return loader.construct_mapping(node)

    for tag in ("!Ref", "!Sub", "!GetAtt", "!Join", "!Select", "!Split", "!FindInMap",
                "!ImportValue", "!Equals", "!If", "!Not", "!Condition", "!And", "!Or",
                "!Base64", "!Cidr", "!GetAZs", "!Transform"):
        Loader.add_constructor(tag, passthrough)
    return yaml.load(TEMPLATE.read_text(), Loader=Loader)


def _strings(value) -> list[str]:
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in _strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in _strings(v)]
    return []


def _functions() -> dict:
    return {
        name: res for name, res in _template()["Resources"].items()
        if res.get("Type") == "AWS::Serverless::Function"
    }


def test_the_function_scan_is_not_empty():
    """Guard the guard: an empty scan would make the assertion below vacuous."""
    fns = _functions()
    assert len(fns) > 20, f"only found {len(fns)} functions; the parse is probably broken"
    assert "JobHuntApi" in fns


def test_no_function_environment_references_the_http_api():
    """The exact cycle, pinned out of the template.

    Generalised past JobHuntApi on purpose: any function that both serves the
    API and names it in its own Environment creates the same cycle, and the
    next one will not be called MCP_ALLOWED_HOSTS.
    """
    offenders = {}
    for name, res in _functions().items():
        env = ((res.get("Properties") or {}).get("Environment") or {}).get("Variables") or {}
        for key, value in env.items():
            if any("HttpApi" in s for s in _strings(value)):
                offenders[f"{name}.{key}"] = value
    assert not offenders, (
        f"{sorted(offenders)} reference the HttpApi resource from a function's "
        "Environment. SAM's API body already references these functions' ARNs, so "
        "this is a circular dependency and CloudFormation refuses the changeset. "
        "Pass the value as a stack Parameter instead — see McpAllowedHosts."
    )


def test_the_mcp_host_is_a_parameter_with_a_closed_default():
    params = _template()["Parameters"]
    assert "McpAllowedHosts" in params, "the host must come from a parameter, not a Ref"
    spec = params["McpAllowedHosts"]
    assert spec.get("Default") == "", (
        "an empty default is what makes an unsupplied value fail CLOSED to "
        "localhost-only; a non-empty default would widen the transport silently"
    )


def test_the_deploy_workflow_supplies_the_parameter():
    """A parameter nothing fills is a dead flag that also breaks MCP in prod."""
    run = next(
        s["run"] for s in yaml.safe_load(DEPLOY.read_text())["jobs"]["deploy"]["steps"]
        if s.get("name") == "SAM Deploy"
    )
    assert re.search(r"^\s*optional\s+McpAllowedHosts\s", run, re.M), (
        "deploy.yml does not pass McpAllowedHosts, so the deployed transport "
        "answers for localhost only and every real client gets 421"
    )
    assert "web/.env.production" in run, (
        "the host should come from the same committed file the smoke step reads, "
        "not a second hand-maintained copy"
    )


def test_ci_runs_the_lint_that_catches_cycles():
    """Bare `sam validate` passes a template CloudFormation will refuse."""
    text = READINESS.read_text()
    validates = re.findall(r"^\s*run:\s*(sam validate[^\n]*)$", text, re.M)
    assert validates, "Deploy Readiness runs no sam validate at all"
    assert all("--lint" in v for v in validates), (
        f"{[v for v in validates if '--lint' not in v]} run without --lint, which "
        "is the flag that detects circular dependencies (cfn-lint E3004)"
    )


def test_the_cycle_rule_is_not_silenced():
    """The gate is only a gate while E3004 is live.

    A future circular dependency is easiest to "fix" by adding E3004 here. That
    would restore exactly the blind spot that cost six deploys, so it fails
    instead.
    """
    assert CFNLINTRC.exists(), ".cfnlintrc.yaml is missing; sam validate --lint would fail on E2533"
    cfg = yaml.safe_load(CFNLINTRC.read_text()) or {}
    ignored = set(cfg.get("ignore_checks") or [])
    assert "E3004" not in ignored, (
        "E3004 is the circular-dependency rule and the only reason --lint was "
        "worth switching on. Fix the cycle instead of ignoring the rule."
    )
    assert ignored == {"E2533"}, (
        f"unexpected ignored rules {sorted(ignored - {'E2533'})}. E2533 (python3.11 "
        "deprecation) is ignored deliberately and documented in the file; anything "
        "else needs the same justification written down."
    )
