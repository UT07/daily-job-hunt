"""Every application package must reach BOTH deploy paths.

This repo has shipped a package present in the layer but absent from the
Docker image; lazy imports hid it until runtime. Assert it instead.
"""
import ast
import pathlib
import re

import pytest
import yaml

REPO = pathlib.Path(__file__).resolve().parents[2]

# "agents" is deliberately NOT in this list. It lives at
# lambdas/pipeline/agents/, inside the pipeline functions' own CodeUri, not
# as a standalone package needing matching Dockerfile COPY + layer
# FIRST_PARTY entries the way "shared" does. See
# test_agents_not_in_layer_build_first_party_list and
# test_agents_package_lives_under_lambdas_pipeline below.
APP_PACKAGES = ["shared"]


@pytest.mark.parametrize("pkg", APP_PACKAGES)
def test_package_exists(pkg):
    assert (REPO / pkg / "__init__.py").is_file(), f"{pkg} is not a package"


@pytest.mark.parametrize("pkg", APP_PACKAGES)
def test_package_in_dockerfile(pkg):
    dockerfile = (REPO / "Dockerfile.lambda").read_text()
    assert f"COPY {pkg}/" in dockerfile, (
        f"{pkg}/ missing from Dockerfile.lambda — it will 404 at runtime "
        f"in the container Lambda"
    )


def _first_party_packages_in_layer_build_sh():
    """Parse the FIRST_PARTY="pkg1 pkg2" list out of layer/build.sh.

    This is the zip-layer analogue of the `COPY {pkg}/` check above: the
    layer mounts at /opt/python for every zip-based pipeline Lambda, so a
    package missing here ModuleNotFoundErrors at runtime even though it is
    correctly COPYed into the container-image Lambda.
    """
    build_sh = (REPO / "layer" / "build.sh").read_text()
    match = re.search(r'FIRST_PARTY="([^"]*)"', build_sh)
    assert match, (
        "layer/build.sh must define FIRST_PARTY=\"...\" listing the "
        "first-party packages copied into the shared layer"
    )
    return match.group(1).split()


@pytest.mark.parametrize("pkg", APP_PACKAGES)
def test_package_in_layer_build_script(pkg):
    packages = _first_party_packages_in_layer_build_sh()
    assert pkg in packages, (
        f"{pkg}/ missing from layer/build.sh FIRST_PARTY list — it will "
        f"ModuleNotFoundError at runtime for zip-based pipeline Lambdas, "
        f"which get first-party packages from /opt/python (this layer), "
        f"not from a Docker COPY"
    )


# Dependencies imported by Lambda-runtime code (agents/, shared/, and the
# pipeline lambdas) that MUST be installed into the shared layer, not just
# into the root requirements.txt used for local dev / CI. This list is the
# contract: grow it whenever Lambda-runtime code gains a new import, or the
# layer will build fine and ModuleNotFoundError in prod anyway — exactly
# the 2026-09-22 COUNCIL_ENGINE=langgraph incident this file is named for.
LAMBDA_RUNTIME_DEPS = ["langgraph", "langchain-core"]


def _requirement_names(path):
    """Best-effort package-name extraction from a requirements.txt file."""
    names = set()
    for line in path.read_text().splitlines():
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        name = re.split(r"[\[<>=!~; ]", line, maxsplit=1)[0].strip()
        if name:
            names.add(name.lower().replace("_", "-"))
    return names


@pytest.mark.parametrize("dep", LAMBDA_RUNTIME_DEPS)
def test_lambda_runtime_dep_in_layer_requirements(dep):
    root_reqs = _requirement_names(REPO / "requirements.txt")
    assert dep.lower() in root_reqs, (
        f"{dep} is listed in LAMBDA_RUNTIME_DEPS but not in root "
        f"requirements.txt — fix the test's LAMBDA_RUNTIME_DEPS list or "
        f"add the dependency to requirements.txt"
    )

    layer_reqs = _requirement_names(REPO / "layer" / "requirements.txt")
    assert dep.lower() in layer_reqs, (
        f"{dep} is a Lambda-runtime dependency (present in root "
        f"requirements.txt) but missing from layer/requirements.txt. "
        f"The shared layer installs from THAT SEPARATE file — a dep added "
        f"only to the root file works locally but ModuleNotFoundErrors in "
        f"every zip-based pipeline Lambda in production."
    )


