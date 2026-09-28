"""Every `$.key` a scraper Task reads must be a key LoadUserConfig produces.

Companion to test_state_machine_asl_valid.py, which checks that the embedded
ASL uses only fields the language allows. This checks the other half of the
same failure class, and the one this file was added for: a JSONPath reference
in a state's `Parameters` is NOT validated at deploy time either. `sam
validate --lint` accepts it, CloudFormation accepts it, the stack reaches
UPDATE_COMPLETE — and then the first execution dies at runtime with

    States.Runtime: The JSONPath '$.locations' specified for the field
    'locations.$' could not be found in the input

which, in RunScrapers, takes the whole Parallel state and therefore the whole
daily run down.

So rather than pinning a hand-written contract that can rot, this runs the
real `load_config.handler` against a mocked DB and compares its actual output
keys with the references the template actually makes. Both halves are read
from the things that ship.

Scoped to the RunScrapers branches on purpose: that Parallel state has no
InputPath, so each branch's Task sees exactly LoadUserConfig's output. States
after MergeAndDedup read keys other states produced ($.dedup_result, ...) and
are a different contract.

KNOWN GAP, pre-existing and deliberately not widened here: load_config has one
early return, `{"error": "no_users", "user_id": "default"}` for a database with
no users at all, which satisfies none of this contract. It already fails on
`$.queries` today, so adding `locations` to it would not make that path work --
it needs the state machine to branch on `$.error` before RunScrapers, which is
a separate change. The parametrisation below covers every path that returns a
real config.
"""
import json
import re
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

TEMPLATE = Path(__file__).resolve().parents[2] / "template.yaml"


def _daily_pipeline_definition():
    """Parse the daily pipeline's ASL out of template.yaml."""
    text = TEMPLATE.read_text()
    for match in re.finditer(r'"StartAt"\s*:\s*"LoadUserConfig"', text):
        brace = text.rindex("{", 0, match.start())
        depth = 0
        for i, ch in enumerate(text[brace:], brace):
            if ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    body = text[brace:i + 1]
                    break
        else:
            continue
        body = re.sub(r"\$\{[^}]+\}", "arn:aws:lambda:eu-west-1:0:function:x", body)
        return json.loads(body)
    raise AssertionError("daily pipeline definition not found in template.yaml")


def _scraper_jsonpath_refs():
    """Top-level `$.key` names referenced by any Task inside RunScrapers."""
    definition = _daily_pipeline_definition()
    run_scrapers = definition["States"]["RunScrapers"]
    assert run_scrapers["Type"] == "Parallel", run_scrapers["Type"]

    refs = {}
    for branch in run_scrapers["Branches"]:
        for name, state in branch["States"].items():
            for field, value in (state.get("Parameters") or {}).items():
                if not field.endswith(".$") or not isinstance(value, str):
                    continue
                match = re.match(r"^\$\.([A-Za-z_][A-Za-z0-9_]*)", value)
                assert match, f"{name}.{field} references {value!r}, which this test cannot parse"
                refs.setdefault(match.group(1), []).append(f"{name}.{field}")
    assert refs, "no JSONPath references found in RunScrapers — parser broken?"
    return refs


def _run_load_config(search_config_row):
    """Run the real load_config.handler against a mocked Supabase."""
    empty = MagicMock()
    empty.data = []

    search = MagicMock()
    search.data = [search_config_row] if search_config_row is not None else []

    def _chain(result):
        chain = MagicMock()
        chain.select.return_value = chain
        chain.eq.return_value = chain
        chain.in_.return_value = chain
        chain.limit.return_value = chain
        chain.execute.return_value = result
        return chain

    db = MagicMock()
    db.table.side_effect = lambda name: _chain(
        search if name == "user_search_configs" else empty
    )

    with patch("load_config.get_supabase", return_value=db):
        import load_config
        return load_config.handler({"user_id": "user-42"}, None)


LIVE_ROW = {
    # Mirrors the real production row, columns and all (checked against the
    # live user_search_configs table 2026-09-28).
    "queries": ["Site Reliability Engineer", "DevOps Engineer"],
    "locations": ["Dublin", "Ireland"],
    "geo_regions": None,
    "experience_levels": ["mid_level", "entry_level"],
    "min_match_score": 60,
    "days_back": 7,
    "max_jobs_per_run": 150,
}


