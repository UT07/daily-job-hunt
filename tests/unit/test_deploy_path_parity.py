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
AGENTS_DIR = REPO / "lambdas" / "pipeline" / "agents"

# agents/_ai_helper.py is the resolution shim this incident produced: it
# tries the flat `import ai_helper` first -- which now resolves under
# pytest AND in the deployed zip Lambda, since agents/ moved specifically to
# make those two shapes identical (see "part 3" below) -- and falls back to
# `from lambdas.pipeline import ai_helper` (the container-image shape) only
# inside a guarded `except ImportError:`, a fallback that never even
# executes in the flattened Lambda where the original bug actually bit.
# That one guarded line is the sanctioned exception this test exists to
# enforce everywhere else: every OTHER module under agents/ must go through
# the shim (or through agents.providers) instead of reaching into
# lambdas.pipeline directly.
_AGENTS_MODULES = sorted(p for p in AGENTS_DIR.glob("*.py") if p.name != "_ai_helper.py")


@pytest.mark.parametrize("path", _AGENTS_MODULES, ids=lambda p: p.name)
def test_agents_modules_never_import_lambdas_package_directly(path):
    """No module under agents/ (other than the _ai_helper shim) may contain
    an `import lambdas...` / `from lambdas... import ...` statement -- that
    import cannot resolve once CodeUri flattens lambdas/pipeline/ into a zip
    Lambda's /var/task, which is exactly what production hit.

    There are now TWO sanctioned guarded fallbacks to `lambdas.pipeline...`
    in this codebase, and both are structurally exempt from THIS particular
    scan for the same reason: neither is a module living inside agents/, so
    `_AGENTS_MODULES` (built from `AGENTS_DIR.glob("*.py")`) never picks
    them up to begin with.
      1. agents/_ai_helper.py's own `except ImportError:` branch (excluded
         above by filename) -- falls back to `lambdas.pipeline.ai_helper`.
      2. lambdas/pipeline/ai_helper.py's `council_complete()` -- its
         `except ImportError:` branch falls back to
         `lambdas.pipeline.agents.graph` for the container image (see that
         function's docstring). That file is agents/'s SIBLING
         (lambdas/pipeline/ai_helper.py), not a module "under agents/", so
         it was never in scope of this scan in the first place.

    Walks the AST instead of grepping text, so a docstring or comment that
    merely *mentions* "lambdas.pipeline" -- as several docstrings in this
    repo now deliberately do, to document this very incident -- can never
    trip this check. Only a real import statement can.
    """
    tree = ast.parse(path.read_text(), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                assert not (alias.name == "lambdas" or alias.name.startswith("lambdas.")), (
                    f"{path.relative_to(REPO)}: `import {alias.name}` cannot resolve in "
                    f"the deployed (flattened) Lambda -- route through agents._ai_helper "
                    f"or agents.providers instead"
                )
        elif isinstance(node, ast.ImportFrom):
            module = node.module or ""
            assert not (module == "lambdas" or module.startswith("lambdas.")), (
                f"{path.relative_to(REPO)}: `from {module} import ...` cannot resolve in "
                f"the deployed (flattened) Lambda -- route through agents._ai_helper or "
                f"agents.providers instead"
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
