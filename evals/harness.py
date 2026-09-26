"""Golden-set runner.

Writes evals/report.json. The CI gate (scripts/check_eval_gate.py) reads
that file; nothing else parses stdout.

This corrects four wrong assumptions in the original plan (Task 24, Step 4;
see docs/superpowers/plans/2026-09-22-ey-genai-platform-upgrade.md and the
task report at
.superpowers/sdd/2026-09-22-ey-genai-platform-upgrade/task-23-25-report.md
for the full reasoning):

1. Score cases are scored against the user's REAL base resume
   (`_load_base_resume`, loaded once per run), never `resume_tex=""`. The
   golden set's expected tiers came from production `match_score`s computed
   against this same resume (scripts/build_evals_golden_set.py) — scoring
   against nothing would measure agreement with noise, not tier accuracy.
   Fails loudly if the resume can't be loaded, per the same instruction.

2. `score_single_job` calls `ai_complete_cached`, which caches purely on
   `md5(system|prompt)` with a 72h TTL and has no `skip_cache` knob. For a
   fixed case the prompt is identical on every repeat, so naive repeats
   would silently replay the SAME cached response `repeats` times and
   report `score_variance == 0` regardless of real model non-determinism —
   hiding exactly the defect (project backlog: "Score inconsistency") this
   metric exists to catch. When `repeats > 1` this module patches
   `score_batch.ai_complete_cached` to bypass the cache for the DURATION of
   that one case's repeats (see `_uncached_ai_complete`), so each repeat is
   a genuinely independent provider call — never by mutating rows in the
   shared production `ai_cache` Supabase table. At `repeats == 1` (the CI
   default) caching is left untouched: a re-run after a rate-limit stop
   doesn't re-pay for calls it already made.

3. Imports go through the qualified `lambdas.pipeline.*` path rather than
   the flat `from ai_helper import ...` / `from guardrails... import ...`
   spelling the plan drafted (which is also stale: guardrails/ moved under
   lambdas/pipeline/ before this task started). This module is never
   deployed — no CodeUri, no layer, no function imports it — so it has none
   of agents/guardrails/retrieval's flat-vs-qualified deploy constraint (see
   tests/unit/test_deploy_path_parity.py); it only ever runs under pytest or
   `python -m evals.harness`, both of which put the repo root on
   `sys.path`. `_ensure_lambda_paths()` below additionally puts
   lambdas/pipeline/ on `sys.path`, because score_batch.py itself contains
   an unqualified `from ai_helper import ...` that only resolves that way.

4. The plan's `_run_tailor_case` sent `council_complete` nothing but the JD
   ("Tailor a resume for this role:\n{description}") — no base resume in the
   PROMPT at all (base_skills/base_body were only ever wired to the GUARD
   check, not the generation call). That asks the model to invent a resume
   from nothing, which then fails check_output's required_sections and
   textbf_preservation guards regardless of model quality — confirmed by a
   real run: the first live attempt at this fixture came back with all five
   required sections missing and 0% \textbf preserved. `_run_tailor_case`
   now embeds the real base resume body in the prompt (mirroring, in
   simplified form, the real prompt tailor_resume.py sends — see
   `_TAILOR_SYSTEM_PROMPT`), so a guard failure means the model actually
   dropped structure, not that it was never given any to keep.

Cost discipline: `run_golden` checkpoints one JSON blob per case to
evals/.checkpoint.json as it goes, and a re-run skips any case already
checkpointed at the same `repeats` value — interrupting a run (Ctrl-C, a
rate limit) never loses completed work. If CONSECUTIVE_FAILURE_LIMIT cases
in a row fail outright (provider exhaustion, not just a bad score), the run
stops and reports how far it got instead of retry-looping through the rest
of the golden set against dead providers.
"""
import json
import os
import pathlib
import sys
import time
from contextlib import nullcontext
from unittest.mock import patch

HERE = pathlib.Path(__file__).parent
REPORT = HERE / "report.json"
CHECKPOINT = HERE / ".checkpoint.json"
REPO_ROOT = HERE.parent

DEFAULT_USER_ID = "7b28f6d3-46c9-4c46-a3a8-d5d7b3480e39"
CONSECUTIVE_FAILURE_LIMIT = 3


