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
    """Call AI provider with 5-provider failover chain.

    Order: Groq (fastest) → NVIDIA NIM (free credits, many models) → DeepSeek
    → OpenRouter (free tier) → Qwen. Each provider is tried once; on failure
    (rate limit, timeout, error), the next provider is attempted.
    """
    providers = [
        {"name": "groq", "url": "https://api.groq.com/openai/v1/chat/completions",
         "key_param": "/naukribaba/GROQ_API_KEY", "model": "llama-3.3-70b-versatile",
         "timeout": 60},
        {"name": "nvidia", "url": "https://integrate.api.nvidia.com/v1/chat/completions",
         "key_param": "/naukribaba/NVIDIA_API_KEY", "model": "meta/llama-3.3-70b-instruct",
         "timeout": 120},
        {"name": "deepseek", "url": "https://api.deepseek.com/v1/chat/completions",
         "key_param": "/naukribaba/DEEPSEEK_API_KEY", "model": "deepseek-chat",
         "timeout": 120},
        {"name": "openrouter", "url": "https://openrouter.ai/api/v1/chat/completions",
         "key_param": "/naukribaba/OPENROUTER_API_KEY", "model": "google/gemini-2.0-flash-exp:free",
         "timeout": 90, "extra_headers": {"HTTP-Referer": "https://github.com/UT07/daily-job-hunt"}},
        {"name": "qwen", "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
         "key_param": "/naukribaba/QWEN_API_KEY", "model": "qwen-plus",
         "timeout": 90},
    ]

    messages = []
    if system:
        messages.append({"role": "system", "content": system})
    messages.append({"role": "user", "content": prompt})

    last_error = None
    for provider in providers:
        try:
            api_key = get_param(provider["key_param"])
            if not api_key or api_key == "mock-value":
                continue  # Skip providers without keys configured

            headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
            if "extra_headers" in provider:
                headers.update(provider["extra_headers"])

            resp = httpx.post(
                provider["url"],
                headers=headers,
                json={"model": provider["model"], "messages": messages,
                      "max_tokens": max_tokens, "temperature": 0.3},
                timeout=provider.get("timeout", 60),
            )
            if resp.status_code == 200:
                content = resp.json()["choices"][0]["message"]["content"]
                logger.info(f"[ai] {provider['name']}/{provider['model']} succeeded")
                return content
            elif resp.status_code == 429:
                logger.warning(f"[ai] {provider['name']} rate limited, trying next")
            else:
                logger.warning(f"[ai] {provider['name']} returned {resp.status_code}")
        except Exception as e:
            last_error = e
            logger.warning(f"[ai] {provider['name']} failed: {e}")

    raise RuntimeError(f"All {len(providers)} AI providers failed. Last error: {last_error}")


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
