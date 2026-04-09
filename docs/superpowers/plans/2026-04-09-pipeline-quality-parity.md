# Pipeline Quality Parity — Lambda ↔ Local Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Bring Lambda pipeline to full parity with local code quality — real council, improvement loop, quality gates, personalized contacts, career-ops keyword injection — so every generated artifact is production-worthy.

**Architecture:** Port the real `ai_client.py` council system (32 models, multi-critic numeric scoring, model-family dedup) to Lambda's `ai_helper.py`. Add a score-and-improve loop to the Step Functions state machine. Adopt career-ops prompt patterns: keyword injection ("rephrase, don't invent"), archetype-based framing, proof-point cover letters. Replace hardcoded contact messages with AI-generated ones. Wire cover letter compilation into S3 storage. Add post-generation quality validation that checks writing quality, not just syntax.

**Validation approach:** After deploying, test ONE S-tier job end-to-end through the Lambda pipeline. Download and visually inspect the actual PDF. Only after confirming quality, batch-generate remaining artifacts.

**Tech Stack:** Python 3.11, AWS Lambda, Step Functions (ASL), SAM template.yaml, httpx, Supabase, S3

**Branch:** `feature/pipeline-quality-parity` (created from `main`)

**Career-ops patterns adopted** (from github.com/santifer/career-ops):
- Keyword injection: reformulate existing bullets using JD vocabulary, never fabricate
- Archetype detection: classify jobs (SRE/DevOps, Backend, Full-Stack, Platform, Data) before tailoring
- Proof-point specifics: "reduced MTTR by 35%" > "improved performance"
- Cover letter proof-point mapping: each paragraph maps a JD requirement to a specific achievement
- Extended banned phrases list merged from career-ops _shared.md
- ATS text normalization (em-dashes, smart quotes, zero-width chars)

---

## File Map

| File | Action | Responsibility |
|------|--------|----------------|
| `lambdas/pipeline/ai_helper.py` | **REWRITE** | Port real council: diverse providers, multi-critic numeric scoring, model-family dedup, quality logging |
| `lambdas/pipeline/tailor_resume.py` | **MODIFY** | Always use council (no light-touch bypass), add quality validation (banned phrases, textbf preservation, fabrication check) |
| `lambdas/pipeline/generate_cover_letter.py` | **MODIFY** | Always use council, fix cover letter S3 upload path |
| `lambdas/pipeline/post_score.py` | **REWRITE** | Port score-and-improve loop from `resume_scorer.py` — make it a quality gate, not just analytics |
| `lambdas/pipeline/find_contacts.py` | **MODIFY** | AI-powered role selection and personalized messages (port from `contact_finder.py`) |
| `lambdas/pipeline/save_job.py` | **MODIFY** | Handle improvement loop output, save cover letter S3 URLs properly |
| `template.yaml` | **MODIFY** | Add ImproveResume state after PostScore, add CatchUpMissing state, increase PostScore timeout |
| `.github/workflows/test.yml` | **MODIFY** | Add quality output tests that check writing, not just structure |
| `tests/unit/test_ai_helper_council.py` | **CREATE** | Unit tests for council diversity, scoring, quality validation |
| `tests/unit/test_quality_validation.py` | **CREATE** | Tests for banned phrase detection, textbf preservation, fabrication check |
| `tests/quality/test_output_quality.py` | **CREATE** | Integration tests that verify actual AI output quality |

---

## Task 1: Create feature branch and set up test infrastructure

**Files:**
- Create: `tests/unit/test_ai_helper_council.py`
- Create: `tests/unit/test_quality_validation.py`

- [ ] **Step 1: Create feature branch**

```bash
cd /Users/ut/code/naukribaba
git checkout -b feature/pipeline-quality-parity main
```

- [ ] **Step 2: Create test file for council**

```python
# tests/unit/test_ai_helper_council.py
"""Tests for the Lambda AI council system — diversity, scoring, quality."""
import json
import pytest


class TestProviderDiversity:
    """Verify council picks distinct model families for generation."""

    def test_two_generators_use_different_families(self):
        """Generators must not both be the same underlying model."""
        from lambdas.pipeline.ai_helper import _build_provider_list, _select_diverse_providers

        providers = _build_provider_list()
        selected = _select_diverse_providers(providers, n=2)
        families = [_model_family(p["model"]) for p in selected]
        assert len(set(families)) == len(families), f"Duplicate families: {families}"

    def test_critic_excludes_generators(self):
        """Critic must not be from the same model family as any generator."""
        from lambdas.pipeline.ai_helper import _build_provider_list, _select_diverse_providers, _model_family

        providers = _build_provider_list()
        generators = _select_diverse_providers(providers, n=2)
        gen_families = {_model_family(p["model"]) for p in generators}

        critic = _select_diverse_providers(
            providers, n=1,
            exclude_families=gen_families,
        )
        assert len(critic) == 1
        assert _model_family(critic[0]["model"]) not in gen_families


class TestCriticScoring:
    """Verify critic returns numeric scores, not binary winner."""

    def test_parse_scores_valid_json(self):
        from lambdas.pipeline.ai_helper import _parse_critic_scores

        raw = "[85, 72]"
        scores = _parse_critic_scores(raw, expected_count=2)
        assert scores == [85, 72]

    def test_parse_scores_with_markdown_fence(self):
        from lambdas.pipeline.ai_helper import _parse_critic_scores

        raw = "```json\n[90, 60]\n```"
        scores = _parse_critic_scores(raw, expected_count=2)
        assert scores == [90, 60]

    def test_parse_scores_rejects_wrong_count(self):
        from lambdas.pipeline.ai_helper import _parse_critic_scores

        raw = "[85]"
        scores = _parse_critic_scores(raw, expected_count=2)
        assert scores is None
```

- [ ] **Step 3: Create test file for quality validation**