def _display_path(path: pathlib.Path) -> str:
    """`path` relative to the repo root for a friendly message, or the
    absolute path if it isn't under the repo root (e.g. a test pointed
    CHECKPOINT/REPORT at a tmp dir) — cosmetic only, never worth crashing
    the run over.
    """
    try:
        return str(path.relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def _load_dotenv() -> None:
    """Load .env into os.environ, same as scripts/rescore_batch.py and
    scripts/build_evals_golden_set.py — needed when this runs as
    `python -m evals.harness` (not under pytest, so tests/conftest.py's own
    .env loading never happens) and SUPABASE_URL / SUPABASE_SERVICE_KEY
    (db_client.py, for _load_base_resume) only live in .env locally. A
    no-op under pytest or in CI, where those are already real env vars
    (conftest.py already loaded .env; CI sets them from secrets) —
    setdefault() never overwrites an already-set value.
    """
    env_path = REPO_ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip().strip("\"'"))


def _ensure_lambda_paths() -> None:
    """Make `lambdas.pipeline.*` importable AND satisfy score_batch.py's own
    bare `from ai_helper import ...`. Idempotent — a no-op if tests/conftest.py
    (under pytest) already did this.
    """
    for p in (str(REPO_ROOT), str(REPO_ROOT / "lambdas" / "pipeline")):
        if p not in sys.path:
            sys.path.insert(0, p)
    os.environ.setdefault("AWS_DEFAULT_REGION", "eu-west-1")


_load_dotenv()
_ensure_lambda_paths()

from evals import load_golden  # noqa: E402
from evals.metrics import summarise  # noqa: E402
from lambdas.pipeline import score_batch  # noqa: E402
from lambdas.pipeline.ai_helper import ai_complete, council_complete  # noqa: E402
from lambdas.pipeline.guardrails.output_guards import check_output  # noqa: E402


def _split_tex(tex: str) -> tuple[str, str]:
    """(preamble, body) split. Mirrors lambdas/pipeline/tailor_resume.py's
    own `_split_tex` (not imported from there to avoid pulling this eval
    module's import graph through tailor_resume's boto3/retrieval imports
    for five lines of pure string logic) — keep in sync if that one changes.
    """
    begin_marker, end_marker = "\\begin{document}", "\\end{document}"
    begin_idx = tex.find(begin_marker)
    if begin_idx < 0:
        return "", tex
    preamble = tex[:begin_idx].rstrip()
    end_idx = tex.rfind(end_marker)
    if end_idx < 0 or end_idx <= begin_idx:
        body = tex[begin_idx + len(begin_marker):].strip()
    else:
        body = tex[begin_idx + len(begin_marker):end_idx].strip()
    return preamble, body


def _base_skills_text(base_body: str) -> str:
    """Same Skills-section extraction tailor_resume.py uses to build the
    fabrication-guard baseline, so check_output sees a realistic input
    instead of an empty (always-passing) one.
    """
    import re
    match = re.search(r"\\section\*\{(?:Technical )?Skills\}(.*?)\\section\*\{", base_body, re.DOTALL)
    return match.group(1) if match else ""


def _load_base_resume() -> str:
    """The user's real base resume `tex_content`, loaded once per run.

    Uses the direct-env Supabase client (db_client.py), same as every other
    one-off script in scripts/ — this is DB access, not a provider call, so
    it does not go through ai_helper.get_supabase()'s SSM lookup. Fails
    loudly instead of falling back to "" — see module docstring point 1.
    """
    import db_client  # repo-root module

    user_id = os.environ.get("EVAL_USER_ID", DEFAULT_USER_ID)
    db = db_client.SupabaseClient.from_env()
    result = (
        db.client.table("user_resumes")
        .select("tex_content")
        .eq("user_id", user_id)
        .order("created_at", desc=True)
        .limit(1)
        .execute()
    )
    tex = (result.data[0].get("tex_content") if result.data else None) or ""
    if not tex:
        raise RuntimeError(
            f"No base resume (user_resumes.tex_content) found for user_id={user_id}. "
            "Refusing to score golden-set cases against an empty resume — that would "
            "measure agreement with noise, not tier accuracy. Fix the DB row or set "
            "EVAL_USER_ID; do not silently pass resume_tex=''."
        )
    return tex


