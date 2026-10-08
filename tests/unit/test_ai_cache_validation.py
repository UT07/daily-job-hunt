"""The response cache stores answers the caller can use, under a key that
names everything that shaped them.

1. VALIDATION. `ai_complete_cached` wrote any non-truncated response to the
   72h Supabase cache BEFORE the caller parsed it. A scoring answer that was
   not JSON was therefore replayed for three days: every re-score of that job
   read the same garbage from cache and returned None, and no re-run could
   shake it off. Callers may now pass `validate`; the response is cached only
   when it returns True, and a cached entry that fails it is ignored and
   re-asked (which also clears entries poisoned before this change).
   score_batch's scoring call passes a JSON-parses check.

2. KEY. The key was md5(system|prompt) in ai_helper and sha256(system|prompt|
   extra) in ai_client. Temperature and max_tokens were not in it, so a
   temperature-0 scoring answer was served to a temperature-0.7 caller, and a
   1024-token answer to a caller who asked for 8192. Both are in both keys now.

The Supabase double is a small in-memory table rather than a MagicMock: a
MagicMock select returns a truthy mock and reads as a cache hit (CLAUDE.md #6,
the "phantom cache hit"). Its soundness is asserted first.
"""
import hashlib
import json
import sys
from datetime import datetime

import pytest

sys.path.insert(0, "lambdas/pipeline")
sys.path.insert(0, ".")
import ai_helper  # noqa: E402
import score_batch  # noqa: E402


class _Table:
    def __init__(self, store):
        self.store, self._filters, self._op, self._row = store, {}, None, None

    def select(self, _cols):
        self._op = "select"
        return self

    def eq(self, col, val):
        self._filters[col] = val
        return self

    def gte(self, col, val):
        self._filters[f"{col}>="] = val
        return self

    def upsert(self, row, on_conflict=None):
        self._op, self._row = "upsert", row
        return self

    def execute(self):
        class R:
            pass

        r = R()
        if self._op == "upsert":
            self.store[self._row["cache_key"]] = dict(self._row)
            r.data = [self._row]
        else:
            row = self.store.get(self._filters.get("cache_key"))
            fresh = row is not None and row["expires_at"] >= self._filters.get("expires_at>=", "")
            r.data = [row] if fresh else []
        return r


class _DB:
    def __init__(self):
        self.store = {}

    def table(self, _name):
        return _Table(self.store)


@pytest.fixture
def db(monkeypatch):
    d = _DB()
    monkeypatch.setattr(ai_helper, "get_supabase", lambda: d)
    return d


@pytest.fixture
def calls(monkeypatch):
    """ai_complete replaced by a scripted sequence of answers."""
    seen = []
    script = []

    def fake(prompt, system="", temperature=0.3, max_tokens=4096):
        seen.append({"prompt": prompt, "temperature": temperature, "max_tokens": max_tokens})
        return {"content": script.pop(0), "provider": "p", "model": "m"}

    monkeypatch.setattr(ai_helper, "ai_complete", fake)
    return seen, script


def test_the_supabase_double_round_trips(db):
    """Prove the instrument: written rows are read back; absent ones are not."""
    t = db.table("ai_cache")
    t.upsert({"cache_key": "k", "response": "r", "expires_at": "2999-01-01"}).execute()
    assert db.table("ai_cache").select("*").eq("cache_key", "k").gte(
        "expires_at", datetime.utcnow().isoformat()).execute().data[0]["response"] == "r"
    assert db.table("ai_cache").select("*").eq("cache_key", "nope").gte(
        "expires_at", "2000").execute().data == []


# -- validate -----------------------------------------------------------------

def test_a_response_failing_validation_is_returned_but_not_cached(db, calls):
    seen, script = calls
    script += ["not json", '{"ok": 1}']
    out = ai_helper.ai_complete_cached("p", system="s", validate=ai_helper.json_parses)
    assert out["content"] == "not json", "the caller still gets the answer to handle"
    assert db.store == {}, "an unusable answer must not be replayed for 72h"

    out = ai_helper.ai_complete_cached("p", system="s", validate=ai_helper.json_parses)
    assert out["content"] == '{"ok": 1}' and len(seen) == 2, "the next call must ask again"


def test_a_response_passing_validation_is_cached(db, calls):
    seen, script = calls
    script += ['{"ok": 1}']
    ai_helper.ai_complete_cached("p", system="s", validate=ai_helper.json_parses)
    ai_helper.ai_complete_cached("p", system="s", validate=ai_helper.json_parses)
    assert len(seen) == 1 and len(db.store) == 1


def test_a_validator_that_raises_counts_as_a_rejection(db, calls):
    _seen, script = calls
    script += ["x"]

    def boom(_text):
        raise ValueError("validator bug")

    ai_helper.ai_complete_cached("p", system="s", validate=boom)
    assert db.store == {}


def test_a_poisoned_cache_entry_is_ignored_and_replaced(db, calls):
    """Entries written before this change are still in the table for up to 72h."""
    seen, script = calls
    script += ["not json", '{"ok": 2}']
    ai_helper.ai_complete_cached("p", system="s")            # no validate: poisons
    assert len(db.store) == 1

    out = ai_helper.ai_complete_cached("p", system="s", validate=ai_helper.json_parses)
    assert out["content"] == '{"ok": 2}' and len(seen) == 2
    assert [r["response"] for r in db.store.values()] == ['{"ok": 2}']


def test_without_validate_behaviour_is_unchanged(db, calls):
    """Control: callers who pass nothing still cache what they got."""
    _seen, script = calls
    script += ["anything"]
    ai_helper.ai_complete_cached("p", system="s")
    assert [r["response"] for r in db.store.values()] == ["anything"]


