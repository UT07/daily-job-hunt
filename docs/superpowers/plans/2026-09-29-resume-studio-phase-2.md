# Resume Studio Phase 2 Implementation Plan — reliable scoring

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the Studio's score stop lying — recompute it from three independent uncached calls whenever the resume is recompiled, and show the honest spread as a band.

**Architecture:** The score rides back with the compile. `_do_rebuild_sections` already holds the rebuilt `.tex`, and the POST endpoint already selects the job's `description`, so scoring happens inside the existing task and returns in the same result the Studio already awaits. No new endpoint, no second poll, no extra trigger.

**Tech Stack:** FastAPI + the existing SQS-backed task dispatch, `lambdas/pipeline/score_batch.py`, React.

**Spec:** `docs/superpowers/specs/2026-09-28-resume-studio-design.md` §5.2

**Builds on:** `docs/superpowers/plans/2026-09-28-resume-studio-phase-1.md` (merged as #115)

## Global Constraints

- **The batch pipeline must not change behaviour.** It scores ~58 jobs a run against a Groq TPM ceiling that is already the bottleneck; 3× would triple that load. `num_calls` stays defaulted to 1 and the cache stays on for every existing caller.
- **Band semantics change, deliberately.** Phase 1's band was min/max across the three *perspectives* (ATS/HM/TR) — that measures whether the lenses disagree. Phase 2's band is min/max across three *repeat calls* — that measures whether the model disagrees with itself. Only the second is the reliability claim the spec makes.
- Measured 2026-09-28: the same job and resume scored **25 to 80 across 30 verified models**, and one model at `temperature=0` returned three different answers to three identical consecutive calls. Temperature controls sampling, not MoE routing or request batching.
- Run backend tests with `env -i PATH="$PATH" HOME="$HOME" .venv/bin/python -m pytest tests/unit tests/contract tests/integration -q`. Frontend: `cd web && npm test`.
- Repo-wide eslint baseline is 21 pre-existing problems; do not add to it.

### Two things in the current code that are worse than the spec says

1. `ai_complete_cached` has **no `skip_cache` parameter at all** (`lambdas/pipeline/ai_helper.py`). `score_single_job_deterministic`'s docstring tells callers to "use `skip_cache`", naming an argument that does not exist. Task 1 adds it and corrects the docstring.
2. `score_single_job_deterministic` returns only medians. The spread is computed and thrown away, so there is nothing to build a band from. Task 2 returns it.

---

## File Structure

| File | Change |
|---|---|
| `lambdas/pipeline/ai_helper.py` | add `skip_cache` to `ai_complete_cached` |
| `lambdas/pipeline/score_batch.py` | thread `skip_cache` through `score_single_job`; return the spread from `score_single_job_deterministic`; fix the docstring |
| `app.py` | `_do_rebuild_sections` scores the rebuilt tex and returns `scores`; the POST passes `description` into the payload |
| `web/src/pages/ResumeStudio.jsx` | hold scores returned by the compile; prefer them over the stored row |
| `web/src/components/studio/ScoreStrip.jsx` | accept an explicit band |
| tests | one file per change |

---

## Task 1: `skip_cache` on the cached AI call

**Files:**
- Modify: `lambdas/pipeline/ai_helper.py` (`ai_complete_cached`)
- Test: `tests/unit/test_ai_cache_bypass.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `ai_complete_cached(prompt, system="", cache_hours=72, temperature=0.3, max_tokens=4096, skip_cache=False)`. With `skip_cache=True` it neither reads nor writes the cache.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_ai_cache_bypass.py
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


def _db_with_cached(response="CACHED"):
    db = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.gte.return_value = chain
    chain.execute.return_value = MagicMock(
        data=[{"response": response, "provider": "cache", "model": "cache"}]
    )
    db.table.return_value = chain
    return db


def test_skip_cache_ignores_a_warm_entry():
    db = _db_with_cached()
    live = {"content": "FRESH", "provider": "groq", "model": "m"}
    with patch.object(ai_helper, "get_supabase", return_value=db), \
         patch.object(ai_helper, "ai_complete", return_value=live) as call:
        out = ai_helper.ai_complete_cached("p", skip_cache=True)
    assert out["content"] == "FRESH", "a warm cache entry was returned despite skip_cache"
    call.assert_called_once()


def test_skip_cache_does_not_write_back():
    """Three bypassed calls must not race to poison the shared key.

    Writing back would mean call 1 populates the cache and calls 2 and 3 read
    it — reintroducing exactly the collapse skip_cache exists to prevent, and
    leaving the last write visible to the batch pipeline afterwards.
    """
    db = MagicMock()
    chain = MagicMock()
    chain.select.return_value = chain
    chain.eq.return_value = chain
    chain.gte.return_value = chain
    chain.execute.return_value = MagicMock(data=[])
    db.table.return_value = chain
    with patch.object(ai_helper, "get_supabase", return_value=db), \
         patch.object(ai_helper, "ai_complete", return_value={"content": "X", "provider": "p", "model": "m"}):
        ai_helper.ai_complete_cached("p", skip_cache=True)
    chain.upsert.assert_not_called()


def test_default_still_reads_and_writes_the_cache():
    """The batch pipeline depends on this. It scores ~58 jobs a run against an
    8k tokens/minute Groq ceiling; losing the cache would be a real regression."""
    db = _db_with_cached()
    with patch.object(ai_helper, "get_supabase", return_value=db), \
         patch.object(ai_helper, "ai_complete") as call:
        out = ai_helper.ai_complete_cached("p")
    assert out["content"] == "CACHED"
    call.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -i PATH="$PATH" HOME="$HOME" .venv/bin/python -m pytest tests/unit/test_ai_cache_bypass.py -q`
Expected: FAIL — `TypeError: ai_complete_cached() got an unexpected keyword argument 'skip_cache'`

- [ ] **Step 3: Write minimal implementation**

In `lambdas/pipeline/ai_helper.py`, change the signature and guard both cache touches:

```python
def ai_complete_cached(
    prompt: str,
    system: str = "",
    cache_hours: int = 72,
    temperature: float = 0.3,
    max_tokens: int = 4096,
    skip_cache: bool = False,
) -> dict:
    """AI complete with Supabase cache. Returns dict with content, provider, model.

    skip_cache=True neither reads nor writes the cache. It exists for repeat
    sampling: score_single_job_deterministic takes the median of num_calls
    independent calls, and a cache hit makes those calls identical, so the
    median of three is the median of one answer returned three times. Writing
    back is skipped too — otherwise call 1 would populate the key that calls 2
    and 3 then read, rebuilding the collapse this avoids, and leaving the last
    sample visible to the batch pipeline afterwards.
    """
    cache_key = hashlib.md5(f"{system}|{prompt}".encode()).hexdigest()
    db = get_supabase()

    if not skip_cache:
        cached = db.table("ai_cache").select("response, provider, model") \
            .eq("cache_key", cache_key) \
            .gte("expires_at", datetime.utcnow().isoformat()).execute()
        if cached.data:
            return {
                "content": cached.data[0]["response"],
                "provider": cached.data[0].get("provider", "cache"),
                "model": cached.data[0].get("model", "cache"),
            }

    result = ai_complete(prompt, system, temperature=temperature, max_tokens=max_tokens)

    if not skip_cache:
        db.table("ai_cache").upsert({
            "cache_key": cache_key,
            "response": result["content"],
            "provider": result["provider"],
            "model": result["model"],
            "expires_at": (datetime.utcnow() + timedelta(hours=cache_hours)).isoformat(),
        }, on_conflict="cache_key").execute()

    return result
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -i PATH="$PATH" HOME="$HOME" .venv/bin/python -m pytest tests/unit/test_ai_cache_bypass.py -q`
Expected: PASS, 3 tests.

- [ ] **Step 5: Commit**

```bash
git add lambdas/pipeline/ai_helper.py tests/unit/test_ai_cache_bypass.py
git commit -m "feat(ai): skip_cache on ai_complete_cached, the argument the docs already promised"
```

---

## Task 2: return the spread, not just the median

**Files:**
- Modify: `lambdas/pipeline/score_batch.py` (`score_single_job`, `score_single_job_deterministic`)
- Test: `tests/unit/test_score_spread.py`

**Interfaces:**
- Consumes: `skip_cache` from Task 1.
- Produces:
  - `score_single_job(job, resume_tex, temperature=0, skip_cache=False)`
  - `score_single_job_deterministic(job, resume_tex, num_calls=1, skip_cache=False)` — the returned dict gains `score_spread`:
    ```python
    {"ats": [lo, hi], "hiring_manager": [lo, hi],
     "tech_recruiter": [lo, hi], "match": [lo, hi], "n": <calls that succeeded>}
    ```

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_score_spread.py
"""A band needs the spread, and the spread was being thrown away.

score_single_job_deterministic took the median of each perspective across
num_calls and discarded everything else. The Studio needs min/max to show
"84-88" rather than "86" — a single integer claims a precision the measurement
does not have. Measured 2026-09-28: one model, one identical prompt,
temperature=0, three consecutive calls, three different answers.
"""
import sys
from unittest.mock import patch

sys.path.insert(0, "lambdas/pipeline")
import score_batch  # noqa: E402

JOB = {"job_hash": "h1", "title": "SRE", "company": "Acme",
       "description": "Kubernetes and Terraform.", "location": "Dublin"}
RESUME = r"\documentclass{article}\begin{document}Jane\end{document}"


def _result(ats, hm, tr):
    return {"ats_score": ats, "hiring_manager_score": hm, "tech_recruiter_score": tr,
            "match_score": round((ats + hm + tr) / 3), "reasoning": "r",
            "key_matches": [], "gaps": [], "provider": "p", "model": "m"}


def test_spread_reports_min_and_max_per_perspective():
    calls = [_result(84, 80, 90), _result(88, 82, 86), _result(86, 81, 88)]
    with patch.object(score_batch, "score_single_job", side_effect=calls):
        out = score_batch.score_single_job_deterministic(JOB, RESUME, num_calls=3)
    assert out["ats_score"] == 86                      # median of 84/88/86
    assert out["score_spread"]["ats"] == [84, 88]
    assert out["score_spread"]["hiring_manager"] == [80, 82]
    assert out["score_spread"]["n"] == 3


def test_agreeing_calls_collapse_the_band_to_a_point():
    calls = [_result(85, 85, 85)] * 3
    with patch.object(score_batch, "score_single_job", side_effect=calls):
        out = score_batch.score_single_job_deterministic(JOB, RESUME, num_calls=3)
    assert out["score_spread"]["ats"] == [85, 85]


def test_a_single_call_reports_n_1_so_the_ui_can_say_so():
    """num_calls=1 gives no variance information at all. The band must not
    imply three calls agreed when only one was made."""
    with patch.object(score_batch, "score_single_job", side_effect=[_result(85, 85, 85)]):
        out = score_batch.score_single_job_deterministic(JOB, RESUME, num_calls=1)
    assert out["score_spread"]["n"] == 1


def test_partial_failure_reports_the_calls_that_landed():
    with patch.object(score_batch, "score_single_job",
                      side_effect=[_result(84, 80, 90), None, _result(88, 82, 86)]):
        out = score_batch.score_single_job_deterministic(JOB, RESUME, num_calls=3)
    assert out["score_spread"]["n"] == 2
    assert out["score_spread"]["ats"] == [84, 88]


def test_all_calls_failing_still_returns_none():
    with patch.object(score_batch, "score_single_job", side_effect=[None, None, None]):
        assert score_batch.score_single_job_deterministic(JOB, RESUME, num_calls=3) is None


def test_repeat_calls_bypass_the_cache():
    """Without this the three calls are one cached answer returned three times."""
    seen = []

    def fake(job, resume_tex, temperature=0, skip_cache=False):
        seen.append(skip_cache)
        return _result(85, 85, 85)

    with patch.object(score_batch, "score_single_job", side_effect=fake):
        score_batch.score_single_job_deterministic(JOB, RESUME, num_calls=3, skip_cache=True)
    assert seen == [True, True, True]


def test_batch_default_is_unchanged():
    """One call, cache on. The batch pipeline scores ~58 jobs against an 8k
    tokens/minute ceiling; 3x uncached would be a real regression."""
    seen = []

    def fake(job, resume_tex, temperature=0, skip_cache=False):
        seen.append(skip_cache)
        return _result(85, 85, 85)

    with patch.object(score_batch, "score_single_job", side_effect=fake):
        score_batch.score_single_job_deterministic(JOB, RESUME)
    assert seen == [False]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -i PATH="$PATH" HOME="$HOME" .venv/bin/python -m pytest tests/unit/test_score_spread.py -q`
Expected: FAIL — `KeyError: 'score_spread'` on the first four tests.

- [ ] **Step 3: Write minimal implementation**

In `score_single_job`, add the parameter and pass it through:

```python
def score_single_job(job: dict, resume_tex: str, temperature: float = 0,
                     skip_cache: bool = False) -> dict | None:
```
and at the `ai_complete_cached` call:
```python
        response_dict = ai_complete_cached(
            prompt, system=SCORE_SYSTEM_PROMPT, temperature=temperature,
            max_tokens=SCORE_MAX_TOKENS, skip_cache=skip_cache,
        )
```

Replace `score_single_job_deterministic` wholesale:

```python
def score_single_job_deterministic(
    job: dict, resume_tex: str, num_calls: int = 1, skip_cache: bool = False
) -> dict | None:
    """Score a job `num_calls` times at temperature=0; return medians plus the spread.

    The median dampens provider variance. The SPREAD is what makes the result
    honest: measured 2026-09-28, one model given one identical prompt at
    temperature=0 returned three different answers to three consecutive calls,
    because temperature controls sampling and not mixture-of-experts routing or
    request batching. Reporting a single integer claims a precision the
    measurement does not have.

    skip_cache must be True for num_calls > 1 to mean anything — otherwise every
    call after the first is the same cached response and the median of three is
    the median of one. The default (1 call, cache on) is what the batch pipeline
    uses and is deliberately unchanged.

    Returns None only when every call fails.
    """
    all_scores: list[dict] = []
    for _ in range(num_calls):
        result = score_single_job(job, resume_tex, temperature=0, skip_cache=skip_cache)
        if result is not None:
            all_scores.append(result)

    if not all_scores:
        return None

    def _vals(key):
        return [s[key] for s in all_scores if isinstance(s.get(key), (int, float))]

    fields = {
        "ats": "ats_score",
        "hiring_manager": "hiring_manager_score",
        "tech_recruiter": "tech_recruiter_score",
        "match": "match_score",
    }
    spread = {"n": len(all_scores)}
    for short, key in fields.items():
        vals = _vals(key)
        spread[short] = [min(vals), max(vals)] if vals else None

    # Non-numeric fields (reasoning, key_matches, gaps, provider, model) come
    # from the first result, so the dict keeps score_single_job's shape.
    merged = dict(all_scores[0])
    for short, key in fields.items():
        vals = _vals(key)
        if not vals:
            continue
        merged[key] = (round(statistics.median(vals), 1) if key == "match_score"
                       else int(statistics.median(vals)))
    merged["score_spread"] = spread
    return merged
```

- [ ] **Step 4: Run test to verify it passes**

Run: `env -i PATH="$PATH" HOME="$HOME" .venv/bin/python -m pytest tests/unit/test_score_spread.py -q`
Expected: PASS, 7 tests.

Then confirm nothing else regressed:
Run: `env -i PATH="$PATH" HOME="$HOME" .venv/bin/python -m pytest tests/unit tests/contract tests/integration -q`

- [ ] **Step 5: Commit**

```bash
git add lambdas/pipeline/score_batch.py tests/unit/test_score_spread.py
git commit -m "feat(scoring): return the spread, so a band can be honest about precision"
```

---

## Task 3: score the rebuilt resume inside the compile task

**Files:**
- Modify: `app.py` (`_do_rebuild_sections`, `update_job_sections`)
- Test: `tests/unit/test_rebuild_sections_scores.py`

**Interfaces:**
- Consumes: `score_single_job_deterministic(..., num_calls=3, skip_cache=True)` from Task 2.
- Produces: `_do_rebuild_sections` returns its existing keys **plus** `scores`:
  ```python
  {"ats_score": int, "hiring_manager_score": int, "tech_recruiter_score": int,
   "match_score": float, "score_spread": {...}}
  ```
  or `None` when scoring failed or there was no description. The Studio already
  awaits this result, so nothing new is polled.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/test_rebuild_sections_scores.py
"""The Studio's score must describe the resume on screen, not the one before it.

Phase 1 read scores off the jobs row, which describes the ORIGINAL tailored
resume. After an edit they are stale for the rest of the session. Scoring the
rebuilt .tex inside the compile task means the number arrives with the PDF, on
the same trigger, with no extra endpoint and no second poll.
"""
import sys
from unittest.mock import MagicMock, patch

sys.path.insert(0, ".")


SCORING_JOB = {"job_hash": "j1", "title": "SRE", "company": "Acme",
               "description": "Kubernetes and Terraform.", "location": "Dublin",
               "remote": None}


def _patched(score_return, scoring_job=SCORING_JOB):
    """Patch app._do_rebuild_sections' collaborators. Returns the result dict."""
    import app
    s3 = MagicMock()
    s3.get_object.return_value = {"Body": MagicMock(read=lambda: b"\\documentclass{article}\\begin{document}x\\end{document}")}
    with patch.object(app, "_get_s3", return_value=s3), \
         patch("lambdas.pipeline.parse_sections.rebuild_tex_from_sections", return_value="NEWTEX"), \
         patch.object(app, "compile_tex_to_pdf", return_value=None), \
         patch.object(app, "_job_for_scoring", return_value=scoring_job), \
         patch.object(app, "score_single_job_deterministic", return_value=score_return) as scorer:
        out = app._do_rebuild_sections("j1", {"summary": "s"}, "u1")
    return out, scorer


def test_scores_ride_back_with_the_compile():
    scored = {"ats_score": 86, "hiring_manager_score": 84, "tech_recruiter_score": 90,
              "match_score": 86.0, "score_spread": {"ats": [84, 88], "n": 3}}
    out, scorer = _patched(scored)
    assert out["scores"]["ats_score"] == 86
    assert out["scores"]["score_spread"]["n"] == 3


def test_it_asks_for_three_uncached_calls():
    """num_calls=1 or a warm cache would make the band meaningless."""
    _out, scorer = _patched({"ats_score": 1, "hiring_manager_score": 1,
                             "tech_recruiter_score": 1, "match_score": 1.0,
                             "score_spread": {"n": 3}})
    kwargs = scorer.call_args.kwargs
    assert kwargs["num_calls"] == 3
    assert kwargs["skip_cache"] is True


def test_a_failed_score_does_not_fail_the_compile():
    """The PDF is the deliverable. A missing score is a missing number, not a
    reason to throw away a successful rebuild."""
    out, _ = _patched(None)
    assert out["scores"] is None
    assert "pdf_url" in out


def test_no_description_means_no_score_attempt():
    out, scorer = _patched({"ats_score": 1}, scoring_job=None)
    assert out["scores"] is None
    scorer.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `env -i PATH="$PATH" HOME="$HOME" .venv/bin/python -m pytest tests/unit/test_rebuild_sections_scores.py -q`
Expected: FAIL — `AttributeError: <module 'app'> does not have the attribute '_job_for_scoring'`

- [ ] **Step 3: Write minimal implementation**

Add near `_do_rebuild_sections` in `app.py`:

```python
def _job_for_scoring(job_id: str, user_id: str) -> dict | None:
    """The job fields score_single_job needs, or None when unavailable.

    Returns the whole dict rather than just the description because title,
    company and location all go into the scoring prompt — passing empty strings
    would quietly produce a worse score than the batch pipeline gets for the
    same job. title and company are hard subscripts in score_single_job, so
    they must be present, not merely gettable.

    Its own function so the scoring step is testable without standing up the
    Supabase chain, and so a lookup failure degrades to "no score" rather than
    failing a compile that otherwise succeeded.
    """
    if _db is None:
        return None
    try:
        row = (
            _db.client.table("jobs")
            .select("description, title, company, location, remote")
            .eq("job_id", job_id).eq("user_id", user_id)
            .maybe_single().execute()
        )
        data = (row.data if row else None) or {}
        if not (data.get("description") or "").strip():
            return None          # nothing to score against
        return {
            "job_hash": job_id,
            "title": data.get("title") or "",
            "company": data.get("company") or "",
            "description": data["description"],
            "location": data.get("location") or "",
            "remote": data.get("remote"),
        }
    except Exception as e:
        logger.warning("[rebuild] could not load job %s for scoring: %s", job_id, e)
        return None
```

Add the import beside the other pipeline imports:

```python
from lambdas.pipeline.score_batch import score_single_job_deterministic
```

Then, immediately before `_do_rebuild_sections`'s `return {`:

```python
    # Score the rebuilt resume so the Studio's number describes the document
    # now on screen. Three independent uncached calls: the median dampens
    # provider variance and the spread is what makes the band honest. This is
    # the Studio path only — the batch pipeline still makes one cached call,
    # because it scores ~58 jobs a run against an 8k tokens/minute ceiling.
    #
    # A scoring failure must never fail the compile. The PDF is the deliverable;
    # a missing score is a missing number.
    scores = None
    scoring_job = _job_for_scoring(job_id, user_id)
    if scoring_job:
        try:
            scored = score_single_job_deterministic(
                scoring_job, new_tex, num_calls=3, skip_cache=True,
            )
            if scored:
                scores = {
                    "ats_score": scored.get("ats_score"),
                    "hiring_manager_score": scored.get("hiring_manager_score"),
                    "tech_recruiter_score": scored.get("tech_recruiter_score"),
                    "match_score": scored.get("match_score"),
                    "score_spread": scored.get("score_spread"),
                }
        except Exception as e:
            logger.warning("[rebuild] scoring failed for %s (compile still OK): %s", job_id, e)
```

and add `"scores": scores,` to the returned dict.

- [ ] **Step 4: Run test to verify it passes**

Run: `env -i PATH="$PATH" HOME="$HOME" .venv/bin/python -m pytest tests/unit/test_rebuild_sections_scores.py -q`
Expected: PASS, 4 tests.

Then the whole backend suite.

- [ ] **Step 5: Commit**

```bash
git add app.py tests/unit/test_rebuild_sections_scores.py
git commit -m "feat(studio): score the rebuilt resume inside the compile task"
```

---

## Task 4: show the measured band

**Files:**
- Modify: `web/src/components/studio/ScoreStrip.jsx`
- Modify: `web/src/pages/ResumeStudio.jsx`
- Modify: `web/src/components/studio/useHashedCompile.js` (surface the whole result, not only the URL)
- Test: `web/src/components/studio/__tests__/ScoreStrip.test.jsx`, `web/src/pages/__tests__/ResumeStudio.contract.test.jsx`

**Interfaces:**
- Consumes: `scores` on the compile result from Task 3.
- Produces: `<ScoreStrip ats hiringManager techRecruiter band={[lo, hi] | null} calls={n} stale />`. When `band` is given it is displayed instead of the perspective min/max, and the caption says how many calls it came from.

- [ ] **Step 1: Write the failing test**

Append to `web/src/components/studio/__tests__/ScoreStrip.test.jsx`:

```jsx
describe('a measured band beats the perspective range', () => {
  it('prefers the band from repeat calls when one is supplied', () => {
    // Phase 1's band was min/max across ATS/HM/TR — whether the three LENSES
    // disagree. This one is min/max across three repeat calls — whether the
    // MODEL disagrees with itself. Only the second is the reliability claim.
    render(<ScoreStrip ats={86} hiringManager={84} techRecruiter={90} band={[85, 87]} calls={3} />);
    expect(screen.getByTestId('score-band')).toHaveTextContent('85–87');
  });

  it('says how many calls produced the band', () => {
    render(<ScoreStrip ats={86} hiringManager={84} techRecruiter={90} band={[85, 87]} calls={3} />);
    expect(screen.getByTestId('score-strip')).toHaveTextContent(/3 calls/i);
  });

  it('does not claim agreement when only one call was made', () => {
    render(<ScoreStrip ats={86} hiringManager={84} techRecruiter={90} band={[86, 86]} calls={1} />);
    expect(screen.getByTestId('score-strip')).toHaveTextContent(/1 call/i);
    expect(screen.getByTestId('score-strip')).not.toHaveTextContent(/calls agree/i);
  });

  it('falls back to the perspective range when no band is supplied', () => {
    render(<ScoreStrip ats={86} hiringManager={84} techRecruiter={90} />);
    expect(screen.getByTestId('score-band')).toHaveTextContent('84–90');
  });
});
```

Append to `web/src/pages/__tests__/ResumeStudio.contract.test.jsx`, inside the existing `beforeEach`'s fetch stub, add `scores` to the task result:

```js
          scores: {
            ats_score: 90, hiring_manager_score: 88, tech_recruiter_score: 92,
            match_score: 90.0, score_spread: { ats: [88, 92], n: 3 },
          },
```

and add:

```jsx
describe('the score updates from the compile', () => {
  it('shows the freshly measured band and stops being stale', async () => {
    renderStudio();
    await waitFor(() => expect(screen.getByLabelText(/summary/i)).toBeInTheDocument());
    expect(screen.getByTestId('score-band')).toHaveTextContent('84–90');   // from the row

    const summary = screen.getByLabelText(/summary/i);
    fireEvent.change(summary, { target: { value: 'Edited summary.' } });
    fireEvent.blur(summary);

    await waitFor(
      () => expect(screen.getByTestId('score-band')).toHaveTextContent('88–92'),
      { timeout: 10000 },
    );
    // Freshly scored against the document on screen, so no longer stale.
    expect(screen.getByTestId('score-strip')).toHaveAttribute('data-stale', 'false');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/components/studio/__tests__/ScoreStrip.test.jsx src/pages/__tests__/ResumeStudio.contract.test.jsx`
Expected: FAIL — the band still shows `84–90` and the strip stays stale.

- [ ] **Step 3: Write minimal implementation**

`useHashedCompile.js` — `compileFn` may resolve an object; keep supporting a bare string:

```js
      .then((res) => {
        if (!mounted.current) return;
        if (hash !== currentHashRef.current) return;
        // compileFn may return a bare url (simple callers) or {pdfUrl, ...extra}.
        const url = typeof res === 'string' ? res : res?.pdfUrl;
        if (!url) { setError('Compile finished without a PDF'); return; }
        setPdfUrl(url);
        setResult(typeof res === 'string' ? null : res);
        setRenderedHash(hash);
      })
```
with `const [result, setResult] = useState(null);` added and `result` returned.

`ResumeStudio.jsx` — return the whole payload from `compileSections`:

```js
    return { pdfUrl: url, scores: result?.scores || null };
```
and destructure `result` from the hook, then:

```js
  const liveScores = result?.scores || null;
  const spread = liveScores?.score_spread;
```
feeding ScoreStrip:

```jsx
          <ScoreStrip
            ats={liveScores?.ats_score ?? job?.ats_score}
            hiringManager={liveScores?.hiring_manager_score ?? job?.hiring_manager_score}
            techRecruiter={liveScores?.tech_recruiter_score ?? job?.tech_recruiter_score}
            band={spread?.match || spread?.ats || null}
            calls={spread?.n ?? null}
            // A freshly measured score describes the document on screen, so it
            // is not stale even though the user has edited.
            stale={!liveScores && (hasEdited || pendingChanges > 0)}
          />
```

`ScoreStrip.jsx` — accept and prefer the band:

```jsx
export default function ScoreStrip({
  ats, hiringManager, techRecruiter, band = null, calls = null, stale = false,
}) {
```
and inside, when `band` is present use it for `lo`/`hi`, and render the caption:

```jsx
            {band && calls ? (
              <span className="text-[10px] text-stone-400">
                {calls} {calls === 1 ? 'call' : 'calls'}
              </span>
            ) : stale ? (
              <span className="text-[10px] text-stone-400">before your edits</span>
            ) : null}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npm test`
Expected: PASS, all files.

Then `cd web && npm run lint` — no new problems above the 21 baseline.

- [ ] **Step 5: Commit**

```bash
git add web/src
git commit -m "feat(studio): show the measured band, not the perspective range"
```

---

## Self-Review

**Spec coverage.** §5.2's two halves — `num_calls=3` (Task 3 passes it) and cache
bypass (Tasks 1–2) — are both covered, and the band is derived from the spread
(Tasks 2 and 4) rather than from the three perspectives. The "recompute on the
same trigger as the compile" requirement is satisfied structurally: the score is
computed *inside* the compile task, so it cannot drift from it.

**Deliberate deviation from the spec.** §5.2 describes greying the strip while a
re-score is outstanding. Here the score and the PDF arrive together, so there is
no window where one is fresh and the other is not — `stale` now means "no live
score yet", which is strictly more accurate than a timer.

**Cost.** Three scoring calls per compile, Studio only. The spec's fallback if
that bites is an explicit Re-score button, never dropping back to one call —
consistency is the requirement. The batch pipeline is untouched, asserted by
`test_batch_default_is_unchanged`.

**Type consistency.** `score_spread` keys (`ats`, `hiring_manager`,
`tech_recruiter`, `match`, `n`) are produced in Task 2 and consumed by the same
names in Tasks 3 and 4. `_do_rebuild_sections`' `scores` keys match the jobs-row
column names the frontend already reads.

**Risk closed before execution.** `score_single_job` reads exactly
`job['title']`, `job['company']` (hard subscripts — a KeyError if absent) and
`.get()` for `description`, `location`, `remote`, plus `job['job_hash']` in its
error paths. `_job_for_scoring` supplies all six. This was worth checking: a
KeyError would have been swallowed by the caught-and-logged path and degraded
silently to "no score", which looks identical to the feature not working. It is
also why `_job_for_scoring` returns the whole dict rather than just the
description — title, company and location all feed the prompt, so empty strings
would quietly yield a worse score than the batch pipeline gets for the same job.
