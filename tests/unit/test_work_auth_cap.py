"""Tests for shared/work_auth.py — geography- and work-auth-aware score capping."""
from shared.work_auth import (
    apply_geo_score_cap,
    apply_work_auth_cap,  # alias
    NON_HOME_COUNTRY_CAP,
    SPONSOR_REQUIRED_CAP,
    _detect_country,
)


# Single-tenant default user shape: Dublin-based, IE only
_USER_AUTH = {
    "IE": "stamp1g - full-time eligible",
    "UK": "requires_visa",
    "US": "requires_sponsorship",
    "EU": "requires_visa",
}


def _make_score(match=92, ats=90, hm=92, tech=94, gaps=None):
    return {
        "match_score": match,
        "ats_score": ats,
        "hiring_manager_score": hm,
        "tech_recruiter_score": tech,
        "gaps": list(gaps or []),
    }


# --------------------------------------------------------------------------
# Country detection
# --------------------------------------------------------------------------

class TestDetectCountry:
    def test_dublin_ireland(self):
        assert _detect_country("Dublin, Ireland") == "IE"
        assert _detect_country("Dublin") == "IE"
        assert _detect_country("Ireland") == "IE"

    def test_us_variants(self):
        assert _detect_country("San Francisco, CA") == "US"
        assert _detect_country("New York, NY") == "US"
        assert _detect_country("Remote, USA") == "US"
        assert _detect_country("United States") == "US"

    def test_uk_variants(self):
        assert _detect_country("London, UK") == "UK"
        assert _detect_country("United Kingdom") == "UK"

    def test_ambiguous_returns_none(self):
        # Preserve recall on truly-ambiguous strings
        assert _detect_country("Remote") is None
        assert _detect_country("Anywhere") is None
        assert _detect_country("EMEA") is None
        assert _detect_country("") is None
        assert _detect_country(None) is None

    def test_germany(self):
        assert _detect_country("Berlin, Germany") == "DE"


# --------------------------------------------------------------------------
# Geography cap (NEW: non-home-country = A-tier max)
# --------------------------------------------------------------------------

class TestGeoCapHomeCountry:
    def test_dublin_job_not_capped(self):
        s = _make_score(match=95)
        job = {"location": "Dublin, Ireland", "description": ""}
        r = apply_geo_score_cap(s, job, _USER_AUTH)
        assert r["match_score"] == 95  # unchanged

    def test_remote_ireland_not_capped(self):
        s = _make_score(match=92)
        job = {"location": "Ireland (Remote)", "description": ""}
        r = apply_geo_score_cap(s, job, _USER_AUTH)
        assert r["match_score"] == 92


class TestGeoCapNonHomeCountry:
    def test_uk_job_with_sponsor_signal_capped_at_a_tier(self):
        # UK requires_visa + employer says "we sponsor" → still cap at A-tier (89)
        # because the candidate prefers home-country jobs even when sponsored
        s = _make_score(match=95, ats=93, hm=95, tech=97)
        job = {"location": "London, UK", "description": "We will sponsor visas for the right candidate."}
        r = apply_geo_score_cap(s, job, _USER_AUTH)
        assert r["match_score"] == NON_HOME_COUNTRY_CAP == 89
        assert r["ats_score"] == 89
        assert r["hiring_manager_score"] == 89
        assert r["tech_recruiter_score"] == 89
        assert "job_outside_ie" in r["gaps"]

    def test_uk_job_no_sponsor_signal_capped_at_b_tier(self):
        # UK requires_visa + no sponsor signal → cap at B-tier (70)
        s = _make_score(match=95)
        job = {"location": "London, UK", "description": "Senior backend engineer..."}
        r = apply_geo_score_cap(s, job, _USER_AUTH)
        assert r["match_score"] == SPONSOR_REQUIRED_CAP == 70
        assert "requires_uk_visa_sponsorship" in r["gaps"]

    def test_us_job_no_sponsor_signal_capped_b_tier(self):
        s = _make_score(match=92)
        job = {"location": "San Francisco, CA", "description": "Build cool things."}
        r = apply_geo_score_cap(s, job, _USER_AUTH)
        assert r["match_score"] == 70
        assert "requires_us_visa_sponsorship" in r["gaps"]

    def test_us_job_h1b_signal_caps_at_a_tier_only(self):
        # H1B mention is a hard sponsor signal → only the geo-cap applies (A-tier),
        # not the stricter sponsor cap (B-tier)
        s = _make_score(match=95)
        job = {"location": "New York, NY", "description": "We sponsor H1B visas."}
        r = apply_geo_score_cap(s, job, _USER_AUTH)
        assert r["match_score"] == NON_HOME_COUNTRY_CAP == 89
        assert "job_outside_ie" in r["gaps"]
        # Sponsorship gap should NOT appear because employer sponsors
        assert "requires_us_visa_sponsorship" not in r["gaps"]

    def test_relocation_signal_uses_soft_cap(self):
        s = _make_score(match=92)
        job = {"location": "Berlin, Germany",
               "description": "Open to international candidates with relocation support."}
        r = apply_geo_score_cap(s, job, _USER_AUTH)
        # DE not in user_auth → not requires_sponsorship, just non-home cap
        assert r["match_score"] == NON_HOME_COUNTRY_CAP == 89