```python
# tests/unit/test_quality_validation.py
"""Tests for post-generation quality validation — writing quality, not just syntax."""
import pytest


class TestBannedPhraseDetection:
    """Validate that banned filler phrases are caught."""

    def test_catches_filler_in_summary(self):
        from lambdas.pipeline.tailor_resume import _check_banned_phrases

        text = r"\section*{Summary} Highly motivated software engineer with extensive experience in building scalable systems."
        errors = _check_banned_phrases(text)
        assert any("highly motivated" in e.lower() for e in errors)
        assert any("extensive experience" in e.lower() for e in errors)

    def test_passes_clean_text(self):
        from lambdas.pipeline.tailor_resume import _check_banned_phrases

        text = r"\section*{Summary} Software Engineer with 3 years building production microservices. Reduced MTTR by 35\%."
        errors = _check_banned_phrases(text)
        assert errors == []


class TestTextbfPreservation:
    """Validate that bold formatting is preserved from base resume."""

    def test_detects_stripped_bold(self):
        from lambdas.pipeline.tailor_resume import _check_textbf_preservation

        base = r"Built \textbf{8 production microservices} using \textbf{Python} and \textbf{FastAPI}"
        tailored = r"Built 8 production microservices using Python and FastAPI"
        errors = _check_textbf_preservation(base, tailored)
        assert len(errors) > 0
        assert "textbf" in errors[0].lower()

    def test_passes_when_bold_preserved(self):
        from lambdas.pipeline.tailor_resume import _check_textbf_preservation

        base = r"Built \textbf{8 production microservices} using \textbf{Python}"
        tailored = r"Built \textbf{8 production microservices} using \textbf{Python} and Node.js"
        errors = _check_textbf_preservation(base, tailored)
        assert errors == []


class TestFabricationDetection:
    """Validate that skills not in base resume are flagged."""

    def test_detects_added_skill(self):
        from lambdas.pipeline.tailor_resume import _check_fabrication

        base_skills = "Python, TypeScript, React, FastAPI, AWS, Docker, Kubernetes, Terraform"
        tailored = r"\section*{Technical Skills} Languages: Java, Spring Boot, Python, TypeScript"
        errors = _check_fabrication(base_skills, tailored)
        # Java and Spring Boot are not in base
        assert any("java" in e.lower() or "spring boot" in e.lower() for e in errors)

    def test_passes_when_all_skills_in_base(self):
        from lambdas.pipeline.tailor_resume import _check_fabrication

        base_skills = "Python, TypeScript, React, FastAPI, AWS, Docker"
        tailored = r"\section*{Technical Skills} Languages: Python, TypeScript, React"
        errors = _check_fabrication(base_skills, tailored)
        assert errors == []


class TestCoverLetterQuality:
    """Validate cover letter opening quality."""

    def test_rejects_company_description_opener(self):
        from lambdas.pipeline.generate_cover_letter import _check_opening_quality

        text = "Hays is a company that specializes in recruitment and employment services, and their client is seeking a developer."
        errors = _check_opening_quality(text, company="Hays")
        assert len(errors) > 0

    def test_passes_specific_opener(self):
        from lambdas.pipeline.generate_cover_letter import _check_opening_quality

        text = "The observability platform your team shipped last quarter caught my attention. I built something similar at Clover."
        errors = _check_opening_quality(text, company="Datadog")
        assert errors == []
```

- [ ] **Step 4: Run tests to verify they fail**

```bash
source .venv/bin/activate
pytest tests/unit/test_ai_helper_council.py tests/unit/test_quality_validation.py -v --tb=short -x 2>&1 | head -40
```

Expected: FAIL — functions don't exist yet.

- [ ] **Step 5: Commit test skeletons**

```bash
git add tests/unit/test_ai_helper_council.py tests/unit/test_quality_validation.py
git commit -m "test: add council diversity and quality validation test skeletons"
```

---

## Task 2: Rewrite `ai_helper.py` — port real council from `ai_client.py`

**Files:**
- Modify: `lambdas/pipeline/ai_helper.py` (full rewrite of lines 127-197)

The current Lambda council is a binary picker with no diversity guarantee. Port the `ai_client.py` council system:
- `_build_provider_list()` — returns the full provider config list
- `_model_family(model)` — normalizes model names to families for dedup
- `_select_diverse_providers(providers, n, exclude_families)` — picks N providers from distinct model families
- `_parse_critic_scores(raw, expected_count)` — extracts JSON array of scores
- `council_complete()` — rewritten: 2 diverse generators + 1 critic (different family), numeric 0-100 scoring on 4 dimensions, no binary picker

- [ ] **Step 1: Extract provider list into `_build_provider_list()`**

Refactor lines 40-80 of `ai_helper.py` into a standalone function so both `ai_complete()` and `council_complete()` share the same provider pool.

```python
def _build_provider_list() -> list[dict]:
    """Build the full provider config list with all available models."""
    openrouter_headers = {"HTTP-Referer": "https://github.com/UT07/daily-job-hunt"}
    openrouter_key = "/naukribaba/OPENROUTER_API_KEY"
    openrouter_url = "https://openrouter.ai/api/v1/chat/completions"
    openrouter_models = [
        "qwen/qwen3.6-plus:free",
        "nvidia/nemotron-3-super-120b-a12b:free",
        "meta-llama/llama-3.3-70b-instruct:free",
        "z-ai/glm-4.5-air:free",
        "google/gemma-3-27b-it:free",
    ]

    providers = [
        {"name": "groq", "url": "https://api.groq.com/openai/v1/chat/completions",
         "key_param": "/naukribaba/GROQ_API_KEY", "model": "llama-3.3-70b-versatile",
         "timeout": 60},
        {"name": "nvidia", "url": "https://integrate.api.nvidia.com/v1/chat/completions",
         "key_param": "/naukribaba/NVIDIA_API_KEY", "model": "meta/llama-3.3-70b-instruct",
         "timeout": 120},
    ]
    for m in openrouter_models:
        providers.append({
            "name": f"openrouter/{m.split('/')[-1].split(':')[0]}",
            "url": openrouter_url, "key_param": openrouter_key, "model": m,
            "timeout": 90, "extra_headers": openrouter_headers,
        })
    providers.append(
        {"name": "qwen", "url": "https://dashscope-intl.aliyuncs.com/compatible-mode/v1/chat/completions",
         "key_param": "/naukribaba/QWEN_API_KEY", "model": "qwen-plus",
         "timeout": 90},
    )
    return providers
```

- [ ] **Step 2: Add `_model_family()` function**

Port from `ai_client.py:699-714`:

```python
def _model_family(model: str) -> str:
    """Collapse model names to canonical family for dedup."""
    m = model.lower().split("/")[-1]
    m = m.replace(":free", "")
    for prefix in ("deepseek", "llama-3.3", "llama-3.1", "llama-4", "qwen3",
                    "qwen-plus", "qwen-turbo", "qwen-max",
                    "mistral-small", "nemotron", "hermes", "gemma", "glm", "step"):
        if m.startswith(prefix):
            return prefix
    return m
```

- [ ] **Step 3: Add `_select_diverse_providers()`**

Port from `ai_client.py:661-738` but adapted for the flat dict-based provider list:

```python
def _select_diverse_providers(
    providers: list[dict],
    n: int,
    exclude_families: set[str] | None = None,
) -> list[dict]:
    """Pick N providers from distinct model families.

    Shuffles before selection so different runs get different subsets.
    Excludes any families in exclude_families (used to pick critics that
    didn't generate).
    """
    import random as _rng
    exclude_families = exclude_families or set()

    shuffled = list(providers)
    _rng.shuffle(shuffled)

    seen: set[str] = set()
    result: list[dict] = []
    for p in shuffled:
        fam = _model_family(p["model"])
        if fam in seen or fam in exclude_families:
            continue
        seen.add(fam)
        result.append(p)
        if len(result) >= n:
            break
    return result
```

- [ ] **Step 4: Add `_parse_critic_scores()`**

Port from `ai_client.py:948-end`:

