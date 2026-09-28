"""shared/location_policy.py — the geographic policy every scraper consults.

The bug these tests pin: until 2026-09-28 the owner's configured locations
(`user_search_configs.locations`, edited in Settings.jsx) reached
`load_config.py` and were used only to salt a cache key. Each board scraper
carried its own byte-identical hardcoded copy of

    LOCATION_KEYWORDS = {"ireland", "dublin", "remote", "emea", "europe",
                         "anywhere"}

so changing Locations in the UI changed nothing about what got scraped.
"""
from shared.location_policy import (
    LEGACY_DEFAULT_LOCATIONS,
    build_location_policy,
    location_matches,
    location_verdict,
    normalize_locations,
    regions_named,
)

OWNER = ["Dublin", "Ireland"]


def _owner_policy():
    return build_location_policy(OWNER)


# ---------------------------------------------------------------------------
# normalize_locations — the jsonb column has more than one shape in this repo
# ---------------------------------------------------------------------------


class TestNormalizeLocations:
    def test_flat_list(self):
        assert normalize_locations(["Dublin", "Ireland"]) == ["Dublin", "Ireland"]

    def test_config_yaml_nesting(self):
        raw = {"primary": ["Dublin"], "secondary": ["Cork", "Galway"]}
        assert sorted(normalize_locations(raw)) == ["Cork", "Dublin", "Galway"]

    def test_bare_string(self):
        assert normalize_locations("Ireland") == ["Ireland"]

    def test_none_and_empty(self):
        assert normalize_locations(None) == []
        assert normalize_locations([]) == []
        assert normalize_locations("") == []

    def test_blank_entries_dropped(self):
        assert normalize_locations(["Dublin", "", "  "]) == ["Dublin"]


# ---------------------------------------------------------------------------
# The policy derives from the user, and ONLY from the user
# ---------------------------------------------------------------------------


class TestPolicyDerivesFromUser:
    def test_owner_config_expands_to_its_region(self):
        policy = _owner_policy()
        assert policy.source == "user_config"
        assert policy.target_regions == frozenset({"ireland"})
        # Sibling cities of a region the user targets come along: they typed
        # the country, so Cork is inside what they asked for.
        assert {"dublin", "ireland", "cork", "galway", "limerick"} <= policy.accept_tokens

    def test_containers_of_the_targeted_region_are_accepted(self):
        policy = _owner_policy()
        assert {"europe", "emea", "eu"} <= policy.accept_tokens

    def test_containers_of_other_regions_are_not(self):
        """EMEA is admitted because Ireland is in it, not because it is a
        universal. A Dublin user must not inherit APAC or North America."""
        policy = _owner_policy()
        assert "apac" not in policy.accept_tokens
        assert "north america" not in policy.accept_tokens

    def test_mumbai_user_gets_the_mirror_image_with_no_special_casing(self):
        policy = build_location_policy(["Mumbai"])
        assert policy.target_regions == frozenset({"india"})
        assert {"bangalore", "hyderabad", "apac", "asia"} <= policy.accept_tokens
        assert "dublin" not in policy.accept_tokens
        assert "europe" not in policy.accept_tokens

    def test_unrecognised_location_still_works_literally(self):
        """A place with no reference data gets no expansion but is honoured."""
        policy = build_location_policy(["Lisbon"])
        assert policy.target_regions == frozenset()
        assert "lisbon" in policy.accept_tokens
        assert location_matches("Lisbon, Portugal", policy)
        assert not location_matches("Berlin, Germany", policy)

    def test_user_who_typed_remote_gets_remote_as_a_place(self):
        """Settings.jsx's own placeholder is "e.g. Dublin, Remote, London", so
        a user can type Remote. Then it IS a place they asked for and must
        admit, unlike the hardcoded universal this module replaced."""
        policy = build_location_policy(["Dublin", "Remote"])
        assert location_matches("Remote - US", policy)


# ---------------------------------------------------------------------------
# The decision on "remote"/"anywhere"/"emea": derived, not universal
# ---------------------------------------------------------------------------


