"""Repeat scoring calls are pointless while the cache answers them.

score_single_job_deterministic takes the median of num_calls independent calls.
Its docstring told callers to "use skip_cache" — an argument that did not exist
on ai_complete_cached. So every repeat call hit the same cache key and the
"median of three" was the median of one response returned three times: the
variance the median exists to dampen was invisible to it.
"""
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, "lambdas/pipeline")
import ai_helper  # noqa: E402


def _chain(data):
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.gte.return_value = chain
    chain.execute.return_value = MagicMock(data=data)
    return chain


def _db(data):
    db = MagicMock()
    db.table.return_value = _chain(data)
    return db


CACHED = [{"response": "CACHED", "provider": "cache", "model": "cache"}]
LIVE = {"content": "FRESH", "provider": "groq", "model": "m"}


def test_skip_cache_ignores_a_warm_entry():
    db = _db(CACHED)
    with patch.object(ai_helper, "get_supabase", return_value=db), \
         patch.object(ai_helper, "ai_complete", return_value=LIVE) as call:
        out = ai_helper.ai_complete_cached("p", skip_cache=True)
    assert out["content"] == "FRESH", "a warm cache entry was returned despite skip_cache"
    call.assert_called_once()


def test_skip_cache_does_not_write_back():
    """Three bypassed calls must not race to poison the shared key.

    Writing back would mean call 1 populates the cache and calls 2 and 3 read
    it — reintroducing exactly the collapse skip_cache exists to prevent, and
    leaving the last sample visible to the batch pipeline afterwards.
    """
    db = _db([])
    with patch.object(ai_helper, "get_supabase", return_value=db), \
         patch.object(ai_helper, "ai_complete", return_value=LIVE):
        ai_helper.ai_complete_cached("p", skip_cache=True)
    db.table.return_value.upsert.assert_not_called()


def test_default_still_reads_the_cache():
    """The batch pipeline depends on this. It scores ~58 jobs a run against an
    8k tokens/minute Groq ceiling; losing the cache would be a real regression.
    """
    db = _db(CACHED)
    with patch.object(ai_helper, "get_supabase", return_value=db), \
         patch.object(ai_helper, "ai_complete") as call:
        out = ai_helper.ai_complete_cached("p")
    assert out["content"] == "CACHED"
    call.assert_not_called()


def test_default_still_writes_the_cache():
    db = _db([])
    with patch.object(ai_helper, "get_supabase", return_value=db), \
         patch.object(ai_helper, "ai_complete", return_value=LIVE):
        ai_helper.ai_complete_cached("p")
    db.table.return_value.upsert.assert_called_once()
