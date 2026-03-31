"""Lightweight AI helper for Lambda functions. Calls AI providers via httpx."""
import hashlib
import json
import logging
import os
from datetime import datetime, timedelta

import boto3
import httpx

logger = logging.getLogger()
ssm = boto3.client("ssm")


def get_param(name):
    return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]


def get_supabase():
    from supabase import create_client
    return create_client(get_param("/naukribaba/SUPABASE_URL"), get_param("/naukribaba/SUPABASE_SERVICE_KEY"))


def ai_complete(prompt: str, system: str = "", max_tokens: int = 4096) -> str:
    """Call AI provider with failover: Groq -> DeepSeek -> OpenRouter."""
    providers = [
        {"name": "groq", "url": "https://api.groq.com/openai/v1/chat/completions",
         "key_param": "/naukribaba/GROQ_API_KEY", "model": "llama-3.3-70b-versatile"},
    ]

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    for provider in providers:
        try:
            api_key = get_param(provider["key_param"])
            resp = httpx.post(
                provider["url"],
                headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
                json={"model": provider["model"], "messages": messages, "max_tokens": max_tokens, "temperature": 0.3},
                timeout=60,
            )
            if resp.status_code == 200:
                return resp.json()["choices"][0]["message"]["content"]
            logger.warning(f"[ai] {provider['name']} returned {resp.status_code}")
        except Exception as e:
            logger.warning(f"[ai] {provider['name']} failed: {e}")

    raise RuntimeError("All AI providers failed")


def ai_complete_cached(prompt: str, system: str = "", cache_hours: int = 72) -> str:
    """AI complete with Supabase cache."""
    cache_key = hashlib.md5(f"{system}|{prompt}".encode()).hexdigest()
    db = get_supabase()

    # Check cache
    cached = db.table("ai_cache").select("response") \
        .eq("cache_key", cache_key) \
        .gte("expires_at", datetime.utcnow().isoformat()).execute()
    if cached.data:
        return cached.data[0]["response"]

    # Call AI
    response = ai_complete(prompt, system)

    # Cache result
    db.table("ai_cache").upsert({
        "cache_key": cache_key,
        "response": response,
        "provider": "groq",
        "model": "auto",
        "expires_at": (datetime.utcnow() + timedelta(hours=cache_hours)).isoformat(),
    }, on_conflict="cache_key").execute()

    return response