def _uncached_ai_complete(prompt, system="", cache_hours=72, temperature=0.3, max_tokens=4096):
    """Drop-in replacement for ai_complete_cached, patched in only for the
    duration of a repeats>1 case (see module docstring point 2)."""
    return ai_complete(prompt, system=system, temperature=temperature, max_tokens=max_tokens)


def _run_score_case(case: dict, resume_tex: str, repeats: int) -> dict:
    # job_hash is not otherwise meaningful here (golden fixtures are
    # standalone JSON, not DB rows) but score_batch.score_single_job's own
    # exception handlers do `job["job_hash"]` when logging a failure — a
    # dict without that key turns a real provider failure into a masking
    # KeyError("job_hash") that hides the actual cause. The fixture id is a
    # fine stand-in: it's only ever used for a log line.
    job = {
        "job_hash": case["id"], "title": case["title"],
        "company": case["company"], "description": case["description"],
    }
    scores: list[float] = []
    start = time.perf_counter()
    cache_ctx = (
        patch.object(score_batch, "ai_complete_cached", _uncached_ai_complete)
        if repeats > 1
        else nullcontext()
    )
    n_failed = 0
    with cache_ctx:
        for _ in range(repeats):
            out = score_batch.score_single_job(job, resume_tex=resume_tex, temperature=0)
            if out is None:
                n_failed += 1
                continue
            scores.append(out.get("match_score", 0))
    elapsed = time.perf_counter() - start
    ok = len(scores) > 0
    return {
        "id": case["id"],
        "task": "score",
        "expected_tier": case["expected"]["tier"],
        "actual_tier": score_batch.score_to_tier(sum(scores) / len(scores)) if ok else "D",
        "scores": scores,
        "guards_passed": True,
        "fabricated": False,
        "latency_s": elapsed / repeats,
        "ok": ok,
        "n_failed_calls": n_failed,
    }


_TAILOR_SYSTEM_PROMPT = (
    "You are an expert resume writer. You work with LaTeX resume BODIES only "
    "(the content between \\begin{document} and \\end{document}) — never emit "
    "\\documentclass, \\usepackage, or \\begin{document}/\\end{document}.\n\n"
    "Your output MUST contain, verbatim, these six section headers: "
    "\\section*{Summary}, \\section*{Technical Skills}, \\section*{Experience}, "
    "\\section*{Featured Projects}, \\section*{Education}, \\section*{Certifications}.\n"
    "PRESERVE every \\textbf{} from the base resume — reorder and reword bullets, "
    "never delete formatting. Do not invent skills, employers or metrics that are "
    "not in the base resume. Avoid generic filler phrases (e.g. 'proven track "
    "record', 'passionate about', 'leveraging', 'robust')."
)


def _run_tailor_case(case: dict, resume_tex: str) -> dict:
    _, base_body = _split_tex(resume_tex)
    base_skills = _base_skills_text(base_body)
    start = time.perf_counter()
    # The base resume body must be IN the prompt, not just passed to the
    # guards: a prompt with no base resume at all (the original plan's
    # draft) asks the model to invent a resume from nothing, which then
    # fails check_output's required_sections/textbf_preservation guards
    # regardless of model quality — the same "scoring against nothing"
    # defect ground truth point 2 calls out for the score path, generalised
    # here to tailoring.
    user_prompt = (
        f"Tailor this resume body for the following job.\n\n"
        f"Job: {case['title']} at {case['company']}\n"
        f"Description: {case['description'][:4000]}\n\n"
        f"BASE RESUME BODY:\n{base_body}\n\n"
        "Return ONLY the tailored body."
    )
    try:
        result = council_complete(
            user_prompt,
            system=_TAILOR_SYSTEM_PROMPT,
            task_description="resume tailoring",
            n_generators=2,
            task="tailor",
            base_skills=base_skills,
            base_body=base_body,
        )
    except Exception as exc:
        return {
            "id": case["id"], "task": "tailor", "expected_tier": None, "actual_tier": None,
            "scores": [], "guards_passed": False, "fabricated": False,
            "latency_s": time.perf_counter() - start, "ok": False, "error": str(exc),
        }
    content = result.get("content", "")
    guard = check_output(content, "tailor", base_skills_text=base_skills, base_body=base_body)
    missing = [k for k in case["expected"]["must_contain"] if k.lower() not in content.lower()]
    return {
        "id": case["id"],
        "task": "tailor",
        "expected_tier": None,
        "actual_tier": None,
        "scores": [],
        "guards_passed": guard.passed and not missing,
        "fabricated": any(v.rule == "fabrication" for v in guard.violations),
        "latency_s": time.perf_counter() - start,
        "ok": True,
        "missing_keywords": missing,
        "violations": [f"{v.rule}:{v.severity}: {v.detail}" for v in guard.violations],
    }


