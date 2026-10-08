"""The Gemini API key travels in a header, never in the URL.

`GeminiProvider` built `...:generateContent?key=<API_KEY>`. requests puts the
request URL into every HTTPError message ("403 Client Error: Forbidden for
url: ...?key=AIza..."), AIClient folds the last error into
"All providers exhausted. Last error: ...", and app.py surfaces that as
`HTTPException(500, f"AI call failed: {e}")` — to logs and to the client.
retrieval/embeddings.py had the same shape via httpx `params={"key": ...}`,
whose HTTPStatusError message also carries the full URL.

Google documents the `x-goog-api-key` header as the alternative to the query
parameter. Both callers now use it.

The doubles return REAL response objects whose URL is the URL the code asked
for — which is exactly how the key reached the message — so a test against
the old code fails by finding the key in the error text.
"""
import json
import sys

import httpx
import pytest
import requests

sys.path.insert(0, ".")
sys.path.insert(0, "lambdas/pipeline")
import ai_client  # noqa: E402

KEY = "AIzaSy-TEST-SECRET-KEY-0123456789"


def _requests_post_factory(status, seen):
    def post(url, headers=None, json=None, timeout=None, params=None):  # noqa: A002
        seen.append({"url": url, "headers": headers or {}, "params": params})
        r = requests.Response()
        r.status_code = status
        r.reason = "Forbidden" if status == 403 else "OK"
        r.url = url + (("?" + "&".join(f"{k}={v}" for k, v in params.items())) if params else "")
        body = ({"candidates": [{"content": {"parts": [{"text": "hi"}]}, "finishReason": "STOP"}]}
                if status == 200 else {"error": {"message": "API key not valid"}})
        r._content = __import__("json").dumps(body).encode()
        return r
    return post


def test_the_double_puts_the_request_url_in_the_error_message():
    """Prove the instrument: requests' HTTPError text includes the URL."""
    r = requests.Response()
    r.status_code, r.reason, r.url = 403, "Forbidden", "https://x/y?key=SECRET"
    with pytest.raises(requests.HTTPError) as e:
        r.raise_for_status()
    assert "SECRET" in str(e.value)


def test_gemini_sends_the_key_as_a_header_not_a_query_parameter(monkeypatch, tmp_path):
    seen = []
    monkeypatch.setattr(requests, "post", _requests_post_factory(200, seen))
    p = ai_client.GeminiProvider(api_key=KEY, model="gemini-x")
    assert p.complete("hello") == "hi"
    assert KEY not in seen[0]["url"]
    assert KEY not in json.dumps(seen[0]["params"] or {})
    assert seen[0]["headers"].get("x-goog-api-key") == KEY


def test_a_gemini_failure_message_does_not_contain_the_key(monkeypatch, tmp_path):
    monkeypatch.setattr(requests, "post", _requests_post_factory(403, []))
    client = ai_client.AIClient(
        [ai_client.GeminiProvider(api_key=KEY, model="gemini-x")],
        cache=ai_client.ResponseCache(db_path=str(tmp_path / "c.db")),
    )
    with pytest.raises(ai_client.ProviderError) as e:
        client.complete("hello", skip_cache=True)
    assert KEY not in str(e.value), "the API key leaked into the error app.py returns as a 500"


# -- retrieval/embeddings.py ----------------------------------------------------

def _httpx_post_factory(status, seen):
    def post(url, params=None, headers=None, json=None, timeout=None):  # noqa: A002
        seen.append({"url": url, "params": params, "headers": headers or {}})
        req = httpx.Request("POST", url, params=params, headers=headers)
        body = ({"embedding": {"values": [0.1] * 768}} if status == 200
                else {"error": {"message": "API key not valid"}})
        return httpx.Response(status, json=body, request=req)
    return post


@pytest.fixture
def embeddings(monkeypatch):
    from retrieval import embeddings as mod

    monkeypatch.setattr(mod, "_api_key", lambda: KEY)
    monkeypatch.setattr(mod, "_cache_get", lambda _k: None)
    monkeypatch.setattr(mod, "_cache_put", lambda _k, _v: None)
    return mod


def test_embeddings_send_the_key_as_a_header(monkeypatch, embeddings):
    seen = []
    monkeypatch.setattr(httpx, "post", _httpx_post_factory(200, seen))
    embeddings.embed("text")
    assert not seen[0]["params"] or "key" not in seen[0]["params"]
    assert seen[0]["headers"].get("x-goog-api-key") == KEY


def test_an_embeddings_failure_message_does_not_contain_the_key(monkeypatch, embeddings):
    monkeypatch.setattr(httpx, "post", _httpx_post_factory(403, []))
    with pytest.raises(httpx.HTTPStatusError) as e:
        embeddings.embed("text")
    assert KEY not in str(e.value)


def test_batch_embeddings_send_the_key_as_a_header(monkeypatch, embeddings):
    seen = []

    def post(url, params=None, headers=None, json=None, timeout=None):  # noqa: A002
        seen.append({"params": params, "headers": headers or {}})
        req = httpx.Request("POST", url, params=params, headers=headers)
        return httpx.Response(403, json={"error": {}}, request=req)

    monkeypatch.setattr(httpx, "post", post)
    with pytest.raises(httpx.HTTPStatusError) as e:
        embeddings.embed_batch(["a", "b"])
    assert seen[0]["headers"].get("x-goog-api-key") == KEY
    assert KEY not in str(e.value)