class TestGeoCapAmbiguousLocation:
    def test_remote_no_country_no_cap(self):
        # Preserve recall on ambiguous "Remote" — could be IE-friendly
        s = _make_score(match=95)
        job = {"location": "Remote", "description": ""}
        r = apply_geo_score_cap(s, job, _USER_AUTH)
        assert r["match_score"] == 95


class TestGeoCapEdgeCases:
    def test_empty_score_result_returned_as_is(self):
        assert apply_geo_score_cap({}, {"location": "London, UK"}, _USER_AUTH) == {}
        assert apply_geo_score_cap(None, {"location": "London, UK"}, _USER_AUTH) is None

    def test_no_location_no_cap(self):
        s = _make_score(match=95)
        r = apply_geo_score_cap(s, {"description": ""}, _USER_AUTH)
        assert r["match_score"] == 95

    def test_lower_score_not_raised_by_cap(self):
        # Cap should never RAISE a score, only lower it
        s = _make_score(match=50, ats=45, hm=55, tech=60)
        job = {"location": "London, UK", "description": ""}
        r = apply_geo_score_cap(s, job, _USER_AUTH)
        assert r["match_score"] == 50
        assert r["ats_score"] == 45
        assert r["hiring_manager_score"] == 55
        assert r["tech_recruiter_score"] == 60

    def test_idempotent(self):
        s = _make_score(match=95)
        job = {"location": "London, UK", "description": ""}
        r1 = apply_geo_score_cap(s, job, _USER_AUTH)
        r2 = apply_geo_score_cap(r1, job, _USER_AUTH)
        assert r1["match_score"] == r2["match_score"]
        # gaps should not duplicate
        assert r2["gaps"].count("requires_uk_visa_sponsorship") == 1

    def test_empty_user_auth_still_caps_geo(self):
        # Even with no auth dict, non-home country still caps at A-tier
        s = _make_score(match=95)
        job = {"location": "London, UK", "description": ""}
        r = apply_geo_score_cap(s, job, None)
        # Without user_auth we can't determine sponsorship → only geo cap applies
        assert r["match_score"] == NON_HOME_COUNTRY_CAP == 89

    def test_alias_works(self):
        # Backwards-compat alias should produce identical results
        s1 = _make_score(match=95)
        s2 = _make_score(match=95)
        job = {"location": "London, UK", "description": "We sponsor."}
        r1 = apply_geo_score_cap(s1, job, _USER_AUTH)
        r2 = apply_work_auth_cap(s2, job, _USER_AUTH)
        assert r1["match_score"] == r2["match_score"]


# --------------------------------------------------------------------------
# Key/value normalisation — the ISO-vs-name mismatch (fixed 2026-09-30)
#
# `_detect_country` emits codes ("US"); the onboarding form takes the country as
# FREE TEXT, so `users.work_authorizations` is keyed by names. `get("US")` on a
# name-keyed dict is None, so _requires_sponsorship returned False for every
# country and SPONSOR_REQUIRED_CAP had never fired in production -- a US role
# needing sponsorship capped at 89 (A) instead of 70 (B).
#
# Measured on production: 126 of 1,313 scored jobs change score, 92 demote A->B
# (US 87, UK 34, DE 5).
# --------------------------------------------------------------------------