class TestRemoteIsNotAUniversalAdmit:
    def test_unqualified_remote_admits(self):
        policy = _owner_policy()
        assert location_matches("Remote", policy)
        assert location_matches("Remote (Worldwide)", policy)
        assert location_matches("Remote (Anywhere)", policy)

    def test_country_scoped_remote_rejects(self):
        """THE regression. 'Remote - US' is a US-only role that happens to be
        work-from-home. Reading the bare word 'remote' as a universal admit is
        what made the old filter admit 100% of every Greenhouse board."""
        policy = _owner_policy()
        for loc in ("Remote - US", "Remote, US", "Remote - India",
                    "US-Remote", "Remote (USA)", "Remote, Canada; Remote, US",
                    "Remote - Colombia"):
            admit, reason = location_verdict(loc, policy)
            assert not admit, f"{loc!r} should be rejected, got {reason}"
            assert reason.startswith("remote_elsewhere:")

    def test_remote_in_a_targeted_region_admits(self):
        policy = _owner_policy()
        assert location_matches("Remote - Ireland", policy)
        assert location_matches("Remote - EMEA", policy)
        assert location_matches("Remote, Europe", policy)

    def test_multi_location_admits_if_any_part_matches(self):
        policy = _owner_policy()
        assert location_matches("Dublin, Ireland; London, England", policy)
        assert location_matches("Cork, Ireland; Dublin, Ireland", policy)
        assert not location_matches("Remote - USA; San Jose, California, USA", policy)

    def test_distributed_is_not_a_location_independence_marker(self):
        """Cloudflare publishes 56 postings whose location is the bare string
        'Distributed' and whose titles are place-bound ('... - Detroit, MI').
        Measured on the live board 2026-09-28."""
        policy = _owner_policy()
        assert not location_matches("Distributed", policy)
        # ...but "Distributed EMEA" still admits, on EMEA.
        assert location_matches("Distributed, EMEA", policy)


class TestOfficeLocations:
    def test_office_outside_the_target_rejects(self):
        policy = _owner_policy()
        for loc in ("New York, NY (HQ)", "San Francisco, California",
                    "Tokyo, Japan", "North America"):
            admit, reason = location_verdict(loc, policy)
            assert not admit, loc
            assert reason == "outside_target"

    def test_office_inside_the_target_admits(self):
        policy = _owner_policy()
        assert location_matches("Dublin, County Dublin, Ireland", policy)
        assert location_matches("Cork", policy)

    def test_missing_location_fails_open(self):
        """Consistent with merge_dedup Rule 0 (a missing posted_date does not
        reject) and _region_for_location: an input we know nothing about is
        not evidence of irrelevance."""
        policy = _owner_policy()
        assert location_matches("", policy)
        assert location_matches(None, policy)
        assert location_verdict("", policy) == (True, "no_location")


class TestAshbyIsRemoteFlag:
    def test_is_remote_no_longer_admits_an_office_address(self):
        """Ashby's per-posting isRemote used to short-circuit the whole check
        (`if is_remote: return True`), which let 983 'New York, NY (HQ)'
        postings into jobs_raw with no location check at all."""
        policy = _owner_policy()
        assert not location_matches("New York, NY (HQ)", policy, is_remote=True)

    def test_is_remote_with_no_location_admits(self):
        policy = _owner_policy()
        assert location_matches("", policy, is_remote=True)

    def test_is_remote_in_target_admits(self):
        policy = _owner_policy()
        assert location_matches("Dublin, Ireland", policy, is_remote=True)


# ---------------------------------------------------------------------------
# The single-tenant default, and the guarantee it never overrides a user
# ---------------------------------------------------------------------------


class TestLegacyFallback:
    def test_fires_only_when_nothing_is_configured(self):
        policy = build_location_policy([])
        assert policy.source == "legacy_fallback"
        assert policy.locations == tuple(LEGACY_DEFAULT_LOCATIONS)

        assert build_location_policy(None).source == "legacy_fallback"

    def test_never_overrides_a_user_who_expressed_a_preference(self):
        """The whole point. A Mumbai-based user must not inherit Ireland."""
        policy = build_location_policy(["Mumbai"])
        assert policy.source == "user_config"
        assert "ireland" not in policy.accept_tokens
        assert not location_matches("Dublin, Ireland", policy)
        assert location_matches("Mumbai, India", policy)

    def test_a_single_unrecognised_location_still_suppresses_it(self):
        policy = build_location_policy(["Reykjavik"])
        assert policy.source == "user_config"
        assert "ireland" not in policy.accept_tokens


