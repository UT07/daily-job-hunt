"""One precedence for "which hash is this job tailored under", everywhere.

shared.tailor_hash.resolve_tailor_hash is the only definition. app.py and
scripts/retailor_bulk.py both import it; the script used to carry its own copy,
pinned here by a parity test. A parity test only proves two copies agree on the
cases someone thought of, so the copy was deleted and this file now asserts
there is exactly one `def resolve_tailor_hash` in the repo (CLAUDE.md #10).
"""
import importlib.util
import pathlib
import re

import pytest

from shared.tailor_hash import resolve_tailor_hash

ROOT = pathlib.Path(__file__).resolve().parents[2]
_DEF = re.compile(r"^\s*def resolve_tailor_hash\(", re.M)

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


def _load_script():
    path = ROOT / "scripts" / "retailor_bulk.py"
    spec = importlib.util.spec_from_file_location("retailor_bulk_parity", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_script_uses_the_shared_resolver():
    assert _load_script().resolve_tailor_hash is resolve_tailor_hash


def test_exactly_one_definition_in_the_repo():
    skip = {"node_modules", ".git", ".venv", ".aws-sam", ".claude", "web"}
    defs = [p.relative_to(ROOT).as_posix() for p in ROOT.rglob("*.py")
            if not skip.intersection(p.relative_to(ROOT).parts)
            and _DEF.search(p.read_text(errors="ignore"))]
    assert defs == ["shared/tailor_hash.py"]


def test_app_uses_the_shared_resolver():
    import app
    assert app.resolve_tailor_hash is resolve_tailor_hash