from shared.work_auth import _normalize_key, _requires_sponsorship  # noqa: E402

# Read live from the production `users` row on 2026-09-30.
_PROD_AUTH = {
    "India": "citizen",
    "Germany": "requires_sponsorship",
    "Ireland": "stamp_1g",
    "United States": "requires_sponsorship",
    "United Kingdom": "requires_sponsorship",
}

# Every value web/src/pages/Onboarding.jsx WorkAuthRow can write.
_FORM_VALUES_AUTHORIZED = ("citizen", "permanent_resident", "stamp_1g", "stamp_4")
_FORM_VALUES_NEEDING_SPONSORSHIP = ("work_visa", "requires_sponsorship")


class TestCountryKeyNormalisation:
    def test_the_real_production_dict_fires_the_cap(self):
        """The regression. Before the fix every one of these was False."""
        assert _requires_sponsorship(_PROD_AUTH, "US") is True
        assert _requires_sponsorship(_PROD_AUTH, "UK") is True
        assert _requires_sponsorship(_PROD_AUTH, "DE") is True

    def test_an_authorized_country_in_the_real_dict_does_not(self):
        """India is "citizen" — the fix must not cap indiscriminately."""
        assert _requires_sponsorship(_PROD_AUTH, "IN") is False

    def test_iso_keyed_dicts_still_work(self):
        """No regression: the shape every existing test and caller uses."""
        assert _requires_sponsorship(_USER_AUTH, "US") is True
        assert _requires_sponsorship(_USER_AUTH, "IE") is False

    def test_a_dict_mixing_names_and_codes_resolves_both(self):
        mixed = {"United States": "requires_sponsorship", "IE": "stamp1g"}
        assert _requires_sponsorship(mixed, "US") is True
        assert _requires_sponsorship(mixed, "IE") is False

    def test_a_country_absent_from_the_profile_is_not_assumed(self):
        """Deliberate, and the reason the cap stays recall-preserving.

        The production row lists no entry for Canada, France, Singapore or the
        Netherlands, and 40 scored jobs sit in those countries. Silence is not
        evidence the candidate needs sponsorship there, so it must not cap.
        Removing this would demote 40 more jobs on no data at all.
        """
        for code in ("CA", "FR", "SG", "NL"):
            assert _requires_sponsorship(_PROD_AUTH, code) is False, code

    def test_aliases_resolve_to_the_internal_code(self):
        for alias, code in [
            ("United States", "US"), ("USA", "US"), ("u.s.", "US"), ("us", "US"),
            ("united_states", "US"), ("United Kingdom", "UK"), ("GB", "UK"),
            ("England", "UK"), ("Ireland", "IE"), ("eire", "IE"),
            ("Deutschland", "DE"), ("India", "IN"), ("the netherlands", "NL"),
        ]:
            assert _normalize_key(alias) == code, alias

    def test_an_unknown_country_normalises_to_none_and_does_not_cap(self):
        assert _normalize_key("Narnia") is None
        assert _requires_sponsorship({"Narnia": "requires_sponsorship"}, "US") is False

    def test_normalisation_is_exact_not_substring(self):
        """"in" is a legitimate whole key for India and a disaster as a substring."""
        assert _normalize_key("in") == "IN"
        assert _normalize_key("indiana") is None
        assert _normalize_key("uskudar") is None
        assert _normalize_key("franchise") is None