@pytest.mark.parametrize(
    "row,label",
    [
        (LIVE_ROW, "live production row"),
        (None, "no search-config row (load_config's own defaults)"),
        ({"queries": [], "locations": None}, "locations explicitly null"),
        ({"queries": [], "locations": {"primary": ["Dublin"]}}, "config.yaml nesting"),
    ],
)
def test_every_scraper_jsonpath_ref_is_produced_by_load_config(row, label):
    produced = set(_run_load_config(row))
    missing = {
        ref: where for ref, where in _scraper_jsonpath_refs().items()
        if ref not in produced
    }
    assert not missing, (
        f"With {label}, LoadUserConfig does NOT return {sorted(missing)}, but "
        f"RunScrapers reads them: {missing}. Step Functions fails the whole "
        f"Parallel state with States.Runtime at execution time — `sam validate "
        f"--lint` will not catch this."
    )


@pytest.mark.parametrize(
    "row,label",
    [
        (LIVE_ROW, "live production row"),
        (None, "no search-config row"),
        ({"queries": [], "locations": None}, "locations explicitly null"),
        ({"queries": [], "locations": "Ireland"}, "bare string"),
        ({"queries": [], "locations": {"primary": ["Dublin"], "secondary": ["Cork"]}},
         "config.yaml nesting"),
    ],
)
def test_load_config_always_emits_locations_as_a_flat_list(row, label):
    """The scrapers index into this; a dict or None would blow up in the
    Lambda rather than in the state machine."""
    result = _run_load_config(row)
    assert isinstance(result["locations"], list), label
    assert all(isinstance(x, str) for x in result["locations"]), label


def test_scrapers_that_filter_by_location_all_receive_it():
    """Audit gate. A new board scraper that filters by location must be wired
    here too, or it silently keeps whatever default it was written with.

    Full audit of lambdas/scrapers/ as of 2026-09-28, so the next person does
    not have to redo it:

      scrape_greenhouse  client-side keyword filter -> now derived. WIRED.
      scrape_ashby       same, plus an isRemote bypass -> now derived. WIRED.
      scrape_linkedin    location goes in the search URL; template.yaml
                         hardcoded "Ireland" -> now derived. WIRED.
      scrape_indeed      same. WIRED.
      scrape_glassdoor   reads a location but pins Glassdoor's numeric
                         locId=104; dormant (its branch is a Pass state).
                         Policy threaded, locId gap documented in the file.
      scrape_adzuna      takes an ISO country CODE, not a location string,
                         and its branch is a Pass state (Adzuna has no "ie").
                         A code lookup is a different problem; left alone.
      scrape_irish       Jobs.ie / IrishJobs / GradIreland are Ireland-only
                         by construction; "Ireland" there is an output field,
                         not a filter.
      scrape_yc          no location gate. Not adding one here: 305 rows, and
                         a new gate needs its own measurement.
      scrape_hn          no location gate; locations are free text parsed out
                         of a comment ("Full-time", blank, ...). Same.
      scrape_apify       generic actor runner, not wired into this state
                         machine at all.
      scrape_contacts    not a job scraper; uses a job's own location as a
                         LinkedIn search hint.
    """
    refs = _scraper_jsonpath_refs()
    receivers = {name.split(".", 1)[0] for name in refs.get("locations", [])}
    assert {"ScrapeLinkedIn", "ScrapeIndeed", "ScrapeGreenhouse", "ScrapeAshby"} <= receivers, (
        f"these scraper states get $.locations: {sorted(receivers)}"
    )


def test_query_hash_still_salted_by_locations():
    """Changing Locations in Settings must bust the per-source scrape cache —
    otherwise the first run after the change returns yesterday's rows."""
    a = _run_load_config({**LIVE_ROW, "locations": ["Dublin", "Ireland"]})
    b = _run_load_config({**LIVE_ROW, "locations": ["Mumbai"]})
    assert a["query_hash"] != b["query_hash"]


def test_owner_query_hash_is_unchanged_by_the_normalisation():
    """load_config now normalises `locations` before hashing. For the live
    row that must be a no-op, or every source's cache is invalidated once for
    no reason on the deploy that ships this."""
    import hashlib
    result = _run_load_config(LIVE_ROW)
    legacy = hashlib.md5(
        f"{'|'.join(LIVE_ROW['queries'])}|{'|'.join(LIVE_ROW['locations'])}".encode()
    ).hexdigest()[:12]
    assert result["query_hash"] == legacy
