from unittest.mock import MagicMock, patch

import pytest

from retrieval import embeddings


def _fake_response(values):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"embedding": {"values": values}}
    return r


def _fake_batch_response(values_list):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"embeddings": [{"values": v} for v in values_list]}
    return r


def test_embed_returns_768_floats():
    with patch.object(embeddings, "_api_key", return_value="k"), \
         patch.object(embeddings, "_cache_get", return_value=None), \
         patch.object(embeddings, "_cache_put"), \
         patch("httpx.post", return_value=_fake_response([0.1] * 768)):
        vec = embeddings.embed("hello")
    assert len(vec) == embeddings.EMBED_DIM == 768


def test_embed_serves_from_cache_without_network():
    cached = [0.5] * 768
    with patch.object(embeddings, "_cache_get", return_value=cached), \
         patch("httpx.post", side_effect=AssertionError("must not call network")):
        assert embeddings.embed("hello") == cached


def test_cache_key_is_content_addressed():
    a = embeddings.cache_key("same text")
    b = embeddings.cache_key("same text")
    c = embeddings.cache_key("other text")
    assert a == b != c
    assert a.startswith("embed:")


def test_embed_raises_on_wrong_dimension():
    # A model swap that silently changes dimensions would corrupt the index.
    import pytest
    with patch.object(embeddings, "_api_key", return_value="k"), \
         patch.object(embeddings, "_cache_get", return_value=None), \
         patch("httpx.post", return_value=_fake_response([0.1] * 512)):
        with pytest.raises(ValueError, match="768"):
            embeddings.embed("hello")


# ---------------------------------------------------------------------------
# Finding 1 (fix round 1): cache_key must tell models/dimensions apart, and a
# cache hit must go through the same dimension guard as a live API response.
# ---------------------------------------------------------------------------

def test_cache_key_changes_with_model():
    text = "same text"
    original = embeddings.cache_key(text)
    with patch.object(embeddings, "MODEL", "models/text-embedding-005"):
        changed = embeddings.cache_key(text)
    assert changed != original
    assert changed.startswith("embed:")


def test_cache_key_changes_with_dimension():
    text = "same text"
    original = embeddings.cache_key(text)
    with patch.object(embeddings, "EMBED_DIM", 1536):
        changed = embeddings.cache_key(text)
    assert changed != original


def test_embed_cache_hit_with_wrong_dimension_is_treated_as_miss_and_refetched():
    # Simulates a row written under an old model/before this guard existed.
    stale = [0.5] * 512
    with patch.object(embeddings, "_api_key", return_value="k"), \
         patch.object(embeddings, "_cache_get", return_value=stale), \
         patch.object(embeddings, "_cache_put") as mock_put, \
         patch("httpx.post", return_value=_fake_response([0.1] * 768)):
        vec = embeddings.embed("hello")
    # Self-heals: no exception, falls through to a live fetch, re-caches the
    # freshly-fetched (correct-dimension) vector.
    assert len(vec) == 768
    mock_put.assert_called_once()


def test_embed_batch_cache_hit_with_wrong_dimension_is_refetched():
    # First text's cache hit is poisoned (wrong dimension); second is a
    # plain miss. Both must be treated as misses and sent to the batch API.
    with patch.object(embeddings, "_api_key", return_value="k"), \
         patch.object(embeddings, "_cache_get", side_effect=[[0.9] * 512, None]), \
         patch.object(embeddings, "_cache_put"), \
         patch("httpx.post", return_value=_fake_batch_response([[0.2] * 768, [0.3] * 768])) as mock_post:
        vecs = embeddings.embed_batch(["stale", "missing"])
    assert [len(v) for v in vecs] == [768, 768]
    sent = mock_post.call_args.kwargs["json"]["requests"]
    assert len(sent) == 2


# ---------------------------------------------------------------------------
# Finding 2 (fix round 1): embed_batch had zero coverage of its own
# interleaving logic, which is exactly where a cache/API misalignment would
# silently pair the wrong vector with the wrong text.
# ---------------------------------------------------------------------------

def test_embed_batch_all_cached_returns_in_order_without_network():
    vec_a = [0.1] * 768
    vec_b = [0.2] * 768
    cached = {embeddings.cache_key("A"): vec_a, embeddings.cache_key("B"): vec_b}
    with patch.object(embeddings, "_cache_get", side_effect=lambda k: cached[k]), \
         patch("httpx.post", side_effect=AssertionError("must not call network")):
        result = embeddings.embed_batch(["A", "B"])
    assert result == [vec_a, vec_b]


def test_embed_batch_none_cached_calls_api_for_all_in_order():
    vec_a = [0.1] * 768
    vec_b = [0.2] * 768
    with patch.object(embeddings, "_api_key", return_value="k"), \
         patch.object(embeddings, "_cache_get", return_value=None), \
         patch.object(embeddings, "_cache_put") as mock_put, \
         patch("httpx.post", return_value=_fake_batch_response([vec_a, vec_b])) as mock_post:
        result = embeddings.embed_batch(["A", "B"])
    assert result == [vec_a, vec_b]
    sent_texts = [r["content"]["parts"][0]["text"] for r in mock_post.call_args.kwargs["json"]["requests"]]
    assert sent_texts == ["A", "B"]
    assert mock_put.call_count == 2


def test_embed_batch_partial_cache_preserves_order_and_calls_api_only_for_misses():
    # The important case: only the middle text is cached. A misaligned
    # zip/index in embed_batch's interleaving would put the wrong vector in
    # the wrong slot -- distinguishable values make sure that would fail.
    vec_a = [0.1] * 768
    vec_b_cached = [0.9] * 768
    vec_c = [0.3] * 768

    def fake_cache_get(key):
        return vec_b_cached if key == embeddings.cache_key("B") else None

    with patch.object(embeddings, "_api_key", return_value="k"), \
         patch.object(embeddings, "_cache_get", side_effect=fake_cache_get), \
         patch.object(embeddings, "_cache_put"), \
         patch("httpx.post", return_value=_fake_batch_response([vec_a, vec_c])) as mock_post:
        result = embeddings.embed_batch(["A", "B", "C"])

    sent_texts = [r["content"]["parts"][0]["text"] for r in mock_post.call_args.kwargs["json"]["requests"]]
    assert sent_texts == ["A", "C"]
    assert result == [vec_a, vec_b_cached, vec_c]


def test_embed_batch_raises_on_wrong_dimension():
    with patch.object(embeddings, "_api_key", return_value="k"), \
         patch.object(embeddings, "_cache_get", return_value=None), \
         patch("httpx.post", return_value=_fake_batch_response([[0.1] * 512])):
        with pytest.raises(ValueError, match="768"):
            embeddings.embed_batch(["A"])


def test_embed_batch_empty_input_returns_empty_list_without_network():
    with patch.object(embeddings, "_cache_get", side_effect=AssertionError("must not touch cache for empty input")), \
         patch("httpx.post", side_effect=AssertionError("must not call network")):
        assert embeddings.embed_batch([]) == []
