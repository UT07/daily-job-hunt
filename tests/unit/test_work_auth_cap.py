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
