"""One precedence for "which hash is this job tailored under", everywhere.

shared.tailor_hash.resolve_tailor_hash is the definition app.py uses.
scripts/retailor_bulk.py still carries its own copy; this pins the two to the
same answers until it imports the shared one (CLAUDE.md #10).
"""
import importlib.util
import pathlib

import pytest

from shared.tailor_hash import resolve_tailor_hash

CASES = [
    ({"job_hash": "h", "canonical_hash": "c"}, "h"),
    ({"job_hash": None, "canonical_hash": "c"}, "c"),
    ({"job_hash": "", "canonical_hash": "c"}, "c"),
    ({"canonical_hash": "c"}, "c"),
    ({"job_hash": None, "canonical_hash": None}, None),
    ({"job_hash": "", "canonical_hash": ""}, None),
    ({}, None),
]


@pytest.mark.parametrize("row,want", CASES)
def test_shared_resolver(row, want):
    assert resolve_tailor_hash(row) == want


@pytest.mark.parametrize("row,want", CASES)
def test_script_copy_agrees(row, want):
    path = pathlib.Path(__file__).resolve().parents[2] / "scripts" / "retailor_bulk.py"
    spec = importlib.util.spec_from_file_location("retailor_bulk_parity", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert mod.resolve_tailor_hash(row) == want


def test_app_uses_the_shared_resolver():
    import app
    assert app.resolve_tailor_hash is resolve_tailor_hash
