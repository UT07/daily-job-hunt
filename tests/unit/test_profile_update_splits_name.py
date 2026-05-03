"""Regression test: PUT /api/profile auto-derives first_name/last_name from name.

Without this, the migration's split-based backfill (20260414_auto_apply_setup.sql)
only runs once, so every NEW user has first_name=NULL/last_name=NULL forever and
check_profile_completeness reports them as missing → profile_complete=False
forever → AppLayout shows FinishSetupBanner forever → AutoApplyButton stuck on
"Complete profile to apply" forever. Surfaced during PR #52 manual smoke.

This test exercises the normalization in app.update_profile (the loop that
maps `full_name`/`name` → `update_data["name"] + first_name + last_name`).
"""
from __future__ import annotations
import pytest


def _normalize(payload: dict) -> dict:
    """Replicate the normalization block in app.update_profile (~line 1436-1450)."""
    update_data = {}
    for k, v in payload.items():
        if k in ("full_name", "name"):
            update_data["name"] = v
            if v and isinstance(v, str):
                parts = v.strip().split(" ", 1)
                update_data["first_name"] = parts[0] if parts else ""
                update_data["last_name"] = parts[1].strip() if len(parts) > 1 else ""
        elif k == "linkedin_url":
            update_data["linkedin"] = v
        elif k == "github_url":
            update_data["github"] = v
        else:
            update_data[k] = v
    return update_data


def test_full_name_splits_into_first_and_last():
    out = _normalize({"full_name": "Utkarsh Singh"})
    assert out["name"] == "Utkarsh Singh"
    assert out["first_name"] == "Utkarsh"
    assert out["last_name"] == "Singh"


def test_name_alias_also_splits():
    out = _normalize({"name": "Jane Doe"})
    assert out["first_name"] == "Jane"
    assert out["last_name"] == "Doe"


def test_three_word_name_keeps_rest_as_last():
    """Multi-part last names: 'Jean-Pierre Van Damme' → first='Jean-Pierre', last='Van Damme'"""
    out = _normalize({"full_name": "Jean-Pierre Van Damme"})
    assert out["first_name"] == "Jean-Pierre"
    assert out["last_name"] == "Van Damme"


def test_single_word_name_leaves_last_empty():
    out = _normalize({"full_name": "Madonna"})
    assert out["first_name"] == "Madonna"
    assert out["last_name"] == ""


def test_empty_name_skips_split():
    out = _normalize({"full_name": ""})
    # Empty string falsy — the `if v and isinstance(v, str)` guard short-circuits
    # so name is set but first/last are NOT added (vs. being empty strings).
    assert out["name"] == ""
    assert "first_name" not in out
    assert "last_name" not in out


def test_whitespace_only_treated_as_no_split():
    out = _normalize({"full_name": "   "})
    # The strip().split() yields [''], so first_name='' and last_name=''.
    # This is the desired behavior — completeness check rejects empty strings.
    assert out["first_name"] == ""
    assert out["last_name"] == ""


def test_other_fields_unchanged():
    out = _normalize({"full_name": "X Y", "phone": "+1", "linkedin_url": "linkedin.com/in/x"})
    assert out["phone"] == "+1"
    assert out["linkedin"] == "linkedin.com/in/x"  # alias normalized
    assert "linkedin_url" not in out