def _load_template_tolerant_of_cfn_tags():
    """Load template.yaml, treating unrecognized CloudFormation short-form
    tags (!Sub, !Ref, !GetAtt, !If, !Join, ...) as plain scalars/sequences/
    mappings instead of erroring.

    Plain `yaml.safe_load` raises `ConstructorError: could not determine a
    constructor for the tag '!Sub'` on this template — SAM/CFN intrinsic
    tags aren't standard YAML and PyYAML doesn't know them, and this repo
    has no cfn-flip/cfn-tools dependency to resolve them properly. This test
    doesn't need the *resolved* value of a `!Sub` or `!GetAtt` (e.g. an
    ARN) — only the surrounding structure (which top-level keys and
    function properties exist) — so discarding the tag and keeping the
    underlying node is sufficient and needs no new dependency.
    """

    class _CfnTagTolerantLoader(yaml.SafeLoader):
        pass

    def _construct_underlying_node(loader, _tag_suffix, node):
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        if isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node)
        return None

    _CfnTagTolerantLoader.add_multi_constructor("!", _construct_underlying_node)

    with (REPO / "template.yaml").open() as f:
        return yaml.load(f, Loader=_CfnTagTolerantLoader)


def test_alias_all_properties_set_whenever_any_function_auto_publishes():
    """A layer-only change must still publish a new Lambda version and move
    the `live` alias — otherwise Step Functions keeps invoking the OLD
    version, which stays frozen to the OLD layer, and every call
    ModuleNotFoundErrors despite a green deploy.

    This is exactly the 2026-09-23 incident this file is named for: layer
    v65 was built correctly with agents/ + langgraph, and the function's
    $LATEST configuration referenced v65, but the `live` alias for
    naukribaba-tailor-resume stayed on version 10 — frozen to the OLD layer
    v64 — because SAM's `AutoPublishAlias` only publishes a new version
    when the function's CodeUri hash changes, and a layer-only bump never
    touches that hash. `AutoPublishAliasAllProperties: true` makes SAM
    consider ALL properties (including the layer list) when deciding
    whether to publish, so a layer-only change moves the alias too.
    """
    template = _load_template_tolerant_of_cfn_tags()

    function_resources = [
        resource
        for resource in template["Resources"].values()
        if isinstance(resource, dict)
        and resource.get("Type") == "AWS::Serverless::Function"
    ]
    functions_with_alias = [
        resource
        for resource in function_resources
        if "AutoPublishAlias" in resource.get("Properties", {})
    ]
    if not functions_with_alias:
        pytest.skip("no function in template.yaml uses AutoPublishAlias")

    globals_function = template.get("Globals", {}).get("Function", {})
    assert globals_function.get("AutoPublishAliasAllProperties") is True, (
        f"{len(functions_with_alias)} function(s) in template.yaml use "
        "AutoPublishAlias, but Globals.Function has no "
        "`AutoPublishAliasAllProperties: true`. Without it, a layer-only "
        "change (a package added to the shared layer, a dependency bump, "
        "...) publishes NO new Lambda version, so the `live` alias that "
        "Step Functions invokes silently keeps running the OLD version "
        "against the OLD layer — deploy reports UPDATE_COMPLETE while "
        "production keeps ModuleNotFoundError'ing."
    )


# ---------------------------------------------------------------------------
# 2026-09-23 COUNCIL_ENGINE=langgraph incident, part 2: a SECOND import
# convention introduced inside agents/ itself, on top of the layer/Dockerfile
# parity this file already guards above.
# ---------------------------------------------------------------------------
# The checks above catch a *package* missing from a deploy path. This
# incident's actual failure was one level down: `agents/` itself was
# deployed correctly everywhere (layer v65 genuinely contained it), but two
# of its own modules -- agents/providers.py and agents/nodes.py -- imported
# it as `from lambdas.pipeline.ai_helper import ...`. That spelling only
# resolves where a `lambdas` package is on the path: at the repo root
# (pytest, via tests/conftest.py) and in the container-image Lambda
# (Dockerfile.lambda ships the whole lambdas/ tree). It CANNOT resolve in a
# zip-based pipeline Lambda, because template.yaml gives those functions
# `CodeUri: lambdas/pipeline/`, which SAM/CFN flattens into /var/task --
# there is no `lambdas` package there, only a flat `ai_helper.py`. Every
# test passed; every zip-based pipeline Lambda ModuleNotFoundError'd.

