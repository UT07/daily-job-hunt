from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock
from shared.preview_cache import get_preview_cache, set_preview_cache, build_cache_key


def test_build_cache_key():
    assert build_cache_key("job-123", 2) == "apply_preview:job-123:2"


def test_get_preview_cache_hit():
    db = MagicMock()
    payload = {"eligible": True, "questions": []}
    db.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value.data = [
        {"response": payload}
    ]

    result = get_preview_cache(db, "job-1", resume_version=1)
    assert result == payload


def test_get_preview_cache_miss():
    db = MagicMock()
    db.table.return_value.select.return_value.eq.return_value.gte.return_value.execute.return_value.data = []

    result = get_preview_cache(db, "job-1", resume_version=1)
    assert result is None


def test_set_preview_cache_writes_with_10min_ttl():
    db = MagicMock()
    payload = {"eligible": True}

    set_preview_cache(db, "job-1", resume_version=1, payload=payload, ttl_minutes=10)

    db.table.return_value.upsert.assert_called_once()
    upsert_payload = db.table.return_value.upsert.call_args.args[0]
    assert upsert_payload["cache_key"] == "apply_preview:job-1:1"
    assert upsert_payload["provider"] == "apply_preview"
    assert upsert_payload["model"] == "n/a"
    assert upsert_payload["response"] == payload
    # expires_at should be ~10 min in the future
    expires = datetime.fromisoformat(upsert_payload["expires_at"].replace("Z", "+00:00"))
    delta = expires - datetime.now(timezone.utc)
    assert timedelta(minutes=9) < delta < timedelta(minutes=11)
