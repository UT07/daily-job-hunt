"""Regression tests for `_derive_header_markers` and `_check_header_present`
in `lambdas/pipeline/tailor_resume.py`.

Background — Bug X2 from comprehensive prod-health investigation:
The previous `_HEADER_MARKERS` was hardcoded to ["Utkarsh Singh",
"254utkarsh@gmail.com"]. When validation ran on a tailored resume
that didn't contain BOTH strings (e.g. AI rewrapped the name across
lines, or for ANY user other than Utkarsh), `_check_header_present`
returned non-empty → validation_errors → fallback to base_tex. The
user's tailored resume was silently replaced by their base resume,
matching the user-reported "regenerate produces same resume" symptom.

The fix derives markers from the user's profile dict at runtime, with
graceful fallbacks (drop a missing field; skip the check entirely if
no profile exists).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `lambdas/pipeline/tailor_resume.py` importable without triggering
# the Lambda's top-level boto3 init.
PROJECT_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(PROJECT_ROOT / "lambdas" / "pipeline"))

from tailor_resume import _check_header_present, _derive_header_markers


class TestDeriveHeaderMarkers:
    def test_full_profile_yields_name_and_email(self):
        profile = {"full_name": "Alice Doe", "email": "alice@example.com"}
        assert _derive_header_markers(profile) == ["Alice Doe", "alice@example.com"]

    def test_name_field_fallback(self):
        # Older schema may use 'name' instead of 'full_name'
        profile = {"name": "Bob", "email": "bob@example.com"}
        assert _derive_header_markers(profile) == ["Bob", "bob@example.com"]

    def test_full_name_takes_precedence_over_name(self):
        profile = {"full_name": "Alice Doe", "name": "Alice", "email": "alice@example.com"}
        assert _derive_header_markers(profile) == ["Alice Doe", "alice@example.com"]

    def test_missing_name_drops_only_that_marker(self):
        profile = {"email": "alice@example.com"}
        assert _derive_header_markers(profile) == ["alice@example.com"]

    def test_missing_email_drops_only_that_marker(self):
        profile = {"full_name": "Alice Doe"}
        assert _derive_header_markers(profile) == ["Alice Doe"]

    def test_blank_strings_treated_as_missing(self):
        profile = {"full_name": "  ", "email": ""}
        assert _derive_header_markers(profile) == []

    def test_none_profile_returns_empty_list(self):
        assert _derive_header_markers(None) == []

    def test_empty_dict_profile_returns_empty_list(self):
        assert _derive_header_markers({}) == []

    def test_no_hardcoded_utkarsh_in_default_path(self):
        """Smoking gun for the multi-tenant bug: default behavior must NOT
        produce Utkarsh-specific markers."""
        profile = {"full_name": "Different Person", "email": "other@example.com"}
        markers = _derive_header_markers(profile)
        assert "Utkarsh Singh" not in markers
        assert "254utkarsh@gmail.com" not in markers


class TestCheckHeaderPresent:
    def test_all_markers_present_returns_empty(self):
        tex = r"""\documentclass{article}
        \begin{document}
        Alice Doe — alice@example.com
        ...
        \end{document}"""
        markers = ["Alice Doe", "alice@example.com"]
        assert _check_header_present(tex, markers) == []

    def test_missing_name_returned(self):
        tex = "alice@example.com"
        markers = ["Alice Doe", "alice@example.com"]
        assert _check_header_present(tex, markers) == ["Alice Doe"]

    def test_missing_email_returned(self):
        tex = "Alice Doe"
        markers = ["Alice Doe", "alice@example.com"]
        assert _check_header_present(tex, markers) == ["alice@example.com"]

    def test_empty_markers_skips_check(self):
        # Anonymous / profile-less path: no markers means no validation.
        # Better than failing every tailor when a profile field is missing.
        assert _check_header_present("anything", []) == []

    def test_does_not_use_hardcoded_utkarsh_markers(self):
        """Regression: if someone re-introduces hardcoded markers, this fails.
        The function is now dependency-injected and must trust the caller's
        list, even if that list is empty or contains different strings."""
        tex_without_utkarsh = "Alice Doe alice@example.com"
        # Even though the tex doesn't contain "Utkarsh", the function must
        # not flag that as missing — it only checks the markers it's given.
        assert _check_header_present(tex_without_utkarsh, ["Alice Doe"]) == []
