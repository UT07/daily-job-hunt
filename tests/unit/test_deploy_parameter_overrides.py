"""deploy.yml's --parameter-overrides must agree with template.yaml.

Every deploy from c15723b through 55ebff2 failed, and `main` moved four
commits ahead of production while CI stayed green. The cause was one
character of shell:

    "CerebrasApiKey=${CEREBRAS_KEY}"

CEREBRAS_API_KEY was never added as a GitHub secret after the provider
landed, so the variable expanded to "" and the argument became
"CerebrasApiKey=". The SAM CLI rejects that as a parse error on the WHOLE
flag -- not a per-parameter warning -- so a single missing OPTIONAL secret
failed the entire deploy before CloudFormation was called:

    Error: Invalid value for '--parameter-overrides':
    CerebrasApiKey= is not a valid format

All thirteen secret-backed parameters were interpolated that way, so any one
unset secret had the same effect. The workflow now filters empties, and
whether that is safe per-parameter is not a judgement the workflow makes:
template.yaml already encodes it. `Default: ''` means the stack can take the
default and the provider is simply absent. No Default means the parameter
must be supplied.

These tests keep the two files in agreement, because the failure mode is
silent -- adding a parameter to one side only shows up as a red deploy after
a merge, which is the worst possible time to find out.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
DEPLOY = ROOT / ".github" / "workflows" / "deploy.yml"
TEMPLATE = ROOT / "template.yaml"


def _deploy_step() -> str:
    doc = yaml.safe_load(DEPLOY.read_text())
    for step in doc["jobs"]["deploy"]["steps"]:
        if step.get("name") == "SAM Deploy":
            return step["run"]
    raise AssertionError("no 'SAM Deploy' step in deploy.yml")


def _classified() -> tuple[set[str], set[str]]:
    """(required, optional) parameter names as the workflow classifies them."""
    run = _deploy_step()
    required = set(re.findall(r"^\s*require\s+(\w+)\s", run, re.M))
    optional = set(re.findall(r"^\s*optional\s+(\w+)\s", run, re.M))
    return required, optional


def _template_parameters() -> dict:
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
    return yaml.load(TEMPLATE.read_text(), Loader=Loader).get("Parameters", {})


def test_the_classification_is_not_empty():
    """Guard the guard -- a regex that matches nothing makes everything below vacuous."""
    required, optional = _classified()
    assert len(required) >= 5, required
    assert len(optional) >= 5, optional
    assert not (required & optional), f"classified both ways: {required & optional}"


def test_every_optional_parameter_has_a_template_default():
    """Omitting a parameter is only safe if the template can fall back."""
    _, optional = _classified()
    params = _template_parameters()
    bad = [
        name for name in sorted(optional)
        if name not in params or "Default" not in params[name]
    ]
    assert not bad, (
        f"{bad} are omitted when their secret is unset, but template.yaml gives them "
        "no Default — the stack would deploy with no value rather than a fallback. "
        "Either add a Default in template.yaml or move them to require()."
    )


def test_every_required_parameter_lacks_a_template_default():
    """The converse: a parameter with a Default should not fail the deploy.

    Not cosmetic. A required-but-defaulted parameter blocks every deploy until
    an operator adds a secret that the template says is unnecessary, which is
    the same class of self-inflicted outage as the original bug, just inverted.
    """
    required, _ = _classified()
    params = _template_parameters()
    bad = [
        name for name in sorted(required)
        if name in params and "Default" in params[name]
    ]
    assert not bad, (
        f"{bad} fail the deploy when unset, but template.yaml declares a Default for "
        "them, so the stack does not need them. Move them to optional()."
    )


def test_no_secret_is_interpolated_inline_into_the_overrides():
    """The exact pattern that broke production, pinned out of the file.

    An inline "Name=${VAR}" inside --parameter-overrides is unconditional: an
    unset secret becomes "Name=" and SAM rejects the whole flag. The literals
    that remain inline are constants with no variable in them, which cannot be
    empty.
    """
    run = _deploy_step()
    overrides = run.split("--parameter-overrides", 1)
    assert len(overrides) == 2, "could not find --parameter-overrides in the deploy step"
    offenders = re.findall(r'"(\w+)=\$\{[^}]+\}"', overrides[1])
    assert not offenders, (
        f"{offenders} are interpolated inline into --parameter-overrides. An unset "
        "secret expands to empty and SAM fails the entire flag with 'is not a valid "
        "format'. Route them through require()/optional() instead."
    )


def test_every_declared_secret_is_actually_consumed():
    """A secret wired into the step's env and read by nothing is a dead flag.

    Same defect class as the `fairness_cap` policy key removed from
    guardrails/policy.py: it looks like configuration and changes nothing.
    """
    doc = yaml.safe_load(DEPLOY.read_text())
    step = next(s for s in doc["jobs"]["deploy"]["steps"] if s.get("name") == "SAM Deploy")
    declared = set(step.get("env") or {})
    run = step["run"]
    unused = sorted(v for v in declared if f"${{{v}:-}}" not in run and f"${{{v}}}" not in run)
    assert not unused, (
        f"{unused} are declared in the SAM Deploy env but never read by the run block, "
        "so the secret is plumbed in and silently ignored."
    )


def test_every_parameter_the_template_requires_is_supplied():
    """A no-Default parameter absent from BOTH sides of the deploy step fails at
    CloudFormation with a less legible error than require() gives."""
    required, optional = _classified()
    run = _deploy_step()
    inline_literals = set(re.findall(r'"(\w+)=[^$"]+"', run))
    supplied = required | optional | inline_literals
    params = _template_parameters()
    missing = [
        name for name, spec in sorted(params.items())
        if "Default" not in spec and name not in supplied
    ]
    assert not missing, (
        f"template.yaml requires {missing} with no Default, and the deploy step never "
        "supplies them. CloudFormation will reject the stack."
    )