@pytest.mark.parametrize("text,ok", [
    ('{"match_score": 80}', True),
    ('```json\n{"match_score": 80}\n```', True),
    ('```\n{"match_score": 80}\n```', True),
    ("Here is my evaluation: great fit", False),
    ('{"match_score": 80', False),
    ("", False),
])
def test_json_parses(text, ok):
    assert ai_helper.json_parses(text) is ok


# -- cache key -----------------------------------------------------------------

def test_cache_key_names_temperature_and_max_tokens(db, calls):
    seen, script = calls
    script += ['{"a":1}', '{"a":2}', '{"a":3}']
    ai_helper.ai_complete_cached("p", system="s", temperature=0, max_tokens=1024)
    ai_helper.ai_complete_cached("p", system="s", temperature=0.7, max_tokens=1024)
    ai_helper.ai_complete_cached("p", system="s", temperature=0, max_tokens=8192)
    ai_helper.ai_complete_cached("p", system="s", temperature=0, max_tokens=1024)  # hit
    assert len(seen) == 3
    assert len(db.store) == 3


def test_cache_key_is_written_from_an_independent_derivation(db, calls):
    """Hand-built expectation, not read back from the module (a test that
    derives its expectation from the code under test proves consistency, not
    correctness)."""
    _seen, script = calls
    script += ["r"]
    ai_helper.ai_complete_cached("my prompt", system="my system", temperature=0, max_tokens=1024)
    expected = hashlib.md5(b"my system|my prompt|temperature=0|max_tokens=1024").hexdigest()
    assert list(db.store) == [expected]


# -- the score_batch call site ---------------------------------------------------

JOB = {"job_hash": "h1", "title": "SRE", "company": "Acme", "description": "Go, k8s"}
RESUME = r"\documentclass{article}\begin{document}Jane\end{document}"


def test_score_call_site_does_not_cache_an_unparseable_answer(db, calls):
    _seen, script = calls
    script += ["I think this is a strong match overall."]
    assert score_batch.score_single_job(JOB, RESUME) is None
    assert db.store == {}, "an unparseable scoring answer blocked this job for 72h"


def test_score_call_site_caches_a_parseable_answer(db, calls):
    _seen, script = calls
    script += [json.dumps({"ats_score": 80, "hiring_manager_score": 70, "tech_recruiter_score": 90})]
    out = score_batch.score_single_job(JOB, RESUME)
    assert out["match_score"] == 80
    assert len(db.store) == 1


def test_eval_harness_stand_in_accepts_what_the_call_site_passes(monkeypatch):
    """evals/harness.py swaps ai_complete_cached for `_uncached_ai_complete`
    when repeats > 1. Its signature lacked `skip_cache` (already passed by
    score_single_job), so every repeated eval call raised TypeError, was
    caught as a model failure, and the case reported zero scores. Same
    defect class as the council doubles: a stand-in with the wrong signature."""
    from evals import harness

    monkeypatch.setattr(harness, "ai_complete", lambda *a, **kw: {
        "content": json.dumps({"ats_score": 80, "hiring_manager_score": 80, "tech_recruiter_score": 80}),
        "provider": "p", "model": "m"})
    case = {"id": "c1", "title": "SRE", "company": "Acme", "description": "Go",
            "expected": {"tier": "A"}}
    result = harness._run_score_case(case, resume_tex=RESUME, repeats=2)
    assert result["n_failed_calls"] == 0, result
    assert result["scores"] == [80, 80]


# -- ai_client's SQLite cache ----------------------------------------------------

def _client(tmp_path, max_tokens=4096):
    import requests

    import ai_client

    cache = ai_client.ResponseCache(db_path=str(tmp_path / "c.db"))
    provider = ai_client.GroqProvider(api_key="k", model="m", max_tokens=max_tokens)
    return ai_client.AIClient([provider], cache=cache), requests


def test_ai_client_cache_keys_on_temperature(tmp_path, monkeypatch):
    client, requests = _client(tmp_path)
    n = []

    def post(url, headers=None, json=None, timeout=None):  # noqa: A002
        n.append(json["temperature"])
        r = requests.Response()
        r.status_code = 200
        r._content = __import__("json").dumps(
            {"choices": [{"message": {"content": f"t={json['temperature']}"}, "finish_reason": "stop"}]}
        ).encode()
        return r

    monkeypatch.setattr(requests, "post", post)
    assert client.complete("p", temperature=0) == "t=0"
    assert client.complete("p", temperature=0.9) == "t=0.9", "served a temperature-0 answer"
    assert client.complete("p", temperature=0) == "t=0"
    assert n == [0, 0.9], "same temperature must still hit the cache"


def test_ai_client_cache_keys_on_max_tokens(tmp_path, monkeypatch):
    import ai_client

    small, requests = _client(tmp_path, max_tokens=1024)
    big = ai_client.AIClient([ai_client.GroqProvider(api_key="k", model="m", max_tokens=8192)],
                             cache=small.cache)
    n = []

    def post(url, headers=None, json=None, timeout=None):  # noqa: A002
        n.append(json["max_tokens"])
        r = requests.Response()
        r.status_code = 200
        r._content = __import__("json").dumps(
            {"choices": [{"message": {"content": f"m={json['max_tokens']}"}, "finish_reason": "stop"}]}
        ).encode()
        return r

    monkeypatch.setattr(requests, "post", post)
    assert small.complete("p") == "m=1024"
    assert big.complete("p") == "m=8192", "a 1024-token answer was served to an 8192 budget"
    assert n == [1024, 8192]
