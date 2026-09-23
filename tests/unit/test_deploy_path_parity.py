"""Every application package must reach BOTH deploy paths.

This repo has shipped a package present in the layer but absent from the
Docker image; lazy imports hid it until runtime. Assert it instead.
"""
import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]

APP_PACKAGES = ["shared", "agents"]


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