class TestAuthStatusValueNormalisation:
    """The half that stops the fix being a downgrade.

    The form writes underscored statuses; _AUTHORIZED_TOKENS spells them with
    spaces. Three of its six options are AUTHORIZED statuses the token list
    could not recognise, so fixing only the key lookup would cap a US permanent
    resident at 70 — hiding good jobs rather than over-promoting bad ones.
    """

    def test_every_authorized_form_value_is_recognised(self):
        for val in _FORM_VALUES_AUTHORIZED:
            assert _requires_sponsorship({"United States": val}, "US") is False, val

    def test_every_sponsorship_form_value_is_recognised(self):
        for val in _FORM_VALUES_NEEDING_SPONSORSHIP:
            assert _requires_sponsorship({"United States": val}, "US") is True, val

    def test_the_form_has_no_value_this_module_cannot_classify(self):
        """Guard the guard: if Onboarding.jsx grows an option, this list is stale."""
        import pathlib
        jsx = pathlib.Path(__file__).resolve().parents[2] / "web/src/pages/Onboarding.jsx"
        if not jsx.exists():
            return
        import re
        block = jsx.read_text()
        i = block.find("onChangeStatus")
        options = set(re.findall(r'<option value="([a-z_0-9]+)"', block[i:i + 1200]))
        options.discard("")
        known = set(_FORM_VALUES_AUTHORIZED) | set(_FORM_VALUES_NEEDING_SPONSORSHIP)
        assert options and options <= known, (
            f"Onboarding.jsx offers work-auth statuses this module has never been "
            f"tested against: {sorted(options - known)}. An unrecognised value falls "
            "to 'requires sponsorship' and silently caps the candidate at B-tier."
        )

    def test_an_empty_status_does_not_cap(self):
        """An unanswered row is missing data, not a declaration."""
        assert _requires_sponsorship({"United States": ""}, "US") is False
        assert _requires_sponsorship({"United States": None}, "US") is False


def test_the_config_yaml_shape_still_resolves():
    """The third live key shape, and the reason the bug looked like working code.

    config.yaml's profile.work_authorization is keyed with lowercase names AND a
    lowercase code -- {"ireland": ..., "india": ..., "us": ...}. The old lookup's
    `get(country.lower())` fallback matched "us" there, so single-user/local mode
    behaved correctly and only the database path (country NAMES, title-cased)
    silently failed. One lookup, three data sources, one of them tested.

    Read from the committed file rather than a fixture, so a change to the real
    config is what fails this.
    """
    import pathlib

    import yaml

    root = pathlib.Path(__file__).resolve().parents[2]
    cfg = yaml.safe_load((root / "config.yaml").read_text()) or {}
    wa = (cfg.get("profile") or {}).get("work_authorization") or {}
    assert wa, "config.yaml has no profile.work_authorization — premise gone"

    assert {_normalize_key(k) for k in wa} <= {"IE", "IN", "US", "UK", "DE", "CA",
                                               "FR", "NL", "SG", "AU"}, wa
    assert all(_normalize_key(k) is not None for k in wa), (
        f"config.yaml names a country this module cannot resolve: "
        f"{[k for k in wa if _normalize_key(k) is None]} — it would silently "
        "never cap for that country"
    )
    assert _requires_sponsorship(wa, "US") is True
    assert _requires_sponsorship(wa, "IE") is False


def test_the_eval_gate_cannot_see_this_cap():
    """Names a coverage gap, so a green AI Eval Gate is not read as covering it.

    `shared/work_auth.py` is in the eval gate's path filter, so touching it runs
    the golden set — which looks like coverage and is not. Not one of the 26
    golden cases carries a `location` field, and `evals/harness.py` never calls
    `apply_geo_score_cap`. `_detect_country(None)` returns None, so the cap
    returns before doing anything on every single case.

    That matters because the cap is not a corner: it applies to 290 of 1,313
    scored production jobs (22%), and the bug this file's other tests cover had
    been live in production for months with the gate green throughout.

    Closing it means adding located cases with expected tiers calibrated for a
    capped score, which is its own job. Until then this test is the honest
    statement of what the gate does not measure (CLAUDE.md rules 1 and 7).
    """
    import json
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    golden = sorted((root / "evals" / "golden").glob("*.json"))
    assert golden, "no golden cases found — this test's premise cannot be checked"

    located = [
        f.name for f in golden
        if (json.loads(f.read_text()) or {}).get("location")
    ]
    harness = (root / "evals" / "harness.py").read_text()
    calls_cap = "apply_geo_score_cap" in harness

    assert not located and not calls_cap, (
        "the eval gate now exercises the geo/work-auth cap "
        f"(located cases: {located}, harness calls cap: {calls_cap}). That is an "
        "improvement — delete this test and rely on the gate, but first confirm "
        "the expected tiers in those cases were calibrated WITH the cap applied "
        "and not against uncapped scores."
    )
