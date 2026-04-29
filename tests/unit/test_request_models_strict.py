"""Meta-test: every *Request Pydantic model in app.py must use extra='forbid'.

This guards against silent field drops where the frontend sends a key that the
backend model doesn't declare. Without extra='forbid' the value is silently
dropped and the request appears to succeed.

If a model intentionally needs to accept arbitrary extras, add it to
REQUEST_MODEL_EXEMPTIONS below with a documented reason — never just remove
the strict-mode call.
"""
from __future__ import annotations

import inspect

import app as app_module
from pydantic import BaseModel

# name -> short reason. Each entry should describe a tracked follow-up.
REQUEST_MODEL_EXEMPTIONS: dict[str, str] = {
    # Cluster A is adding apply_url to this model. Re-enable forbid once it
    # merges.
    "SingleJobRunRequest": "cluster-a-pending: apply_url field being added",
}


def _all_request_models() -> list[type[BaseModel]]:
    models: list[type[BaseModel]] = []
    for name, obj in inspect.getmembers(app_module, inspect.isclass):
        if not issubclass(obj, BaseModel) or obj is BaseModel:
            continue
        if not name.endswith("Request"):
            continue
        # Only models actually defined in app.py (not imported)
        if obj.__module__ != app_module.__name__:
            continue
        models.append(obj)
    return models


def test_at_least_one_request_model_exists() -> None:
    """Sanity guard: discovery must find the *Request models."""
    assert len(_all_request_models()) >= 5


def test_all_request_models_forbid_extra_fields() -> None:
    """Every *Request model must reject unknown fields.

    Reasoning: silent extras have caused multiple production bugs where the
    frontend sent data the backend never processed (e.g. apply_url on
    SingleJobRunRequest, enabled_sources on search-config). With
    extra='forbid' these become explicit 422 errors at request time.
    """
    offenders: list[str] = []
    for model in _all_request_models():
        if model.__name__ in REQUEST_MODEL_EXEMPTIONS:
            continue
        # ConfigDict stores 'extra' under model_config['extra'] in pydantic v2
        extra = model.model_config.get("extra")
        if extra != "forbid":
            offenders.append(f"{model.__name__} (extra={extra!r})")

    assert not offenders, (
        "The following *Request models do not set extra='forbid':\n  - "
        + "\n  - ".join(offenders)
        + "\n\nAdd `model_config = ConfigDict(extra='forbid')` to each. "
        "If a model truly needs to accept arbitrary fields, add it to "
        "REQUEST_MODEL_EXEMPTIONS with a documented reason."
    )
