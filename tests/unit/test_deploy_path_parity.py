"""Every application package must reach BOTH deploy paths.

This repo has shipped a package present in the layer but absent from the
Docker image; lazy imports hid it until runtime. Assert it instead.
"""
import pathlib

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