# agents/ moved to lambdas/pipeline/agents/ on 2026-09-23 (see "part 3"
# below) specifically so it packages inside the pipeline functions' own
# CodeUri instead of the shared layer. It is no longer a repo-root package.
#
# 2026-09-24 generalization: this check originally only scanned agents/*.py,
# exempting agents/_ai_helper.py by filename because that one file's whole
# job is the sanctioned `try: import ai_helper / except ImportError: from
# lambdas.pipeline import ai_helper` fallback. Task 12 (retrieval/embeddings.py)
# and Task 13 (retrieval/store.py) each then inlined that SAME guarded
# fallback directly into their own module instead of a dedicated shim file
# -- there is no whole file to exempt by name for either of them, and a
# per-file exemption list doesn't scale to "every module under
# lambdas/pipeline/" anyway. Fixed at the root instead: walk every .py file
# anywhere under lambdas/pipeline/ (this CodeUri's real root), and judge
# each `lambdas...` import by where it sits in the AST, not by which file
# it happens to live in. An import is sanctioned exactly when it is
# lexically inside a `try`/`except ImportError` construct -- the only shape
# that resolves in the flattened zip Lambda (the flat import succeeds
# there) while also covering the container-image shape (the flat import
# fails over to the qualified one there instead). A bare, unguarded
# `lambdas...` import anywhere else is flagged, regardless of file or
# directory.
PIPELINE_DIR = REPO / "lambdas" / "pipeline"
_PIPELINE_MODULES = sorted(PIPELINE_DIR.rglob("*.py"))


def _handler_catches_import_error(handler: ast.ExceptHandler) -> bool:
    """True if this `except` clause's type is (or includes) ImportError."""
    exc_type = handler.type
    if isinstance(exc_type, ast.Name):
        return exc_type.id == "ImportError"
    if isinstance(exc_type, (ast.Tuple, ast.List)):
        return any(isinstance(e, ast.Name) and e.id == "ImportError" for e in exc_type.elts)
    return False


def _unguarded_lambdas_imports(tree: ast.AST) -> list[str]:
    """Return one description per `lambdas...` import that is NOT inside a
    try/except ImportError construct.

    Recurses by hand instead of using `ast.walk` (which flattens the tree
    and loses nesting) so it can track, node by node, whether the current
    statement sits inside the `try:` body or a matching handler's body of a
    `try` / `except ImportError:` -- the shape every sanctioned fallback in
    this codebase uses today (agents/_ai_helper.py, retrieval/embeddings.py,
    retrieval/store.py, ai_helper.py's council_complete). Both branches of
    that construct are treated as guarded, regardless of which one actually
    holds the `lambdas...` spelling, since the construct as a whole is the
    deliberate flat-first / qualified-fallback resolution dance. `orelse`
    and `finalbody` are deliberately NOT guarded by this try, since they run
    unconditionally rather than only after a failed flat import.

    A docstring or comment that merely *mentions* "lambdas.pipeline" is
    never an ast.Import/ast.ImportFrom node in the first place, so it can
    never trip this -- only a real import statement can.
    """
    violations: list[str] = []

    def visit(node: ast.AST, guarded: bool) -> None:
        if isinstance(node, ast.Try) and any(_handler_catches_import_error(h) for h in node.handlers):
            for child in node.body:
                visit(child, True)
            for handler in node.handlers:
                for child in handler.body:
                    visit(child, True)
            for child in node.orelse:
                visit(child, guarded)
            for child in node.finalbody:
                visit(child, guarded)
            return
        if isinstance(node, ast.Import):
            for alias in node.names:
                if not guarded and (alias.name == "lambdas" or alias.name.startswith("lambdas.")):
                    violations.append(f"line {node.lineno}: `import {alias.name}`")
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            if not guarded and (module == "lambdas" or module.startswith("lambdas.")):
                violations.append(f"line {node.lineno}: `from {module} import ...`")
        for child in ast.iter_child_nodes(node):
            visit(child, guarded)

    visit(tree, False)
    return violations