```python
def _parse_critic_scores(raw: str, expected_count: int) -> list[int] | None:
    """Extract a JSON array of integer scores from a critic's response."""
    import json as _json
    import re

    text = raw.strip()
    # Strip markdown fences
    if "```" in text:
        parts = text.split("```")
        text = parts[1] if len(parts) >= 2 else text
        if text.startswith("json"):
            text = text[4:]
        text = text.strip()

    # Find JSON array
    match = re.search(r"\[[\d\s,]+\]", text)
    if not match:
        return None

    try:
        scores = _json.loads(match.group())
        if len(scores) != expected_count:
            return None
        return [max(0, min(100, int(s))) for s in scores]
    except (ValueError, TypeError):
        return None
```

- [ ] **Step 5: Rewrite `council_complete()` with real critic scoring**

Replace lines 127-197. Key changes:
1. Generators use `_select_diverse_providers()` — guaranteed different model families
2. Critic uses a DIFFERENT family than any generator
3. Critic scores 0-100 on 4 dimensions (not binary pick)
4. Quality logging to CloudWatch

```python
def council_complete(
    prompt: str,
    system: str = "",
    task_description: str = "",
    n_generators: int = 2,
    temperature: float = 0.3,
) -> dict:
    """Generate multiple AI responses from diverse models, critique with numeric scoring.

    1. Pick n_generators providers from DISTINCT model families
    2. Generate candidates independently
    3. Pick 1 critic from a DIFFERENT family than any generator
    4. Critic scores each candidate 0-100 on accuracy, completeness, quality, adherence
    5. Return highest-scoring candidate

    Falls back to first candidate if critique fails.
    """
    import json

    all_providers = _build_provider_list()

    # Step 1: Select diverse generators
    generators = _select_diverse_providers(all_providers, n=n_generators)
    if not generators:
        raise RuntimeError("Council: no providers available")

    logger.info(f"[council] Generators: {[f'{g['name']}:{g['model']}' for g in generators]}")

    # Step 2: Generate candidates
    candidates = []
    for gen in generators:
        try:
            result = _call_provider(gen, prompt, system, temperature, max_tokens=4096)
            if result and result.get("content"):
                candidates.append(result)
        except Exception as e:
            logger.warning(f"[council] Generator {gen['name']} failed: {e}")

    if not candidates:
        raise RuntimeError("Council: all generators failed")
    if len(candidates) == 1:
        logger.info("[council] Only 1 candidate — returning without critique")
        return candidates[0]

    # Step 3: Select critic from a different model family
    gen_families = {_model_family(g["model"]) for g in generators}
    critics = _select_diverse_providers(all_providers, n=1, exclude_families=gen_families)
    if not critics:
        # Fallback: allow any provider as critic
        critics = _select_diverse_providers(all_providers, n=1)

    critic_provider = critics[0]
    logger.info(f"[council] Critic: {critic_provider['name']}:{critic_provider['model']}")

    # Step 4: Build critique prompt with numeric scoring
    candidate_blocks = []
    for i, c in enumerate(candidates, 1):
        candidate_blocks.append(f"--- CANDIDATE {i} ({c['provider']}:{c['model']}) ---\n{c['content'][:3000]}")

    critique_prompt = (
        f"You are evaluating {len(candidates)} candidate outputs for this task:\n"
        f"{task_description}\n\n"
        "Rate each candidate 0-100 on:\n"
        "1. ACCURACY: Does it follow ALL instructions? No banned phrases, no fabrication?\n"
        "2. COMPLETENESS: Are all required sections/structure present?\n"
        "3. QUALITY: Active voice, specific metrics, no filler, proper formatting (\\textbf preserved)?\n"
        "4. ADHERENCE: Does it match the specific job description, not generic?\n\n"
        "Average the four dimensions into a single score per candidate.\n\n"
        + "\n\n".join(candidate_blocks)
        + "\n\nReturn ONLY a JSON array of integer scores in candidate order, e.g. [85, 72]. No other text."
    )

    try:
        critique = _call_provider(
            critic_provider, critique_prompt,
            system="You are an impartial AI output evaluator. Return only valid JSON.",
            temperature=0, max_tokens=100,
        )
        scores = _parse_critic_scores(critique["content"], len(candidates))
        if scores:
            best_idx = max(range(len(scores)), key=lambda i: scores[i])
            winner = candidates[best_idx]
            logger.info(
                f"[council] Scores: {scores}, Winner: candidate {best_idx+1} "
                f"({winner['provider']}:{winner['model']}) score={scores[best_idx]}"
            )
            return winner
        else:
            logger.warning(f"[council] Could not parse critic scores: {critique['content'][:200]}")
            return candidates[0]
    except Exception as e:
        logger.warning(f"[council] Critique failed ({e}), returning first candidate")
        return candidates[0]
```

- [ ] **Step 6: Extract `_call_provider()` helper from `ai_complete()`**

Refactor the inner loop of `ai_complete()` (lines 85-125) into a standalone function that takes a single provider dict and makes one HTTP call. Both `ai_complete()` and `council_complete()` use it.

```python
def _call_provider(
    provider: dict,
    prompt: str,
    system: str = "",
    temperature: float = 0.3,
    max_tokens: int = 4096,
) -> dict | None:
    """Make a single AI call to one provider. Returns dict with content/provider/model or None."""
    try:
        api_key = get_param(provider["key_param"])
        if not api_key or api_key == "mock-value":
            return None

        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if "extra_headers" in provider:
            headers.update(provider["extra_headers"])

        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        resp = httpx.post(
            provider["url"],
            headers=headers,
            json={"model": provider["model"], "messages": messages,
                  "max_tokens": max_tokens, "temperature": temperature},
            timeout=provider.get("timeout", 60),
        )
        if resp.status_code == 200:
            content = resp.json()["choices"][0]["message"].get("content")
            if not content:
                logger.warning(f"[ai] {provider['name']} returned empty content")
                return None
            return {"content": content, "provider": provider["name"], "model": provider["model"]}
        else:
            logger.warning(f"[ai] {provider['name']} returned {resp.status_code}")
            return None
    except Exception as e:
        logger.warning(f"[ai] {provider['name']} failed: {e}")
        return None
```

- [ ] **Step 7: Refactor `ai_complete()` to use `_build_provider_list()` and `_call_provider()`**

Replace the inline provider construction and HTTP loop with the new helpers.

- [ ] **Step 8: Run council tests**

```bash
pytest tests/unit/test_ai_helper_council.py -v --tb=short -x
```

Expected: TestProviderDiversity and TestCriticScoring PASS.

- [ ] **Step 9: Commit**

```bash
git add lambdas/pipeline/ai_helper.py tests/unit/test_ai_helper_council.py
git commit -m "feat: port real council system to Lambda — diverse generators, numeric critic scoring"
```

---

## Task 3: Add quality validation functions to `tailor_resume.py`

**Files:**
- Modify: `lambdas/pipeline/tailor_resume.py` (add new functions after line 127)

Add three new validation functions that check writing quality, not just LaTeX structure.

- [ ] **Step 1: Add `_check_banned_phrases()` function**

After the existing `_check_brace_balance()` function (line ~127):

```python
_BANNED_PHRASES = [
    "highly motivated", "extensive experience", "proven track record",
    "passionate about", "self-motivated", "team player", "detail-oriented",
    "results-driven", "strong background in", "experienced professional",
    "seasoned professional", "leveraging", "utilizing", "showcasing",
    "demonstrating proficiency", "directly transferable to", "aligned with",
    "outcomes relevant to", "i am excited", "excited to join",
]


