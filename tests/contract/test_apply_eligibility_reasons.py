"""Contract test: every reason returned by /api/apply/eligibility must match
the enum in shared/eligibility_reasons.json. The frontend reads the same
JSON file at hooks/useApplyEligibility.js — pinning here means a backend
reason added without updating the JSON breaks CI before it reaches users.

Why: Smart Apply Phase 1 spec §6.2 requires this pinning; same drift class
as PR #44's AddJob payload contract test.
"""
from __future__ import annotations
import json
import re
from pathlib import Path

PROJECT_ROOT = Path(__file__).parent.parent.parent
ENUM_FILE = PROJECT_ROOT / "shared" / "eligibility_reasons.json"
APP_PY = PROJECT_ROOT / "app.py"


def test_backend_reasons_match_shared_enum():
    """Scrape every `return {"eligible": False, "reason": "..."}` literal in
    the apply_eligibility endpoint and assert each is in the enum file."""
    enum = json.loads(ENUM_FILE.read_text())
    declared = set(enum["ineligibility_reasons"])

    src = APP_PY.read_text()
    # Slice from `def apply_eligibility` to the next `def` to scope the scan.
    start = src.index("def apply_eligibility")
    end = src.index("\ndef ", start + 1)
    fn_src = src[start:end]

    pattern = re.compile(r'"reason":\s*"([a-z_]+)"')
    found = set(pattern.findall(fn_src))

    extra_in_code = found - declared
    missing_in_enum = declared - found

    assert not extra_in_code, (
        f"app.py:apply_eligibility returns reason(s) not in shared/eligibility_reasons.json: {extra_in_code}. "
        f"Add them to the JSON file or remove them from the endpoint."
    )
    assert not missing_in_enum, (
        f"shared/eligibility_reasons.json declares reason(s) the backend never returns: {missing_in_enum}. "
        f"Either add the branch in app.py:apply_eligibility or remove from the JSON."
    )