@pytest.mark.parametrize("path", _PIPELINE_MODULES, ids=lambda p: str(p.relative_to(PIPELINE_DIR)))
def test_no_unguarded_lambdas_imports_under_pipeline(path):
    """No module anywhere under lambdas/pipeline/ may contain a bare
    `import lambdas...` / `from lambdas... import ...` -- that import cannot
    resolve once CodeUri flattens lambdas/pipeline/ into a zip Lambda's
    /var/task, which is exactly what production hit on 2026-09-23. The one
    sanctioned exception is a `lambdas...` import inside a
    `try: <flat import> / except ImportError: <lambdas... fallback>`
    construct -- see the comment block above and `_unguarded_lambdas_imports`
    for why that shape alone resolves in every deploy path this repo has.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    violations = _unguarded_lambdas_imports(tree)
    assert not violations, (
        f"{path.relative_to(REPO)}: unguarded lambdas-package import(s) "
        f"that cannot resolve in the deployed (flattened) Lambda: "
        f"{violations}. Wrap the fallback in try/except ImportError (see "
        f"agents/_ai_helper.py or retrieval/embeddings.py), or route "
        f"through agents.providers instead."
    )


# ---------------------------------------------------------------------------
# 2026-09-23 COUNCIL_ENGINE=langgraph incident, part 3: agents/ itself was
# the wrong kind of package to put in the shared layer.
#
# AutoPublishAliasAllProperties (tested above) makes a layer-only CHANGE
# move the `live` alias -- but the layer is referenced from the function's
# template properties only as the plain logical ID `Ref: SharedDepsLayer`.
# SAM's transform expands that to the real, content-hashed logical ID
# (e.g. `SharedDepsLayerb7375ba5b5`) at deploy time, AFTER it has already
# decided whether the function's properties changed enough to publish a new
# version. A layer-CONTENT-only change (editing agents/ without touching
# template.yaml) therefore leaves the function's template properties
# textually identical, publishes no new version, and the `live` alias stays
# frozen on the OLD version bound to the OLD layer -- exactly what
# happened: layer v66 had the fix, but `live` still pointed at version 11,
# bound to layer v65.
#
# The fix is to stop putting first-party application code in the layer at
# all. `agents/` moved to lambdas/pipeline/agents/, inside the pipeline
# functions' own CodeUri, where SAM hashes real packaged file content --
# not a template-text diff -- so an agents/ change always changes that
# hash and always publishes a new version.
# ---------------------------------------------------------------------------


def test_agents_not_in_layer_build_first_party_list():
    """agents/ must never re-enter the shared layer's FIRST_PARTY list.

    A layer mounts at /opt/python, which precedes /var/task on sys.path, so
    a stale copy there would also silently shadow the real one. First-party
    application code belongs in the function's own CodeUri (hashed by SAM
    from real file content); only genuine third-party dependencies belong
    in the layer.
    """
    packages = _first_party_packages_in_layer_build_sh()
    assert "agents" not in packages, (
        "agents/ is back in layer/build.sh's FIRST_PARTY list. It must ship "
        "inside lambdas/pipeline/agents/, as part of the pipeline "
        "functions' CodeUri, instead -- a layer-only change does not "
        "reliably publish a new Lambda version (see "
        "test_alias_all_properties_set_whenever_any_function_auto_publishes "
        "and the comment block above)."
    )


def test_agents_package_lives_under_lambdas_pipeline():
    """agents/ must be a subpackage of lambdas/pipeline/, not a repo-root package.

    lambdas/pipeline/ is the CodeUri SAM/CFN packages for every zip-based
    pipeline Lambda (score_batch, tailor_resume, generate_cover_letter,
    ...), so SAM hashes agents/'s actual file contents as part of that
    function package -- unlike the shared layer, whose SAM-generated
    logical ID is a content hash of the resolved template computed
    independently of whether version-publishing already ran.
    """
    assert (REPO / "lambdas" / "pipeline" / "agents" / "__init__.py").is_file(), (
        "agents/ is not at lambdas/pipeline/agents/ -- it must live inside "
        "the pipeline functions' CodeUri, not at the repo root, so its "
        "changes are packaged (and hashed) with the function itself"
    )
    assert not (REPO / "agents").is_dir(), (
        "a stale repo-root agents/ directory exists alongside "
        "lambdas/pipeline/agents/ -- two copies of the same package is "
        "exactly the shadowing hazard this move exists to eliminate"
    )


# ---------------------------------------------------------------------------
# retrieval/ (Task 12, pgvector embeddings) applies the agents/ lesson above
# up front instead of relearning it: it is consumed by merge_dedup.py and
# tailor_resume.py, both of which already live inside lambdas/pipeline/, so
# it ships at lambdas/pipeline/retrieval/ -- inside the pipeline functions'
# own CodeUri -- rather than as a repo-root package needing layer/Dockerfile
# parity the way "shared" does. It is deliberately NOT in APP_PACKAGES above
# for the same reason "agents" is not.
# ---------------------------------------------------------------------------


def test_retrieval_not_in_layer_build_first_party_list():
    """retrieval/ must never enter the shared layer's FIRST_PARTY list.

    Same hazard as agents/: a layer mounts at /opt/python, which precedes
    /var/task on sys.path, so a stale copy there would silently shadow the
    real one, and a layer-content-only change does not reliably publish a
    new Lambda version (see
    test_alias_all_properties_set_whenever_any_function_auto_publishes).
    First-party application code belongs in the function's own CodeUri.
    """
    packages = _first_party_packages_in_layer_build_sh()
    assert "retrieval" not in packages, (
        "retrieval/ must not be in layer/build.sh's FIRST_PARTY list. It "
        "ships inside lambdas/pipeline/retrieval/, as part of the pipeline "
        "functions' CodeUri, instead."
    )


def test_retrieval_package_lives_under_lambdas_pipeline():
    """retrieval/ must be a subpackage of lambdas/pipeline/, not a repo-root package.

    lambdas/pipeline/ is the CodeUri SAM/CFN packages for every zip-based
    pipeline Lambda, including merge_dedup and tailor_resume -- retrieval/'s
    two consumers -- so SAM hashes retrieval/'s actual file contents as part
    of that function package, guaranteeing a version publish on every change.
    """
    assert (REPO / "lambdas" / "pipeline" / "retrieval" / "__init__.py").is_file(), (
        "retrieval/ is not at lambdas/pipeline/retrieval/ -- it must live "
        "inside the pipeline functions' CodeUri, not at the repo root, so "
        "its changes are packaged (and hashed) with the function itself"
    )
    assert not (REPO / "retrieval").is_dir(), (
        "a stale repo-root retrieval/ directory exists alongside "
        "lambdas/pipeline/retrieval/ -- two copies of the same package is "
        "exactly the shadowing hazard this move exists to eliminate"
    )


# ---------------------------------------------------------------------------
# guardrails/ (Task 19, guardrail result types + per-task policy) applies the
# agents/ and retrieval/ lesson up front instead of relearning it: it will be
# consumed by tailor_resume.py, score_batch.py and agents/nodes.py, all of
# which already live inside lambdas/pipeline/, so it ships at
# lambdas/pipeline/guardrails/ -- inside the pipeline functions' own CodeUri
# -- rather than as a repo-root package needing layer/Dockerfile parity the
# way "shared" does. It is deliberately NOT in APP_PACKAGES above for the
# same reason "agents" and "retrieval" are not.
# ---------------------------------------------------------------------------


def test_guardrails_not_in_layer_build_first_party_list():
    """guardrails/ must never enter the shared layer's FIRST_PARTY list.

    Same hazard as agents/ and retrieval/: a layer mounts at /opt/python,
    which precedes /var/task on sys.path, so a stale copy there would
    silently shadow the real one, and a layer-content-only change does not
    reliably publish a new Lambda version (see
    test_alias_all_properties_set_whenever_any_function_auto_publishes).
    First-party application code belongs in the function's own CodeUri.
    """
    packages = _first_party_packages_in_layer_build_sh()
    assert "guardrails" not in packages, (
        "guardrails/ must not be in layer/build.sh's FIRST_PARTY list. It "
        "ships inside lambdas/pipeline/guardrails/, as part of the pipeline "
        "functions' CodeUri, instead."
    )


def test_guardrails_package_lives_under_lambdas_pipeline():
    """guardrails/ must be a subpackage of lambdas/pipeline/, not a repo-root package.

    lambdas/pipeline/ is the CodeUri SAM/CFN packages for every zip-based
    pipeline Lambda, including tailor_resume and score_batch -- guardrails/'s
    consumers -- so SAM hashes guardrails/'s actual file contents as part of
    that function package, guaranteeing a version publish on every change.
    """
    assert (REPO / "lambdas" / "pipeline" / "guardrails" / "__init__.py").is_file(), (
        "guardrails/ is not at lambdas/pipeline/guardrails/ -- it must live "
        "inside the pipeline functions' CodeUri, not at the repo root, so "
        "its changes are packaged (and hashed) with the function itself"
    )
    assert not (REPO / "guardrails").is_dir(), (
        "a stale repo-root guardrails/ directory exists alongside "
        "lambdas/pipeline/guardrails/ -- two copies of the same package is "
        "exactly the shadowing hazard this move exists to eliminate"
    )