def _check_banned_phrases(tex: str) -> list[str]:
    """Check for banned filler phrases in the tailored body."""
    errors = []
    tex_lower = tex.lower()
    for phrase in _BANNED_PHRASES:
        if phrase in tex_lower:
            errors.append(f"banned_phrase: '{phrase}'")
    return errors
```

- [ ] **Step 2: Add `_check_textbf_preservation()` function**

```python
import re


def _check_textbf_preservation(base_body: str, tailored_body: str) -> list[str]:
    """Check that \\textbf{} formatting is preserved from base resume.

    If base has N \\textbf occurrences and tailored has < 50% of N,
    the AI stripped formatting.
    """
    base_count = len(re.findall(r"\\textbf\{", base_body))
    tailored_count = len(re.findall(r"\\textbf\{", tailored_body))

    if base_count == 0:
        return []  # No bold in base, nothing to preserve

    ratio = tailored_count / base_count
    if ratio < 0.5:
        return [
            f"textbf_stripped: base has {base_count} \\textbf, tailored has {tailored_count} "
            f"({ratio:.0%} preserved, need ≥50%)"
        ]
    return []
```

- [ ] **Step 3: Add `_check_fabrication()` function**

```python
def _check_fabrication(base_skills: str, tailored_tex: str) -> list[str]:
    """Check if tailored resume mentions skills not present in base.

    Extracts the Technical Skills section from tailored_tex and compares
    against base_skills string.
    """
    # Known skills that are fine to add (LaTeX formatting variants, common abbreviations)
    _SAFE_ADDITIONS = {
        "ci/cd", "ci", "cd", "rest", "restful", "api", "apis", "oop",
        "tdd", "bdd", "sql", "nosql", "html", "css", "json", "yaml",
        "xml", "http", "https", "tcp", "udp", "grpc", "graphql",
        "agile", "scrum", "kanban", "devops", "sre", "mlops",
        "linux", "unix", "macos", "windows", "bash", "zsh", "shell",
    }

    base_lower = base_skills.lower()
    errors = []

    # Extract Skills section from tailored
    skills_match = re.search(
        r"\\section\*\{(?:Technical )?Skills\}(.*?)\\section\*\{",
        tailored_tex, re.DOTALL
    )
    if not skills_match:
        return []

    skills_text = skills_match.group(1)
    # Extract individual skill words/phrases (comma-separated in LaTeX)
    # Strip LaTeX commands first
    clean = re.sub(r"\\[a-zA-Z]+\{([^}]*)\}", r"\1", skills_text)
    clean = re.sub(r"\\[a-zA-Z]+", "", clean)
    clean = re.sub(r"[{}\\]", "", clean)

    # Split by comma, ampersand, or newline
    items = re.split(r"[,&\n]+", clean)
    for item in items:
        skill = item.strip().lower()
        if not skill or len(skill) < 2:
            continue
        if skill in _SAFE_ADDITIONS:
            continue
        # Check if this skill appears in the base (case-insensitive)
        if skill not in base_lower:
            # Only flag multi-word skills or specific frameworks (not generic words)
            if len(skill) > 3 and " " in skill or skill in (
                "java", "vue.js", "angular", "ruby", "php", "scala", "rust",
                "kotlin", "swift", "dart", "flutter", "spring", "hibernate",
                "django", "rails", "laravel",
            ):
                errors.append(f"fabrication: '{skill.title()}' not in base resume skills")

    return errors
```

- [ ] **Step 4: Run quality validation tests**

```bash
pytest tests/unit/test_quality_validation.py::TestBannedPhraseDetection -v --tb=short -x
pytest tests/unit/test_quality_validation.py::TestTextbfPreservation -v --tb=short -x
pytest tests/unit/test_quality_validation.py::TestFabricationDetection -v --tb=short -x
```

Expected: All PASS.

- [ ] **Step 5: Commit**

```bash
git add lambdas/pipeline/tailor_resume.py tests/unit/test_quality_validation.py
git commit -m "feat: add quality validation — banned phrases, textbf preservation, fabrication check"
```

---

## Task 4: Remove light-touch bypass, add career-ops keyword injection + archetype framing

**Files:**
- Modify: `lambdas/pipeline/tailor_resume.py:215-390` (handler function)
- Modify: `lambdas/pipeline/tailor_resume.py:130-198` (system prompt)

- [ ] **Step 1: Add archetype detection function**

Before the handler, add job archetype classification (adapted from career-ops):

```python
_ARCHETYPES = {
    "sre_devops": {
        "signals": ["SRE", "site reliability", "infrastructure", "terraform", "kubernetes",
                     "monitoring", "incident", "on-call", "uptime", "observability", "platform"],
        "framing": "Emphasize reliability metrics (uptime, MTTR), infrastructure automation, monitoring dashboards, incident response."
    },
    "backend": {
        "signals": ["backend", "API", "microservices", "distributed systems", "database",
                     "REST", "GraphQL", "server-side", "Java", "Go", "Rust"],
        "framing": "Emphasize API design, data modeling, system architecture, performance optimization, testing."
    },
    "fullstack": {
        "signals": ["full-stack", "full stack", "frontend", "React", "Vue", "Angular",
                     "Node.js", "web application", "UI", "UX"],
        "framing": "Emphasize end-to-end ownership, responsive UI, API integration, deployment pipelines."
    },
    "platform_cloud": {
        "signals": ["platform", "cloud engineer", "AWS", "GCP", "Azure", "CI/CD",
                     "deployment", "DevOps", "IaC", "CDK", "CloudFormation"],
        "framing": "Emphasize cloud architecture, cost optimization, CI/CD pipelines, infrastructure as code."
    },
    "data": {
        "signals": ["data engineer", "ETL", "Spark", "analytics", "ML pipeline",
                     "data platform", "warehouse", "Airflow", "dbt"],
        "framing": "Emphasize data pipelines, processing scale, data quality, ML infrastructure."
    },
}


def _detect_archetype(title: str, description: str) -> tuple[str, str]:
    """Classify job into an archetype based on title + description signals.

    Returns (archetype_name, framing_instruction).
    """
    text = f"{title} {description}".lower()
    scores = {}
    for arch, config in _ARCHETYPES.items():
        scores[arch] = sum(1 for s in config["signals"] if s.lower() in text)

    best = max(scores, key=scores.get) if any(scores.values()) else "fullstack"
    return best, _ARCHETYPES[best]["framing"]