def _load_checkpoint() -> dict:
    if CHECKPOINT.exists():
        try:
            return json.loads(CHECKPOINT.read_text())
        except json.JSONDecodeError:
            return {}
    return {}


def _save_checkpoint(done: dict) -> None:
    CHECKPOINT.write_text(json.dumps(done, indent=2))


def run_golden(repeats: int = 1, resume: bool = True) -> list[dict]:
    """Run every golden case. See module docstring for the cache/resume/
    circuit-breaker behaviour.
    """
    cases = load_golden()
    done = _load_checkpoint() if resume else {}
    results: list[dict] = []
    resume_tex: str | None = None
    consecutive_failures = 0

    for i, case in enumerate(cases, 1):
        checkpointed = done.get(case["id"])
        if checkpointed and checkpointed.get("_repeats") == repeats:
            print(f"  [{i}/{len(cases)}] {case['id']} already done (checkpoint) — skipping")
            results.append(checkpointed)
            continue

        if resume_tex is None:
            resume_tex = _load_base_resume()

        try:
            if case["task"] == "score":
                result = _run_score_case(case, resume_tex, repeats)
            else:
                result = _run_tailor_case(case, resume_tex)
        except Exception as exc:  # noqa: BLE001 — a case-level crash must not kill the run
            result = {
                "id": case["id"], "task": case["task"], "expected_tier": case.get("expected", {}).get("tier"),
                "actual_tier": None, "scores": [], "guards_passed": False, "fabricated": False,
                "latency_s": 0.0, "ok": False, "error": str(exc),
            }

        result["_repeats"] = repeats
        results.append(result)
        done[case["id"]] = result
        _save_checkpoint(done)

        ok = result.get("ok", True)
        consecutive_failures = 0 if ok else consecutive_failures + 1
        status = "ok" if ok else f"FAILED ({result.get('error', 'no successful calls')})"
        print(f"  [{i}/{len(cases)}] {case['id']} ({case['task']}) {status}")

        if consecutive_failures >= CONSECUTIVE_FAILURE_LIMIT:
            print(
                f"\nSTOPPING after {consecutive_failures} consecutive failures — "
                "providers are very likely rate-limited or exhausted. Re-run the same "
                "command later: completed cases are checkpointed in "
                f"{_display_path(CHECKPOINT)} and will be skipped."
            )
            print(f"Progress: {len(results)}/{len(cases)} cases attempted before stopping.")
            break

    return results


if __name__ == "__main__":
    _repeats = 1
    _fresh = "--fresh" in sys.argv
    _argv_positional = [a for a in sys.argv[1:] if not a.startswith("--")]
    if _argv_positional:
        _repeats = int(_argv_positional[0])

    _results = run_golden(repeats=_repeats, resume=not _fresh)
    _report = summarise(_results)
    _n_errors = sum(1 for r in _results if not r.get("ok", True))
    REPORT.write_text(json.dumps({
        "summary": _report,
        "n_cases_attempted": len(_results),
        "n_cases_total": len(load_golden()),
        "n_errors": _n_errors,
        "repeats": _repeats,
        "results": _results,
    }, indent=2))
    print(f"\nWrote {_display_path(REPORT)}")
    print(json.dumps(_report, indent=2))
    if _n_errors:
        print(f"\n{_n_errors}/{len(_results)} cases had zero successful calls — see \"error\" fields in the report.")