# ---------------------------------------------------------------------------
# search_term — the boards whose URL takes exactly one location
# ---------------------------------------------------------------------------


class TestSearchTerm:
    def test_prefers_the_country_over_a_city_inside_it(self):
        """Pins that threading config into LinkedIn/Indeed cannot narrow the
        owner's results: template.yaml hardcoded "Ireland" and this still
        resolves to "Ireland" from ['Dublin', 'Ireland']."""
        assert _owner_policy().search_term == "Ireland"

    def test_order_independent(self):
        assert build_location_policy(["Ireland", "Dublin"]).search_term == "Ireland"

    def test_falls_back_to_the_first_when_no_country_is_named(self):
        assert build_location_policy(["Dublin"]).search_term == "Dublin"
        assert build_location_policy(["Mumbai", "Pune"]).search_term == "Mumbai"

    def test_legacy_singular_location_string_still_works(self):
        """Back-compat for a caller passing the old scalar `location` key."""
        assert build_location_policy("Ireland").search_term == "Ireland"

    def test_unconfigured_matches_the_value_template_yaml_used_to_hardcode(self):
        assert build_location_policy(None).search_term == "Ireland"


class TestRegionsNamed:
    def test_returns_every_region_a_multi_location_string_names(self):
        assert regions_named("Remote, Canada; Remote, US") == frozenset({"us"})
        assert regions_named("Dublin, Ireland; London, England") == frozenset(
            {"ireland", "uk"}
        )

    def test_us_token_is_padded_so_it_does_not_match_inside_a_word(self):
        assert "us" not in regions_named("Austria")
        assert "us" not in regions_named("Belarus")


class TestTokenMatchingIsWordBounded:
    """Short accept tokens must not match inside longer words.

    Found by measurement, not by review: the first version of this module
    matched accept tokens as bare substrings and admitted
    'Landeshauptstadt Munchen, DEU' -- a Munich office -- because the "eu"
    container sits inside "DEU". Same class as merge_dedup's documented "cto"
    inside "director".
    """

    def test_eu_does_not_match_inside_a_word(self):
        policy = _owner_policy()
        assert not location_matches("Landeshauptstadt Munchen, DEU", policy)
        assert not location_matches("Seoul, South Korea", policy)
        assert location_matches("EU Remote", policy)

    def test_cork_still_matches_through_punctuation(self):
        policy = _owner_policy()
        for loc in ("Cork", "Cork, County Cork", "Cork, Munster, IE, T12 H682",
                    "Cork Metropolitan Area"):
            assert location_matches(loc, policy), loc

    def test_us_matches_through_a_hyphen_and_a_dot(self):
        """merge_dedup's " us " padding misses both of these; word boundaries
        on a normalised haystack catch them."""
        policy = _owner_policy()
        assert not location_matches("US-Remote", policy)
        assert not location_matches("Remote, U.S", policy)
        assert regions_named("US-Remote") == frozenset({"us"})

    def test_globally_is_stripped_whole(self):
        assert location_matches("Hiring Globally", _owner_policy())


class TestTheUserCanWidenItThemselves:
    """The measured cost of this change for the owner is 56 remote-UK roles
    (GitLab SRE/backend, mostly), which "Remote, United Kingdom" says require
    UK authorisation. That is a deliberate, reported narrowing -- and because
    the policy is derived rather than hardcoded, adding the UK in Settings
    brings every one of them back with no code change. Pinned so a later
    refactor cannot quietly remove the escape hatch."""

    def test_adding_the_uk_readmits_uk_remote_roles(self):
        narrow = build_location_policy(["Dublin", "Ireland"])
        assert not location_matches("Remote, United Kingdom", narrow)

        widened = build_location_policy(["Dublin", "Ireland", "United Kingdom"])
        assert location_matches("Remote, United Kingdom", widened)
        assert location_matches("London, England", widened)
        # ...and still not the US.
        assert not location_matches("Remote - US", widened)

    def test_adding_remote_readmits_everything_remote(self):
        widened = build_location_policy(["Dublin", "Ireland", "Remote"])
        assert location_matches("Remote - US", widened)
        assert not location_matches("New York, NY (HQ)", widened)