```

- [ ] **Step 2: Add career-ops keyword injection rules to system prompt**

Append to `_SYSTEM_PROMPT` (after the existing WRITING STYLE section, before the final "Return ONLY" line):

```python
# Add after line ~196 in _SYSTEM_PROMPT:

KEYWORD INJECTION (CRITICAL — adapted from career-ops methodology):
- Extract the top 15-20 keywords and phrases from the JD below.
- Reformulate EXISTING bullets using JD vocabulary. Example: if the base says "built automated data pipelines" and the JD says "ETL orchestration", rewrite as "orchestrated ETL data pipelines". Same truth, JD words.
- NEVER add skills, tools, or experiences not present in the base resume. This is reformulation, not fabrication.
- Distribute keywords strategically:
  * Summary: must contain the top 5 JD keywords
  * First bullet of each job: must contain at least 1 JD keyword
  * Skills section: reorder to front-load JD-matching skills
- Prefer proof-point specifics over abstractions:
  * "Reduced MTTR by 35% across 8 production microservices" > "improved system reliability"
  * "Processed 2M+ daily events via Kafka pipelines" > "built scalable data infrastructure"
  * Use ONLY metrics that already exist in the base resume — do NOT invent numbers.

ARCHETYPE FRAMING:
{archetype_framing}
```

- [ ] **Step 3: Wire archetype into the handler**

In the handler, after loading the job description, detect archetype and inject framing:

```python
    archetype, archetype_framing = _detect_archetype(title, description)
    logger.info(f"[tailor] Archetype: {archetype} for '{title}' at '{company}'")

    # Build system prompt with archetype framing injected
    system_prompt = _SYSTEM_PROMPT.replace("{archetype_framing}", archetype_framing)
```

- [ ] **Step 4: Remove light-touch single-call path**

ALL tailoring goes through `council_complete()`. Replace lines ~277-290:

```python
    # ALWAYS use council — no single-call bypass regardless of tier
    logger.info(f"[tailor] Council mode for {job_hash} (depth={tailoring_depth}, archetype={archetype})")
    try:
        response_dict = council_complete(
            prompt=user_prompt,
            system=system_prompt,
            task_description=(
                f"Tailor resume for '{title}' at '{company}' (archetype: {archetype}). "
                f"Depth: {tailoring_depth}. "
                "Pick the candidate with best JD keyword injection (reformulated, not fabricated), "
                "complete sections, active voice, preserved \\textbf formatting, "
                "proof-point specifics, and no filler phrases."
            ),
            n_generators=2,
            temperature=0.3,
        )
    except RuntimeError as e:
        logger.error(f"[tailor] Council failed: {e}")
        return {"error": str(e), "job_hash": job_hash}
```

- [ ] **Step 2: Wire quality validation into the validation gate**

After the existing structural validation (brace balance, macro arity, sections, headers, word count), add the new quality checks. These are WARNINGS that trigger retry, not hard fallbacks.

In the handler, after `validation_errors = [...]` block, add:

```python
    # Quality validation (writing quality, not just syntax)
    quality_warnings = []
    quality_warnings.extend(_check_banned_phrases(body))
    quality_warnings.extend(_check_textbf_preservation(base_body, body))

    # Extract base skills for fabrication check
    base_skills_match = re.search(
        r"\\section\*\{(?:Technical )?Skills\}(.*?)\\section\*\{",
        base_body, re.DOTALL
    )
    if base_skills_match:
        quality_warnings.extend(_check_fabrication(base_skills_match.group(1), body))

    if quality_warnings and not validation_errors:
        logger.warning(f"[tailor] Quality warnings for {job_hash}: {'; '.join(quality_warnings[:5])}")
        # Retry once with explicit feedback about what to fix
        retry_prompt = (
            user_prompt
            + "\n\nYour previous attempt had these quality issues:\n"
            + "\n".join(f"- FIX: {w}" for w in quality_warnings)
            + "\nPlease fix ALL of them. Return ONLY the corrected body."
        )
        try:
            retry_dict = council_complete(
                prompt=retry_prompt, system=system_prompt,
                task_description=f"Retry tailor for '{title}' — fix quality issues.",
                n_generators=2, temperature=0.3,
            )
            retry_body = retry_dict.get("content", "").strip()
            # Re-validate structural + quality
            retry_quality = _check_banned_phrases(retry_body) + _check_textbf_preservation(base_body, retry_body)
            if len(retry_quality) < len(quality_warnings):
                logger.info(f"[tailor] Retry improved quality: {len(quality_warnings)} → {len(retry_quality)} warnings")
                body = retry_body
                response_dict = retry_dict
            else:
                logger.info("[tailor] Retry did not improve quality, keeping original")
        except RuntimeError:
            logger.warning("[tailor] Quality retry failed, keeping original")
```

- [ ] **Step 3: Run full test suite**

```bash
pytest tests/unit/ -v --tb=short -x
```

Expected: All existing + new tests PASS.

- [ ] **Step 4: Commit**

```bash
git add lambdas/pipeline/tailor_resume.py
git commit -m "feat: remove light-touch bypass — council runs for ALL tiers with quality retry"
```

---

## Task 5: Fix cover letter quality — career-ops proof-point mapping + opening check + always council + S3 upload

**Files:**
- Modify: `lambdas/pipeline/generate_cover_letter.py`

Career-ops approach: *"Cover letters: JD quotes mapped to proof points."* Each paragraph must connect a JD requirement to a specific resume achievement with a real metric. No generic "taught me the importance of" filler.

- [ ] **Step 0: Rewrite COVER_LETTER_SYSTEM_PROMPT with proof-point mapping**

Replace the existing paragraph structure guidance with career-ops proof-point methodology:

```python
# Replace Paragraph 2 instruction in COVER_LETTER_SYSTEM_PROMPT:

Paragraph 2 (6-8 sentences): This is the meat. Map TWO specific JD requirements to YOUR achievements:
- Quote or paraphrase a JD requirement, then connect it to a specific metric from your resume.
- Example pattern: "Your team [JD requirement]. At Clover, I [specific achievement with metric]."
- Example: "Your team ships observability tooling at scale. At Clover, I built monitoring dashboards across 8 microservices that reduced MTTR by 35%."
- Example: "The role requires experience with cloud-native architectures. I designed and maintained a multi-region AWS SaaS platform serving 99.9% uptime."
- Use ONLY metrics and achievements that exist in the resume. Do NOT invent numbers.
- Do NOT list technologies. Show impact through specific stories.

