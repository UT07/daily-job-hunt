"""Probe candidate chat models at REALISTIC prompt size.

ai_council_model_rot.md: models that answer a toy prompt return 413/empty on a
real scoring call. So the probe sends a scoring-shaped prompt of the same order
as production (~3.5k chars) and requires a parseable JSON answer back -- not
just HTTP 200.
"""
import concurrent.futures as cf
import json
import os
import re
import time
import httpx
from dotenv import load_dotenv
load_dotenv("/Users/ut/code/naukribaba/.env")

JD = ("We are hiring a Senior Platform Engineer in Dublin. You will design and "
      "operate multi-tenant Kubernetes infrastructure on AWS, own Terraform "
      "modules, build CI/CD with GitHub Actions, and improve observability with "
      "Prometheus and Grafana. Requirements: 5+ years in backend or platform "
      "engineering, strong Python and Go, deep AWS (EKS, IAM, VPC, Lambda), "
      "Terraform, and experience running production incident response. "
      "Nice to have: Kafka, Postgres tuning, service mesh, cost optimisation. ") * 4
RESUME = ("Utkarsh Singh. Platform and backend engineer, Dublin. Built an "
          "automated job-search pipeline on AWS Step Functions and Lambda with "
          "LangGraph multi-agent orchestration, pgvector retrieval, and a "
          "guardrails layer. Python, FastAPI, Docker, Terraform, GitHub Actions. ") * 3

PROMPT = (f"Job description:\n{JD}\n\nCandidate resume:\n{RESUME}\n\n"
          'Score this match. Reply with ONLY compact JSON: '
          '{"score": <0-100 integer>, "reason": "<one short sentence>"}')

ENDPOINTS = {
    "groq":       ("https://api.groq.com/openai/v1/chat/completions", "GROQ_API_KEY"),
    "openrouter": ("https://openrouter.ai/api/v1/chat/completions",   "OPENROUTER_API_KEY"),
    "nvidia":     ("https://integrate.api.nvidia.com/v1/chat/completions", "NVIDIA_API_KEY"),
    "deepseek":   ("https://api.deepseek.com/chat/completions",       "DEEPSEEK_API_KEY"),
    "qwen":       ("https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions", "QWEN_API_KEY"),
}

# Exclude anything that is not a general chat model.
SKIP = re.compile(
    r"embed|whisper|orpheus|tts|guard|safety|content-safety|topic-control|"
    r"rerank|image|video|vision|deplot|kosmos|fuyu|realtime|translate|"
    r"diffusion|starcoder|codegemma|recurrentgemma|ocr|paraformer|asr|"
    r"tingwu|wan2|z-image|omni|detector|calibration|retriev", re.I)


def probe(provider, model):
    url, keyvar = ENDPOINTS[provider]
    key = os.environ.get(keyvar)
    t0 = time.time()
    try:
        r = httpx.post(url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            json={"model": model, "messages": [{"role": "user", "content": PROMPT}],
                  "max_tokens": 3000, "temperature": 0},
            timeout=100)
        dt = round(time.time() - t0, 1)
        if r.status_code != 200:
            return dict(provider=provider, model=model, ok=False, dt=dt,
                        why=f"HTTP {r.status_code}: {r.text[:70]}")
        body = r.json()
        content = (body["choices"][0]["message"].get("content") or "").strip()
        if not content:
            reasoning = body["choices"][0]["message"].get("reasoning")
            return dict(provider=provider, model=model, ok=False, dt=dt,
                        why="empty content" + (" (reasoning only)" if reasoning else ""))
        m = re.search(r'\{.*?"score".*?\}', content, re.S)
        if not m:
            return dict(provider=provider, model=model, ok=False, dt=dt,
                        why=f"no JSON: {content[:50]!r}")
        try:
            score = json.loads(m.group(0)).get("score")
        except Exception:
            return dict(provider=provider, model=model, ok=False, dt=dt, why="bad JSON")
        if not isinstance(score, (int, float)):
            return dict(provider=provider, model=model, ok=False, dt=dt, why=f"score={score!r}")
        return dict(provider=provider, model=model, ok=True, dt=dt, score=score)
    except Exception as e:
        return dict(provider=provider, model=model, ok=False,
                    dt=round(time.time() - t0, 1), why=f"{type(e).__name__}: {str(e)[:60]}")


def candidates():
    out = []
    for provider, (_, keyvar) in ENDPOINTS.items():
        key = os.environ.get(keyvar)
        if not key:
            continue
        list_url = {"groq": "https://api.groq.com/openai/v1/models",
                    "openrouter": "https://openrouter.ai/api/v1/models",
                    "nvidia": "https://integrate.api.nvidia.com/v1/models",
                    "deepseek": "https://api.deepseek.com/models",
                    "qwen": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/models"}[provider]
        try:
            r = httpx.get(list_url, headers={"Authorization": f"Bearer {key}"}, timeout=25)
            ids = [m["id"] for m in r.json().get("data", [])]
        except Exception:
            continue
        if provider == "openrouter":
            ids = [i for i in ids if i.endswith(":free")]
        if provider == "qwen":  # catalog is huge and dated-snapshot heavy
            ids = [i for i in ids if re.match(r"^(qwen3\.[678]|qwq)", i) and not re.search(r"\d{4}-\d{2}-\d{2}", i)]
        out += [(provider, i) for i in ids if not SKIP.search(i)]
    return out


if __name__ == "__main__":
    cands = candidates()
    print(f"probing {len(cands)} candidates at ~{len(PROMPT)} chars\n", flush=True)
    results = []
    with cf.ThreadPoolExecutor(max_workers=6) as ex:
        for res in ex.map(lambda c: probe(*c), cands):
            results.append(res)
            mark = "PASS" if res["ok"] else "fail"
            extra = f"score={res['score']}" if res["ok"] else res["why"]
            print(f"{mark}  {res['provider']:11} {res['model'][:46]:46} {res['dt']:>5}s  {extra}", flush=True)
    ok = [r for r in results if r["ok"]]
    print(f"\n==== {len(ok)} / {len(results)} PASSED ====")
    json.dump(results, open("/private/tmp/claude-501/-Users-ut-code-naukribaba/9ef2e110-d6a3-44bf-a9da-60262b1243a2/scratchpad/probe_results.json", "w"), indent=1)
