"""A function that reads its API keys from SSM must be allowed to read SSM.

Found 2026-10-09 in production, after Save & Score went async: the task ran
229s and ended "All 15 AI providers failed". Every Gemini model failed with

    AccessDeniedException when calling the GetParameter operation:
    User: ...assumed-role/job-hunt-api-...

ai_helper fetches `/naukribaba/GEMINI_API_KEY` through `get_param` (SSM). The
pipeline Lambdas carry `SSMParameterReadPolicy: naukribaba/*`; JobHuntApi --
which imports the same ai_helper through app.py -- never did. Gemini, the
provider the daily pipeline actually scores with, was therefore dead inside the
API on every call; interactive scoring only ever worked while Groq's free
daily quota lasted, and failed outright once it was spent.

The rule, by population (CLAUDE.md #7): every function whose code can reach
`get_param` -- its handler module imports ai_helper or calls get_param, or it is
the API image whose app.py does -- must hold the SSM read policy.
"""
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "template.yaml"


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
    return {n: r["Properties"] for n, r in tpl["Resources"].items()
            if r.get("Type") == "AWS::Serverless::Function"}


def _resolve(module: str, search_roots) -> Path | None:
    rel = Path(*module.split("."))
    for root in search_roots:
        for cand in (root / f"{rel}.py", root / rel / "__init__.py"):
            if cand.exists():
                return cand
    return None


def _reachable_sources(entry: Path, search_roots):
    """Every repo module the entry point imports, transitively.

    TRANSITIVE on purpose: app.py never names ai_helper -- it reaches it through
    `lambdas.pipeline.score_batch`. A literal-string check missed exactly the
    function this test exists for, and the population assertion below caught it.
    """
    import ast
    seen, stack = set(), [entry]
    while stack:
        path = stack.pop()
        if path in seen:
            continue
        seen.add(path)
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [a.name for a in node.names]
            elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                names = [node.module] + [f"{node.module}.{a.name}" for a in node.names]
            for n in names:
                hit = _resolve(n, search_roots)
                if hit and hit not in seen:
                    stack.append(hit)
    return seen


def _reads_keys_from_ssm(name, props) -> bool:
    if props.get("PackageType") == "Image":
        # The API container: app.py at the repo root is its entry point.
        entry, roots = ROOT / "app.py", [ROOT, ROOT / "lambdas" / "pipeline"]
    else:
        module = props["Handler"].split(".")[0]
        code = ROOT / props["CodeUri"]
        entry, roots = code / f"{module}.py", [code, ROOT]
        assert entry.exists(), f"{name}: handler module {entry} not found"
    return any("def get_param" in p.read_text() or "get_param(" in p.read_text()
               for p in _reachable_sources(entry, roots))


def _has_ssm_read(props) -> bool:
    for p in props.get("Policies") or []:
        if isinstance(p, dict) and "SSMParameterReadPolicy" in p:
            if str(p["SSMParameterReadPolicy"].get("ParameterName", "")).startswith("naukribaba/"):
                return True
    return False


def test_the_population_is_real():
    readers = [n for n, p in _functions().items() if _reads_keys_from_ssm(n, p)]
    # The API image and the pipeline handlers that call ai_helper.
    assert "JobHuntApi" in readers
    assert len(readers) > 10


def test_every_function_that_reads_keys_from_ssm_may_read_ssm():
    missing = sorted(n for n, p in _functions().items()
                     if _reads_keys_from_ssm(n, p) and not _has_ssm_read(p))
    assert not missing, (
        "These functions read API keys via ai_helper.get_param (SSM) but have no "
        f"SSMParameterReadPolicy for naukribaba/*: {missing}. Every key they "
        "fetch fails with AccessDeniedException, silently removing that provider.")
