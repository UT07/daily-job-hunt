"""A CodeDeploy traffic shift must be able to roll back, or it must not exist.

On 2026-10-09 two consecutive production deploys failed and rolled back:

  1. "the number of requests by the IAM role ...CodeDeployServiceRole...
     exceeded the request limit for AWSLambda"
  2. "The Lambda function alias version does not match the current version in
     AppSpec file" -- two deployments for SaveMetrics' alias created one second
     apart, one Succeeded, one Failed.

Both are the same cause: 29 `live` aliases each deployed through CodeDeploy at
once. Since #213 put shared/ into every function's CodeUri, ANY shared/ change
updates every function, so every such deploy is that burst.

25 of the 29 bought nothing for it. `AllAtOnce` through CodeDeploy is a plain
alias update with extra API calls; `Linear10PercentEvery1Minute` with no
`Alarms` shifts traffic over ten minutes with nothing watching, so nothing can
ever roll it back -- a deployment status that cannot fail (CLAUDE.md #2). The
four canaries WITH alarms are real protection and stay.

The rule, enforced: every DeploymentPreference must carry Alarms.
"""
from pathlib import Path

import yaml

TEMPLATE = Path(__file__).resolve().parents[2] / "template.yaml"


class _CfnLoader(yaml.SafeLoader):
    pass


def _any(loader, _suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_CfnLoader.add_multi_constructor("!", _any)


def _functions():
    tpl = yaml.load(TEMPLATE.read_text(), Loader=_CfnLoader)
    return {name: res.get("Properties", {})
            for name, res in tpl["Resources"].items()
            if res.get("Type") == "AWS::Serverless::Function"}


def test_the_template_has_functions_to_judge():
    # Guard against the check below passing over an empty population.
    assert len(_functions()) > 20


def test_every_deployment_preference_can_roll_back():
    unguarded = {name: p["DeploymentPreference"].get("Type")
                 for name, p in _functions().items()
                 if p.get("DeploymentPreference")
                 and not p["DeploymentPreference"].get("Alarms")}
    assert not unguarded, (
        "These functions route their alias through CodeDeploy with no Alarms, "
        "so the deployment can never roll back -- it only adds CodeDeploy API "
        f"load (2026-10-09: two deploys failed on it): {unguarded}")


def test_the_alarmed_canaries_are_kept():
    # The protection that IS real must survive this cleanup.
    canaries = {name for name, p in _functions().items()
                if (p.get("DeploymentPreference") or {}).get("Alarms")}
    assert {"TailorResumeFunction", "CompileLatexFunction",
            "GenerateCoverLetterFunction", "SaveJobFunction"} <= canaries
