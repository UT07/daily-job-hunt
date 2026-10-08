"""Guards for the AI council's model configuration.

Background (2026-08-31 audit): every one of the 7 configured providers was
returning 404/410/429 because the free model IDs had been retired or moved
behind paywalls by their vendors. The council was 0/7 alive, which silently
broke scoring (1,099 of 1,205 jobs stuck at score_status='pending') and made
tailoring fail with "Council: all generators failed" — which in turn killed
the whole daily Step Functions run.

Nothing in CI caught it because no test asserted anything about the model
IDs, and the runtime treats a dead provider as an ordinary failover.

These tests can't call the vendor APIs (no keys in CI, and we don't want CI
depending on a third party's uptime), so they pin the things that CAN be
checked offline: that known-retired IDs never come back, that reasoning
models get a workable token budget, and that family dedup actually dedups.
"""
import pytest

from lambdas.pipeline import ai_helper


# Model IDs confirmed dead on 2026-08-31 by live probe against the production
# SSM keys. Each returned a hard failure, not a transient one:
#   llama-3.3-70b-versatile            groq        404 does not exist
#   meta/llama-3.3-70b-instruct        nvidia      410 Gone
#   qwen/qwen3.6-plus:free             openrouter  404 free tier deprecated
#   meta-llama/llama-3.3-70b-instruct:free         404 unavailable for free
#   z-ai/glm-4.5-air:free                          404 unavailable for free
#   google/gemma-3-27b-it:free                     404 unavailable for free
#
# Added 2026-10-08, and this one is dead in a different way -- it answers, and
# the answer is never usable:
#   qwen-3.8-27b    cerebras    returns only reasoning tokens, no content
# Measured by production over the 212-résumé batch of 2026-10-07: 385
# appearances, 109 of this failure (68 at max_tokens=8192, 41 at 1024). A
# larger budget buys nothing -- it fails at 8192 as readily as at 1024.
#
# HOST-SPECIFIC, and the entry is safe only because the two spellings differ.
# Cerebras serves it as `qwen-3.8-27b`; Groq serves the SAME WEIGHTS as
# `qwen/qwen3.8-27b`, 365 appearances and ZERO failures of this kind. Retiring
# the Groq spelling would remove a working model and, since _model_family
# collapses the pair, take the whole qwen3 family with it.
#
# Added 2026-10-08, recorded dead in lambdas/pipeline/ai_helper.py since the
# 2026-09-28 probe ("404 -- model id no longer exists") and still listed in
# ai_client.from_config until this date, because nothing compared the two:
#   minimax/minimax-m3:free                openrouter  404
#   z-ai/glm-5.2:free                      openrouter  404
#   inclusionai/ling-3.0-flash-fin:free    openrouter  404, absent from /models
RETIRED_MODEL_IDS = {
    "llama-3.3-70b-versatile",
    "meta/llama-3.3-70b-instruct",
    "qwen/qwen3.6-plus:free",
    "meta-llama/llama-3.3-70b-instruct:free",
    "z-ai/glm-4.5-air:free",
    "google/gemma-3-27b-it:free",
    "qwen-3.8-27b",
    "minimax/minimax-m3:free",
    "z-ai/glm-5.2:free",
    "inclusionai/ling-3.0-flash-fin:free",
}


def test_provider_list_contains_no_retired_models():
    """The council must not ship a model ID we've confirmed is dead."""
    configured = {p["model"] for p in ai_helper._build_provider_list()}
    still_dead = configured & RETIRED_MODEL_IDS
    assert not still_dead, (
        f"Council still configures retired model(s): {sorted(still_dead)}. "
        "These return 404/410 and make the council fail closed."
    )


def test_the_working_host_of_a_host_specific_retirement_survives():
    """`qwen-3.8-27b` is retired and `qwen/qwen3.8-27b` must NOT be.

    The two ids are the same weights on two hosts and differ only by a hyphen
    against a slash. Only Cerebras' serving of it is broken. Retiring the Groq
    spelling as well would drop a model with 365 clean appearances and -- since
    _model_family collapses the pair -- remove the qwen3 family from the
    council entirely, which the diversity tests below would then be satisfying
    with one family fewer and no one the wiser.
    """
    # The registry entry is asserted as well as the pool, because the two do
    # different jobs and only one of them survives a careless edit. The pool
    # assertion below stops the model coming BACK; the registry entry is the
    # record of WHY, and deleting it leaves a retirement no reader can account
    # for — which is how a measured decision becomes folklore and then gets
    # reversed. Mutation-tested: emptying the registry alone broke nothing.
    assert "qwen-3.8-27b" in RETIRED_MODEL_IDS, (
        "the retirement is no longer recorded; the measurement behind it is in "
        "the comment above RETIRED_MODEL_IDS and should go with it"
    )
    configured = {p["model"] for p in ai_helper._build_provider_list()}
    assert "qwen-3.8-27b" not in configured, "Cerebras' broken spelling is retired"
    assert "qwen/qwen3.8-27b" in configured, (
        "Groq's qwen entry is gone. If that was deliberate, say so here and in "
        "RETIRED_MODEL_IDS with the measurement; if it was collateral damage "
        "from retiring Cerebras' lookalike id, it has cost the council a family."
    )


