from unittest.mock import MagicMock, patch

from retrieval import embeddings


def _fake_response(values):
    r = MagicMock()
    r.status_code = 200
    r.json.return_value = {"embedding": {"values": values}}
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
