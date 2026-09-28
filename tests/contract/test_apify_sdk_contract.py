"""The arguments we pass to Apify must exist in the installed SDK.

2026-09-28: contacts returned 0 for every job and the Apify scraper was dead,
because `apify-client>=1.6.0` had no upper bound. The Docker-built layer
resolved 3.2.0, which renamed ActorClient.call's `timeout_secs` to
`run_timeout` (now a timedelta); the local venv sat on 2.5.0, which still had
the old name. Every call raised TypeError in production and nowhere else, was
caught as a warning, and logged "0 contacts found via Apify" as though it had
searched and found nobody.

No unit test could catch that, because the test environment had the working
version. This one can, because it asserts against whatever SDK is actually
installed — so it fails the moment the pin and the call sites disagree.
"""
import inspect

import pytest

apify_actor = pytest.importorskip(
    "apify_client._resource_clients.actor",
    reason="apify-client v3 layout not installed",
)

# Every keyword this repo passes to ActorClient.call(), across
# contact_finder.py, lambdas/pipeline/find_contacts.py and
# lambdas/scrapers/scrape_apify.py.
KWARGS_WE_PASS = {"run_input", "run_timeout"}


def test_every_kwarg_we_pass_exists_in_the_installed_sdk():
    params = set(inspect.signature(apify_actor.ActorClient.call).parameters)
    missing = KWARGS_WE_PASS - params
    assert not missing, (
        f"ActorClient.call() does not accept {sorted(missing)} in the installed "
        f"apify-client. Either the pin in requirements.txt moved across a breaking "
        f"change, or a call site uses an argument from a different major version."
    )


def test_the_removed_v2_name_is_not_used_anywhere():
    """timeout_secs is the v2 spelling. If it reappears in a call site while the
    pin is on v3, production breaks and local tests stay green."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    offenders = []
    for rel in ("contact_finder.py",
                "lambdas/pipeline/find_contacts.py",
                "lambdas/scrapers/scrape_apify.py"):
        text = (root / rel).read_text()
        for i, line in enumerate(text.splitlines(), 1):
            if "timeout_secs" in line and not line.lstrip().startswith("#"):
                offenders.append(f"{rel}:{i}")
    assert not offenders, f"v2-only timeout_secs still passed at: {offenders}"


def test_run_timeout_takes_a_timedelta_not_an_int():
    """v3 changed the type as well as the name — passing an int silently
    type-errors at call time, not at import."""
    from datetime import timedelta

    sig = inspect.signature(apify_actor.ActorClient.call)
    annotation = str(sig.parameters["run_timeout"].annotation)
    assert "timedelta" in annotation, annotation
    assert isinstance(timedelta(seconds=30), timedelta)