def test_provider_list_is_not_empty():
    providers = ai_helper._build_provider_list()
    assert len(providers) >= 3, (
        f"Council has only {len(providers)} providers; needs at least 3 so "
        "_select_diverse_providers can pick distinct families."
    )


def test_council_has_at_least_three_distinct_families():
    """Diversity is the point of the council — verify it's achievable."""
    families = {ai_helper._model_family(p["model"]) for p in ai_helper._build_provider_list()}
    assert len(families) >= 3, (
        f"Only {len(families)} distinct model families configured ({sorted(families)}). "
        "The council degenerates to the same model voting with itself."
    )


@pytest.mark.parametrize(
    "a,b",
    [
        ("openai/gpt-oss-120b", "openai/gpt-oss-20b"),
        ("qwen/qwen3.8-27b", "qwen/qwen3.6-27b"),
        # Two HOSTS for one model, not two models. Cerebras serves Qwen 3.8 27B
        # as "qwen-3.8-27b" and Groq as "qwen/qwen3.8-27b" — the hyphen is the
        # only difference, and the prefix table matches "qwen3", so the
        # Cerebras spelling used to fall through to its own family. Two
        # generators on the same weights is one generator.
        ("qwen-3.8-27b", "qwen/qwen3.8-27b"),
        ("gpt-oss-120b", "openai/gpt-oss-120b"),
    ],
)
def test_same_family_models_collapse(a, b):
    """Size variants of one model are the SAME family.

    If they don't collapse, _select_diverse_providers can pick both and the
    council loses the independence that makes its vote meaningful.
    """
    assert ai_helper._model_family(a) == ai_helper._model_family(b), (
        f"{a!r} and {b!r} resolve to different families "
        f"({ai_helper._model_family(a)!r} vs {ai_helper._model_family(b)!r})"
    )


def test_critic_token_budget_is_reasoning_safe():
    """Reasoning models spend the budget before emitting any content.

    gpt-oss-120b burned 298 of 300 tokens on `reasoning` and returned
    content='' with finish_reason='length'. _call_provider treats empty
    content as a failure, so a reasoning critic silently degrades the
    council to "return the first candidate". The budget must leave room
    for reasoning AND the answer.
    """
    assert ai_helper.CRITIC_MAX_TOKENS >= 512, (
        f"CRITIC_MAX_TOKENS={ai_helper.CRITIC_MAX_TOKENS} is too small for a "
        "reasoning model; it will return empty content and fail the critic."
    )


# ---------------------------------------------------------------------------
# The same guard, for the OTHER client.
#
# Everything above checks ai_helper._build_provider_list() — the pipeline
# council. Nothing checked ai_client.py, the client main.py and the local
# dry-run path use, and that is how NvidiaNIMProvider kept defaulting to
# meta/llama-3.3-70b-instruct after it went end-of-life. The model id was
# already in RETIRED_MODEL_IDS above, documented as "nvidia 410 Gone", and the
# default sat there for a month because no test looked at this file.
# ---------------------------------------------------------------------------

def _ai_client_default_models():
    """Default `model=` on every AIProvider subclass in ai_client.py."""
    import inspect

    import ai_client

    defaults = {}
    for name, obj in vars(ai_client).items():
        if not inspect.isclass(obj) or not issubclass(obj, ai_client.AIProvider):
            continue
        if obj is ai_client.AIProvider:
            continue
        param = inspect.signature(obj.__init__).parameters.get("model")
        if param is not None and param.default is not inspect.Parameter.empty:
            defaults[name] = param.default
    return defaults


def test_ai_client_defaults_find_some_providers():
    """Guard the guard: a broken scan must not pass silently."""
    assert len(_ai_client_default_models()) >= 3, _ai_client_default_models()


def test_no_ai_client_provider_defaults_to_a_retired_model():
    dead = {n: m for n, m in _ai_client_default_models().items() if m in RETIRED_MODEL_IDS}
    assert not dead, (
        f"ai_client provider(s) default to a model confirmed dead: {dead}. "
        "These return 404/410 on every call."
    )


def test_410_is_treated_as_permanent_in_both_clients():
    """A model retired on a published date never comes back.

    Without this, 410 fell to the transient branch and the model was retried
    every two minutes forever — the exact waste the cooldown table exists to
    prevent.
    """
    import ai_client

    assert 410 in ai_client.AIClient._DEAD_CODES

    import inspect

    src = inspect.getsource(ai_helper.note_provider_failure)
    assert "410" in src, (
        "ai_helper.note_provider_failure does not mention 410; an end-of-lifed "
        "model gets the short transient cooldown instead of the long one"
    )


