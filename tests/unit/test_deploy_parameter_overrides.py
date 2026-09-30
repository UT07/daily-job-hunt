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


# ── Dead parameters ───────────────────────────────────────────────────────
#
# The tests above keep deploy.yml and template.yaml in agreement about which
# parameters exist. They say nothing about whether a parameter DOES anything,
# and on 2026-09-30 two of the twenty-one did not:
#
#   GoogleCredentialsJson  declared 5e21293 (the commit that added the SAM
#     template), wired into JobHuntApi's environment, and supplied by deploy.yml
#     as the inline literal "GoogleCredentialsJson=PLACEHOLDER_SET_MANUALLY".
#     abc0fe9 — the SAME DAY — reverted the Google Docs approach the parameter
#     was for, and removed everything except the parameter. Six months of
#     deploys carried it. The live Lambda held
#     GOOGLE_CREDENTIALS_JSON=PLACEHOLDER_SET_MANUALLY, verified by
#     `aws lambda get-function-configuration`.
#
#   CapSolverApiKey  declared for the Smart Apply cloud-browser work, plumbed
#     through a GitHub secret and an optional() line, and referenced NOWHERE in
#     template.yaml — not one !Ref. Smart Apply was torn down in August;
#     BrowserSubnetIds went with it and this one stayed.
#
# Same defect class as the `fairness_cap` key removed from
# guardrails/policy.py, and worse than an absent key in both cases. A NoEcho
# parameter that only ever carries a committed placeholder makes the stack
# report a secret it does not have, and google_docs_client._get_credentials
# takes its env-var branch on the variable being non-empty rather than on its
# being credentials — so the placeholder turned an honest FileNotFoundError
# naming google_credentials.json into `JSONDecodeError: Expecting value: line 1
# column 1`, which reads as corrupt credentials rather than absent ones.
#
# These two tests are what makes the next revert-leftover fail CI. They are
# deliberately NOT "the env var this parameter feeds is read by some Python
# module": GOOGLE_CREDENTIALS_JSON *is* read, by google_docs_client.py, which
# `COPY *.py` ships into the API container and which app.py imports
# transitively via cover_letter.py. That formulation passes under the bug,
# which per CLAUDE.md rule 6 makes it worse than no test.

PARAMETERS_BLOCK = re.compile(r"^Parameters:\n.*?(?=^\w)", re.M | re.S)


def _template_body() -> str:
    """template.yaml with the Parameters block removed.

    A parameter's own declaration is not a use of it, so it has to come out
    before asking whether anything references the name — otherwise every
    parameter trivially "references itself" and the test below is vacuous.
    """
    text = TEMPLATE.read_text()
    body, n = PARAMETERS_BLOCK.subn("", text)
    assert n == 1, "could not locate the top-level Parameters block in template.yaml"
    return body


def _inline_literals() -> set[str]:
    """Parameter names given a hardcoded value inside --parameter-overrides."""
    run = _deploy_step()
    overrides = run.split("--parameter-overrides", 1)
    assert len(overrides) == 2, "could not find --parameter-overrides in the deploy step"
    return set(re.findall(r'"(\w+)=[^$"]+"', overrides[1]))


def test_the_dead_parameter_scaffolding_is_not_vacuous():
    """Guard the guard (CLAUDE.md rule 6).

    Both tests below are "assert not <set>". A broken regex empties the set and
    they pass while checking nothing — which is how the thing they check for
    survived six months in the first place.
    """
    params = _template_parameters()
    assert len(params) >= 15, f"only found {len(params)} parameters; the loader is broken"

    body = _template_body()
    assert "Resources:" in body, "Parameters-block excision ate the rest of the template"
    assert "SupabaseUrl" in body, "a known-referenced parameter vanished from the body"
    assert "  SupabaseUrl:\n" not in body, "the Parameters block was not actually removed"

    literals = _inline_literals()
    assert len(literals) >= 3, (
        f"expected several inline literal overrides, found {sorted(literals)}; "
        "the regex no longer matches the deploy step"
    )
    assert literals <= set(params), (
        f"{sorted(literals - set(params))} are passed as overrides but template.yaml "
        "declares no such parameter — sam deploy rejects unknown parameters."
    )


def test_every_declared_parameter_is_referenced_by_the_template():
    """A parameter nothing !Refs is configuration that configures nothing.

    CapSolverApiKey was exactly this: declared, secret-backed, passed on every
    deploy, and read by no resource in the stack.
    """
    body = _template_body()
    params = _template_parameters()
    unreferenced = sorted(
        name for name in params
        if not re.search(r"\b" + re.escape(name) + r"\b", body)
    )
    assert not unreferenced, (
        f"{unreferenced} are declared in template.yaml's Parameters and referenced by "
        "nothing in the template. Either wire them into a resource or delete them "
        "(and their deploy.yml require()/optional() line and step env entry)."
    )


def test_no_noecho_parameter_is_supplied_as_a_hardcoded_literal():
    """A secret whose only value is committed to the repo is not a secret.

    `NoEcho: true` is a claim that the value is sensitive. An inline
    "Name=literal" in --parameter-overrides is a claim that it is a constant
    checked into a public repo. Both cannot be true, so one of them is a lie:
    either the parameter does not need NoEcho, or — as with
    GoogleCredentialsJson=PLACEHOLDER_SET_MANUALLY — the value is a placeholder
    and the parameter is dead. Route a real secret through require()/optional().
    """
    params = _template_parameters()
    offenders = sorted(
        name for name in _inline_literals()
        if str(params.get(name, {}).get("NoEcho", "")).lower() == "true"
    )
    assert not offenders, (
        f"{offenders} are declared NoEcho in template.yaml but supplied as hardcoded "
        "literals in deploy.yml's --parameter-overrides. A committed value is not a "
        "secret; if it is a placeholder the parameter is dead and should be removed."
    )


def test_the_announced_literal_count_matches_the_literals_actually_passed():
    """The deploy step prints how many literals it passes. It must not drift.

    CLAUDE.md rule 2: a status that cannot distinguish did-the-work from
    did-nothing is a lie, and a hardcoded count in a log line goes stale the
    moment a literal is added or removed — silently, because nothing reads it
    back. Removing GoogleCredentialsJson made "plus 5 literals" wrong.
    """
    run = _deploy_step()
    announced = re.search(r"plus (\d+) literals", run)
    assert announced, "the SAM Deploy step no longer announces a literal count"
    assert int(announced.group(1)) == len(_inline_literals()), (
        f"the deploy step announces {announced.group(1)} inline literals but passes "
        f"{len(_inline_literals())}: {sorted(_inline_literals())}."
    )
