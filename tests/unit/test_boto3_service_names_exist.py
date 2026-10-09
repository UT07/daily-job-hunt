"""Every boto3 client name in the repo must be a service boto3 actually has.

Found 2026-10-09 in production logs, on every Dashboard load:

    Failed to fetch Step Functions status: Unknown service: 'states'

`/api/pipeline/status` fell back to Step Functions with
`boto3.client("states")`. The service is called `stepfunctions` -- `states` is
only the ARN/IAM prefix -- so the fallback raised before any call, a broad
`except` logged it, and the Dashboard silently showed no latest run. No test
could see it because every test mocks boto3 (CLAUDE.md #5).

The name is checkable without AWS: botocore ships the list.
"""
import ast
import subprocess
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parents[2]
VALID = set(boto3.session.Session().get_available_services())


def _client_names():
    files = subprocess.run(["git", "ls-files", "*.py"], cwd=ROOT, check=True,
                           capture_output=True, text=True).stdout.split()
    found = []
    for rel in files:
        try:
            tree = ast.parse((ROOT / rel).read_text())
        except (SyntaxError, UnicodeDecodeError):
            continue
        for node in ast.walk(tree):
            if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "client" and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                base = node.func.value
                if isinstance(base, ast.Name) and base.id in ("boto3", "session", "_session"):
                    found.append((rel, node.lineno, node.args[0].value))
    return found


def test_the_scan_sees_real_clients():
    # Guard against passing over an empty population (CLAUDE.md #7).
    names = {n for _, _, n in _client_names()}
    assert {"s3", "stepfunctions"} <= names, names


def test_every_boto3_client_name_is_a_real_service():
    bad = [f"{rel}:{line} boto3.client({name!r})"
           for rel, line, name in _client_names() if name not in VALID]
    assert not bad, ("boto3 has no such service -- the call raises "
                     f"UnknownServiceError before reaching AWS: {bad}")


# --- and the API role may make the calls it makes -------------------------
#
# Fixing the name alone would have traded UnknownServiceError for AccessDenied:
# the role granted Describe/Stop/GetExecutionHistory but never ListExecutions,
# which the same fallback calls first. Both were swallowed by one broad except
# (CLAUDE.md #17). Every Step Functions method app.py calls must map to an
# action JobHuntApi's policy grants.

import re  # noqa: E402

import yaml  # noqa: E402


class _CfnLoader(yaml.SafeLoader):
    pass


def _any(loader, _suffix, node):
    if isinstance(node, yaml.ScalarNode):
        return loader.construct_scalar(node)
    if isinstance(node, yaml.SequenceNode):
        return loader.construct_sequence(node)
    return loader.construct_mapping(node)


_CfnLoader.add_multi_constructor("!", _any)

_SFN_METHODS = {"start_execution", "list_executions", "describe_execution",
                "stop_execution", "get_execution_history"}


def _api_states_actions():
    tpl = yaml.load((ROOT / "template.yaml").read_text(), Loader=_CfnLoader)
    granted = set()
    for pol in tpl["Resources"]["JobHuntApi"]["Properties"].get("Policies") or []:
        for st in (pol.get("Statement") or []) if isinstance(pol, dict) else []:
            acts = st.get("Action", [])
            granted |= {a for a in ([acts] if isinstance(acts, str) else acts)
                        if a.startswith("states:")}
    return granted


def test_the_api_role_grants_every_step_functions_call_app_makes():
    src = (ROOT / "app.py").read_text()
    called = {m for m in _SFN_METHODS if re.search(rf"\.{m}\(", src)}
    assert {"start_execution", "describe_execution"} <= called  # population is real
    needed = {"states:" + "".join(w.title() for w in m.split("_")) for m in called}
    missing = sorted(needed - _api_states_actions())
    assert not missing, f"app.py calls Step Functions actions JobHuntApi may not: {missing}"