# ---------------------------------------------------------------------------
# The same guard, for EVERY file that can name a model.
#
# Both checks above look where someone already thought to look: the pipeline
# council, then ai_client's class defaults. CLAUDE.md #10 is the lesson that
# keeps having to be relearned — a guard and the data it guards never meet
# unless the scan is the whole repo. On 2026-10-08 a wider look found dead ids
# that neither check could see: two in ai_client.from_config's OpenRouter list
# (recorded 404 in ai_helper since 09-28), three in
# scripts/backfill_requirement_map.py, and one in config.yaml.
#
# What counts as "naming a model": a Python string constant EQUAL to a retired
# id, a YAML token outside a comment, or a JSON string value. Comments and
# docstrings that explain WHY a model was retired are not configuration, and
# flagging them would push people to delete exactly the history that stops a
# retirement being reversed.
# ---------------------------------------------------------------------------
import ast  # noqa: E402
import json  # noqa: E402
import pathlib  # noqa: E402
import re  # noqa: E402
import subprocess  # noqa: E402

REPO = pathlib.Path(__file__).resolve().parents[2]
_EXCLUDED_DIRS = {"tests", "node_modules", ".worktrees", ".claude"}
_SCANNED_SUFFIXES = {".py", ".yaml", ".yml", ".json"}
_YAML_TOKEN = re.compile(r"[A-Za-z0-9._/:\-]+")
_YAML_COMMENT = re.compile(r"(^|\s)#.*$")


def _scanned_files() -> list[pathlib.Path]:
    """Tracked files plus untracked-but-not-ignored ones, so a new script is
    checked before it is ever committed."""
    out = subprocess.run(
        ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
        cwd=REPO, capture_output=True, check=True,
    ).stdout.decode()
    files = []
    for rel in sorted(set(filter(None, out.split("\0")))):
        p = pathlib.PurePosixPath(rel)
        if p.suffix not in _SCANNED_SUFFIXES or set(p.parts) & _EXCLUDED_DIRS:
            continue
        if (REPO / rel).is_file():
            files.append(REPO / rel)
    return files


def _retired_in_python(src: str) -> list[tuple[int, str]]:
    return [
        (node.lineno, node.value)
        for node in ast.walk(ast.parse(src))
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and node.value in RETIRED_MODEL_IDS
    ]


def _retired_in_yaml(src: str) -> list[tuple[int, str]]:
    hits = []
    for n, line in enumerate(src.splitlines(), 1):
        for tok in _YAML_TOKEN.findall(_YAML_COMMENT.sub("", line)):
            if tok in RETIRED_MODEL_IDS:
                hits.append((n, tok))
    return hits


def _retired_in_json(src: str) -> list[tuple[int, str]]:
    hits = []

    def walk(v):
        if isinstance(v, str):
            if v in RETIRED_MODEL_IDS:
                hits.append((0, v))
        elif isinstance(v, dict):
            for k, x in v.items():
                walk(k)
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)

    walk(json.loads(src))
    return hits


_FINDERS = {".py": _retired_in_python, ".yaml": _retired_in_yaml,
            ".yml": _retired_in_yaml, ".json": _retired_in_json}


def test_the_repo_scan_covers_the_files_that_name_models():
    """Guard the guard: a scan that silently reads nothing passes everything."""
    scanned = {str(p.relative_to(REPO)) for p in _scanned_files()}
    assert len(scanned) >= 100, f"only {len(scanned)} files scanned"
    for must in ("ai_client.py", "lambdas/pipeline/ai_helper.py", "config.yaml",
                 "scripts/backfill_requirement_map.py",
                 "lambdas/pipeline/agents/model_registry.json", "template.yaml"):
        assert must in scanned, f"{must} is not scanned"
    assert not any(s.startswith("tests/") for s in scanned)


def test_the_finders_see_configuration_and_ignore_commentary():
    """Calibrate the instrument on known inputs before trusting its zero."""
    dead = "z-ai/glm-5.2:free"
    assert _retired_in_python(f'MODEL = "{dead}"\n') == [(1, dead)]
    assert _retired_in_python(f'X = {{"model": "{dead}"}}\n') == [(1, dead)]
    assert _retired_in_python(f'# {dead} 404\n"""{dead} was retired."""\n') == []
    assert _retired_in_yaml(f'ai:\n  model: "{dead}"\n') == [(2, dead)]
    assert _retired_in_yaml(f"ai:\n  model: {dead}  # retired\n") == [(2, dead)]
    assert _retired_in_yaml(f"# was {dead}, 404\nai: {{}}\n") == []
    assert _retired_in_json(json.dumps({"models": [{"model": dead}]})) == [(0, dead)]
    assert _retired_in_json(json.dumps({"_note": f"{dead} was removed"})) == []


def test_no_file_in_the_repo_configures_a_retired_model():
    found, unreadable = [], []
    for path in _scanned_files():
        src = path.read_text(encoding="utf-8", errors="replace")
        try:
            hits = _FINDERS[path.suffix](src)
        except (SyntaxError, ValueError) as e:
            unreadable.append(f"{path.relative_to(REPO)}: {type(e).__name__}")
            continue
        found += [f"{path.relative_to(REPO)}:{line} {model}" for line, model in hits]
    assert not found, (
        "Retired model id(s) still configured — these return 404/410 on every "
        "call:\n  " + "\n  ".join(found)
    )
    # A file the guard cannot parse is a file it did not check. Say so rather
    # than let it count towards a clean result.
    assert not unreadable, f"files the retired-model scan could not parse: {unreadable}"