Paragraph 3 (3-4 sentences): Mention ONE more relevant project by name (e.g., Purrrfect Keys, WhatsTheCraic) with a specific result. Say you are available and based in Dublin. End with a confident, forward-looking sentence. No begging, no "I look forward to."
```

- [ ] **Step 1: Add `_check_opening_quality()` function**

```python
def _check_opening_quality(text: str, company: str) -> list[str]:
    """Check that the cover letter doesn't open by describing the company."""
    errors = []
    first_sentence = text.split(".")[0].lower() if text else ""

    # Pattern: "[Company] is a company that..." or "[Company] specializes in..."
    company_lower = company.lower()
    bad_patterns = [
        f"{company_lower} is a",
        f"{company_lower} specializes",
        f"{company_lower} is an",
        f"{company_lower} provides",
        f"{company_lower} offers",
        f"a company that",
        "i want to work as",
        "i am writing to",
        "this project taught me",
    ]
    for pattern in bad_patterns:
        if pattern in first_sentence:
            errors.append(f"bad_opening: first sentence matches '{pattern}' — describe what the company DOES that interests you, not what the company IS")

    return errors
```

- [ ] **Step 2: Remove light-touch bypass from cover letter handler**

Replace the `if light_touch:` branch. Same as resume — always council.

```python
    # ALWAYS use council for cover letters
    try:
        result = council_complete(
            prompt=prompt,
            system=COVER_LETTER_SYSTEM_PROMPT,
            task_description=f"Write cover letter for {job['title']} at {job['company']}. Must open with something specific about the company, not a generic description.",
            n_generators=2,
            temperature=0.7,
        )
    except RuntimeError:
        result = ai_complete(prompt, system=COVER_LETTER_SYSTEM_PROMPT, temperature=0.7)
```

- [ ] **Step 3: Add opening quality check to validation**

In the `_validate_cover_letter()` function, add after existing checks:

```python
    # Check opening quality
    opening_errors = _check_opening_quality(text, company=company)
    errors.extend(opening_errors)
```

Update `_validate_cover_letter` signature to accept `company` parameter.

- [ ] **Step 4: Fix cover letter S3 upload path**

Ensure cover letters are uploaded to S3 (the audit found 0 CL PDFs). Check the `handler()` to ensure `cover_letter_s3_key` is set and passed to `save_job.py`.

- [ ] **Step 5: Run cover letter tests**

```bash
pytest tests/unit/test_quality_validation.py::TestCoverLetterQuality -v --tb=short -x
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add lambdas/pipeline/generate_cover_letter.py tests/unit/test_quality_validation.py
git commit -m "feat: cover letter quality — always council, opening check, fix S3 upload"
```

---

## Task 6: Port score-and-improve loop to `post_score.py`

**Files:**
- Modify: `lambdas/pipeline/post_score.py` (rewrite from 61 → ~150 lines)
- Modify: `template.yaml` (increase PostScore timeout, add re-tailor trigger)

The current `post_score.py` is analytics-only. Port the `resume_scorer.py` quality gate:
- Score the tailored resume (3 perspectives: ATS, HM, TR)
- If any score < 85 AND round < max_rounds, trigger improvement
- Improvement = re-tailor with score feedback appended to prompt

- [ ] **Step 1: Add scoring prompt constants to post_score.py**

Port the `SCORER_SYSTEM_PROMPT` from `resume_scorer.py:20-125`.

- [ ] **Step 2: Add `_score_tailored()` function**

```python
def _score_tailored(tex_content: str, job_description: str) -> dict:
    """Score tailored resume against JD from 3 perspectives.

    Returns dict with ats_score, hiring_manager_score, tech_recruiter_score,
    improvements (list), and fabrication_detected (bool).
    """
    from lambdas.pipeline.ai_helper import council_complete
    import json

    prompt = f"""Score this tailored resume against the job description.

JOB DESCRIPTION:
{job_description[:3000]}

TAILORED RESUME:
{tex_content[:5000]}

Return a JSON object:
{{
  "ats_score": <0-100>,
  "hiring_manager_score": <0-100>,
  "tech_recruiter_score": <0-100>,
  "fabrication_detected": <true/false>,
  "improvements": ["suggestion 1", "suggestion 2", ...]
}}"""

    result = council_complete(
        prompt=prompt,
        system=SCORER_SYSTEM_PROMPT,
        task_description="Score this resume accurately. No inflated scores.",
        n_generators=2,
        temperature=0.2,
    )

    try:
        text = result["content"].strip()
        if "```" in text:
            text = text.split("```")[1]
            if text.startswith("json"):
                text = text[4:]
        scores = json.loads(text.strip())
        return _validate_scores(scores)
    except (json.JSONDecodeError, KeyError) as e:
        logger.warning(f"[post_score] Could not parse scores: {e}")
        return {"ats_score": 0, "hiring_manager_score": 0, "tech_recruiter_score": 0,
                "fabrication_detected": False, "improvements": []}
```

- [ ] **Step 3: Add improvement trigger to handler**

```python
def handler(event, context):
    """Score tailored resume. If scores < 85, return needs_improvement=True for retry."""
    # ... existing code to fetch resume from S3 ...

    scores = _score_tailored(tex_content, job_description)

    ats = scores.get("ats_score", 0)
    hm = scores.get("hiring_manager_score", 0)
    tr = scores.get("tech_recruiter_score", 0)
    min_score = min(ats, hm, tr)

    # Save scores to DB regardless
    db.table("jobs").update({
        "tailored_ats_score": ats,
        "tailored_hm_score": hm,
        "tailored_tr_score": tr,
        "writing_quality_score": scores.get("writing_quality", None),
    }).eq("user_id", user_id).eq("job_hash", job_hash).execute()

    # Quality gate: flag for re-tailor if below threshold
    needs_improvement = min_score < 85 and not scores.get("fabrication_detected")
    improvement_round = event.get("improvement_round", 0)

    return {
        "job_hash": job_hash,
        "scored": True,
        "scores": {"ats": ats, "hm": hm, "tr": tr},
        "needs_improvement": needs_improvement and improvement_round < 2,
        "improvement_round": improvement_round,
        "improvements": scores.get("improvements", []),
    }
```

- [ ] **Step 4: Update template.yaml — add improvement loop in state machine**

In the `PostScoreTailoredJobs` Map state's iterator, add a Choice state after PostScoreJob:

```json
"CheckNeedsImprovement": {
  "Type": "Choice",
  "Choices": [
    {
      "Variable": "$.needs_improvement",
      "BooleanEquals": true,
      "Next": "RetailorAndRescore"
    }
  ],
  "Default": "PostScoreDone"
},
"RetailorAndRescore": {
  "Type": "Task",
  "Resource": "${TailorResumeFunction.Arn}",
  "Parameters": {
    "user_id.$": "$.user_id",
    "job_hash.$": "$.job_hash",
    "tailoring_depth": "moderate",
    "improvement_feedback.$": "$.improvements"
  },
  "TimeoutSeconds": 300,
  "Next": "RecompileAfterImprovement",
  "Catch": [{"ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "PostScoreDone"}]
},
"RecompileAfterImprovement": {
  "Type": "Task",
  "Resource": "${CompileLatexFunction.Arn}",
  "TimeoutSeconds": 60,
  "Next": "ResaveAfterImprovement",
  "Catch": [{"ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "PostScoreDone"}]
},
"ResaveAfterImprovement": {
  "Type": "Task",
  "Resource": "${SaveJobFunction.Arn}",
  "TimeoutSeconds": 60,
  "Next": "PostScoreDone",
  "Catch": [{"ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "PostScoreDone"}]
},
"PostScoreDone": {
  "Type": "Succeed"
}
```

- [ ] **Step 5: Increase PostScore Lambda timeout**

In `template.yaml`, change PostScoreFunction timeout from 60s to 300s (scoring + improvement can take time).

- [ ] **Step 6: Run tests**

```bash
pytest tests/unit/ tests/contract/ -v --tb=short -x
```

- [ ] **Step 7: Commit**

```bash
git add lambdas/pipeline/post_score.py template.yaml
git commit -m "feat: port score-and-improve loop to Lambda — quality gate, not just analytics"
```

---

## Task 7: Add catch-up step for missing S+A resumes

**Files:**
- Modify: `template.yaml` (add CatchUpMissing state after PostScoreTailoredJobs)
- Create: `lambdas/pipeline/catchup_missing.py`

- [ ] **Step 1: Create catchup Lambda**

```python
# lambdas/pipeline/catchup_missing.py
"""Find S+A tier jobs missing resumes and return them for processing."""
import logging

logger = logging.getLogger()


def handler(event, context):
    """Query for active S/A tier jobs with resume_s3_url IS NULL.

    Returns a list of job items formatted for ProcessMatchedJobs Map state.
    Limited to 10 per run to avoid timeout.
    """
    from lambdas.pipeline.ai_helper import get_supabase, get_param

    db = get_supabase()
    user_id = event.get("user_id", get_param("/naukribaba/DEFAULT_USER_ID"))

    # Find S+A tier jobs missing resumes
    result = (
        db.table("jobs")
        .select("job_hash, match_score, score_tier, title, company")
        .eq("user_id", user_id)
        .eq("expired", False)
        .in_("score_tier", ["S", "A"])
        .is_("resume_s3_url", "null")
        .order("match_score", desc=True)
        .limit(10)
        .execute()
    )

    items = []
    for job in result.data:
        items.append({
            "user_id": user_id,
            "job_hash": job["job_hash"],
            "match_score": job["match_score"],
            "score_tier": job["score_tier"],
            "tailoring_depth": "light" if job["match_score"] >= 85 else "moderate",
            "skip_cover_letter": False,
            "skip_contacts": False,
        })

    logger.info(f"[catchup] Found {len(items)} S+A jobs missing resumes")
    return {"catchup_items": items, "catchup_count": len(items)}
```

- [ ] **Step 2: Add CatchUpMissing to state machine in template.yaml**

After `PostScoreTailoredJobs`, before `SavePipelineMetrics`:

```json
"CatchUpMissing": {
  "Type": "Task",
  "Resource": "${CatchUpMissingFunction.Arn}",
  "TimeoutSeconds": 30,
  "Next": "ProcessCatchUpJobs",
  "Catch": [{"ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "SavePipelineMetrics"}]
},
"ProcessCatchUpJobs": {
  "Type": "Map",
  "ItemsPath": "$.catchup_items",
  "MaxConcurrency": 4,
  "Next": "SavePipelineMetrics",
  "Catch": [{"ErrorEquals": ["States.ALL"], "ResultPath": "$.error", "Next": "SavePipelineMetrics"}],
  "Iterator": {
    "Comment": "Same flow as ProcessMatchedJobs — tailor, compile, CL, compile CL, save",
    "StartAt": "CatchUpTailor",
    "States": { /* same states as ProcessMatchedJobs iterator */ }
  }
}
```

- [ ] **Step 3: Add Lambda function resource in template.yaml**

- [ ] **Step 4: Commit**

```bash
git add lambdas/pipeline/catchup_missing.py template.yaml
git commit -m "feat: add catch-up step — daily pipeline fills missing S+A resumes"
```

---

## Task 8: Fix contact personalization

**Files:**
- Modify: `lambdas/pipeline/find_contacts.py`

- [ ] **Step 1: Replace hardcoded roles with AI role selection**

Port `_get_search_roles()` from `contact_finder.py` — AI suggests 3 company-specific roles based on job title and company.

- [ ] **Step 2: Replace hardcoded message with AI-generated messages**

Port the personalized message generation from `contact_finder.py`. Each contact gets a message specific to the role and company, not "cloud infrastructure and backend engineering" for everyone.

- [ ] **Step 3: Add Serper.dev fallback**

If Apify fails for a role, try Serper.dev (2,500 free queries/month). If Serper fails, return a Google search URL for manual lookup.

- [ ] **Step 4: Commit**

```bash
git add lambdas/pipeline/find_contacts.py
git commit -m "feat: personalized contacts — AI role selection, AI messages, Serper fallback"
```

---

## Task 9: Integration test — end-to-end quality check

**Files:**
- Create: `tests/quality/test_output_quality.py`

- [ ] **Step 1: Write E2E quality test**

This test invokes the local tailoring path (not Lambda) and checks the output quality against the validation functions we built.

```python
# tests/quality/test_output_quality.py
"""Integration test: verify AI output quality meets standards."""
import pytest


@pytest.mark.quality
class TestResumeOutputQuality:
    """Verify generated resumes pass quality validation."""

    def test_no_banned_phrases_in_tailored_resume(self, sample_tailored_tex):
        from lambdas.pipeline.tailor_resume import _check_banned_phrases
        errors = _check_banned_phrases(sample_tailored_tex)
        assert errors == [], f"Banned phrases found: {errors}"

    def test_textbf_preserved(self, sample_base_body, sample_tailored_tex):
        from lambdas.pipeline.tailor_resume import _check_textbf_preservation
        errors = _check_textbf_preservation(sample_base_body, sample_tailored_tex)
        assert errors == [], f"Bold formatting lost: {errors}"

    def test_no_fabrication(self, sample_base_skills, sample_tailored_tex):
        from lambdas.pipeline.tailor_resume import _check_fabrication
        errors = _check_fabrication(sample_base_skills, sample_tailored_tex)
        assert errors == [], f"Fabricated skills: {errors}"


@pytest.mark.quality
class TestCoverLetterOutputQuality:
    """Verify generated cover letters pass quality validation."""

    def test_no_generic_opening(self, sample_cover_letter, sample_company):
        from lambdas.pipeline.generate_cover_letter import _check_opening_quality
        errors = _check_opening_quality(sample_cover_letter, company=sample_company)
        assert errors == [], f"Bad opening: {errors}"
```

- [ ] **Step 2: Update CI to block on quality tests**

In `.github/workflows/test.yml`, change the writing-quality-tests job to be blocking (remove `|| true` if present).

- [ ] **Step 3: Commit**

```bash
git add tests/quality/test_output_quality.py .github/workflows/test.yml
git commit -m "test: add output quality integration tests — block PRs on quality failures"
```

---

## Task 10: Deploy and single-company E2E validation (BEFORE any batch)

**CRITICAL: Do NOT batch-generate anything until Step 6 passes visual inspection.**

- [ ] **Step 1: Run full test suite locally**

```bash
source .venv/bin/activate
pytest tests/unit/ tests/contract/ tests/quality/ -v --tb=short 2>&1 | tail -20
```

Expected: All PASS.

- [ ] **Step 2: Build and deploy to Lambda**

```bash
sam build && sam deploy
```

- [ ] **Step 3: Pick ONE S-tier job for E2E test**

```bash
python3 -c "
from dotenv import load_dotenv; load_dotenv()
import os
from supabase import create_client
client = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
jobs = client.table('jobs').select('job_hash, title, company, match_score').eq('score_tier', 'S').eq('expired', False).order('match_score', desc=True).limit(3).execute()
for j in jobs.data:
    print(f\"{j['job_hash'][:12]} | {j['match_score']} | {j['company']} — {j['title']}\")
"
```

Pick the top job. Note the job_hash.

- [ ] **Step 4: Trigger single-job pipeline via Lambda API**

```bash
JOB_HASH="<paste hash here>"
curl -X POST "https://<api-url>/api/pipeline/re-tailor" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d "{\"tier\": \"SA\", \"max_jobs\": 1}"
```

Or invoke the Step Function directly:
```bash
aws stepfunctions start-execution \
  --state-machine-arn "$(aws stepfunctions list-state-machines --region eu-west-1 --query 'stateMachines[?contains(name,`single-job`)].stateMachineArn' --output text)" \
  --input "{\"user_id\": \"default\", \"job_hash\": \"$JOB_HASH\", \"skip_scoring\": true}" \
  --region eu-west-1
```

Wait for execution to complete (check Step Functions console or poll status).

- [ ] **Step 5: Download and inspect ALL artifacts**

```bash
# Resume PDF
aws s3 cp "s3://utkarsh-job-hunt/users/7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39/resumes/${JOB_HASH}_tailored.pdf" /tmp/test_resume.pdf
open /tmp/test_resume.pdf

# Resume TEX (check source)
aws s3 cp "s3://utkarsh-job-hunt/users/7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39/resumes/${JOB_HASH}_tailored.tex" /tmp/test_resume.tex

# Cover letter PDF
aws s3 cp "s3://utkarsh-job-hunt/users/7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39/cover_letters/${JOB_HASH}_cl.pdf" /tmp/test_cl.pdf
open /tmp/test_cl.pdf

# Check contacts in DB
python3 -c "
from dotenv import load_dotenv; load_dotenv()
import os, json
from supabase import create_client
client = create_client(os.environ['SUPABASE_URL'], os.environ['SUPABASE_SERVICE_KEY'])
job = client.table('jobs').select('title, company, linkedin_contacts, resume_s3_url, cover_letter_s3_url, tailored_ats_score, tailored_hm_score, tailored_tr_score, tailoring_model').eq('job_hash', '$JOB_HASH').execute()
if job.data:
    j = job.data[0]
    print(f\"Company: {j['company']} — {j['title']}\")
    print(f\"Resume: {j['resume_s3_url']}\")
    print(f\"Cover Letter: {j['cover_letter_s3_url']}\")
    print(f\"Scores: ATS={j.get('tailored_ats_score')}, HM={j.get('tailored_hm_score')}, TR={j.get('tailored_tr_score')}\")
    print(f\"Model: {j.get('tailoring_model')}\")
    contacts = j.get('linkedin_contacts')
    if contacts:
        for c in (json.loads(contacts) if isinstance(contacts, str) else contacts):
            print(f\"  Contact: {c.get('name')} — {c.get('message','')[:80]}\")
"
```

- [ ] **Step 6: VISUAL QUALITY CHECKLIST (must ALL pass before batch)**

Open the PDF files and check:

**Resume:**
- [ ] `\jobentry` renders correctly (company name, location, dates, title — NOT #1 #2 #3 #4)
- [ ] `\textbf{}` formatting is visible (bold keywords, not plain text wall)
- [ ] Summary mentions the SPECIFIC role title from the JD
- [ ] Summary contains at least 1 real metric from base resume
- [ ] No banned filler phrases ("highly motivated", "passionate about", etc.)
- [ ] Skills section contains ONLY skills from base resume (no fabrication)
- [ ] JD keywords are woven into existing bullets (reformulated, not invented)
- [ ] Exactly 2 pages
- [ ] 3 projects shown (Purrrfect Keys always + 2 relevant)

**Cover Letter:**
- [ ] First sentence is NOT a description of the company ("X is a company that...")
- [ ] Each paragraph maps a JD requirement to a specific resume achievement
- [ ] Contains at least 1 real metric
- [ ] No banned phrases
- [ ] 280-380 words
- [ ] No dashes

**Contacts:**
- [ ] Messages are specific to the job (not generic "cloud infrastructure")
- [ ] Role titles are company-appropriate

**If ANY check fails:** Fix the issue, redeploy, re-test THIS SAME JOB. Do not proceed to batch.

- [ ] **Step 7: Batch-generate remaining S+A tier artifacts**

ONLY after Step 6 passes:

```bash
curl -X POST "https://<api-url>/api/pipeline/re-tailor" \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer <token>" \
  -d "{\"tier\": \"SA\", \"max_jobs\": 20}"
```

Monitor progress in Step Functions console. Repeat with max_jobs=20 until all S+A jobs have artifacts.

- [ ] **Step 8: Spot-check 3 random batch results**

Download 3 random PDFs from the batch and repeat Step 6 checklist. If quality is consistent, the pipeline is healthy.

- [ ] **Step 9: Create PR**

```bash
gh pr create --title "fix: pipeline quality parity — real council, career-ops patterns, quality gates" --body "$(cat <<'EOF'
## Summary
- Port real AI council to Lambda (diverse generators, numeric critic scoring, model-family dedup)
- Remove light-touch bypass — council runs for ALL tiers
- Add career-ops keyword injection (reformulate, don't fabricate) + archetype framing
- Add quality validation: banned phrases, textbf preservation, fabrication check
- Port score-and-improve loop (quality gate, not just analytics)
- Fix cover letter pipeline (proof-point mapping, S3 upload)
- Fix contacts (AI role selection, personalized messages)
- Add catch-up step for missing S+A resumes
- Single-company E2E validated before batch

## Test plan
- [ ] Unit tests pass (council diversity, quality validation)
- [ ] Single S-tier job produced correct PDF via Lambda
- [ ] Cover letter PDF exists in S3 with proof-point content
- [ ] Contact messages are job-specific
- [ ] 3 random batch results pass visual checklist
EOF
)"
```

- [ ] **Step 10: Merge after confirming batch quality**

Only merge after Step 8 passes. Then the daily pipeline (7:00 UTC weekdays) will use the new quality system for all future jobs.
