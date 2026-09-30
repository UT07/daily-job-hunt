"""Cerebras — a fifth independent quota for both AI clients.

The pool's weakness was never entry count. It is that the entries sit on a
small number of ACCOUNTS, and two of them carry nearly all of the load:

  * Groq's free tier is 8,000 tokens per MINUTE. A tailoring call carries the
    whole base resume in and the whole body out, so one call can consume it.
  * OpenRouter's free pool shares ONE daily allowance across every model on
    it. Measured 2026-09-29 against the live keys: all four OpenRouter entries
    failed in the same sweep — two 404, two 429 — because the account-wide
    allowance was spent. They are one point of failure, not four.

So the lever is another account, not another model. Cerebras' published free
limits (fetched 2026-09-30 from inference-docs.cerebras.ai/support/rate-limits)
are 5 RPM / 30k TPM / 1M TPD on each of the two models it serves through
shared inference — nearly 4x Groq's per-minute token budget, on a quota shared
with nothing else in the chain.

What these tests pin, in order of what has actually broken before:

  1. the key is read by name and an absent key is a clean skip, not a crash
     (rule: a provider nobody has a key for must not cost a request);
  2. a Cerebras 429 cools CEREBRAS, not Groq and not OpenRouter — the
     cooldown table is only useful if its scope is right;
  3. `qwen-3.8-27b` (Cerebras' spelling) and `qwen/qwen3.8-27b` (Groq's) are
     the SAME weights and must collapse to one family, or
     _select_diverse_providers picks both as "distinct" generators and the
     council reviews its own answer;
  4. the key reaches every path that needs it — the eval gate and the
     deployed stack — because a provider wired into the code and not into
     the deploy is a provider that works only in unit tests.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, "lambdas/pipeline")
import ai_helper  # noqa: E402

CEREBRAS_KEY_PARAM = "/naukribaba/CEREBRAS_API_KEY"
CEREBRAS_URL = "https://api.cerebras.ai/v1/chat/completions"

# The two models Cerebras serves through shared (free) inference, per its own
# docs on 2026-09-30. Both are models this repo has already probed successfully
# on another host: openai/gpt-oss-120b is the production council's primary Groq
# entry ("strongest available, ~800ms") and qwen/qwen3.8-27b its fastest
# ("~335ms, clean JSON"). Same weights, different account — which is the whole
# point, and why no new capability risk comes with the new quota.
CEREBRAS_MODELS = {"gpt-oss-120b", "qwen-3.8-27b"}


@pytest.fixture(autouse=True)
def _clean():
    ai_helper._reset_cooldowns()
    yield
    ai_helper._reset_cooldowns()


def _cerebras_entries():
    """Every Cerebras entry in the pool.

    Asserts non-empty on purpose. Several tests below iterate this list, and an
    empty list makes `for p in ...` and `all(...)` pass vacuously — a check
    that reports success on a pool with no Cerebras in it at all is not a
    check (CLAUDE.md verification rule 2).
    """
    entries = [p for p in ai_helper._build_provider_list()
               if p["key_param"] == CEREBRAS_KEY_PARAM]
    assert entries, "no Cerebras entries in the pool — the rest of this file is vacuous"
    return entries


def _names():
    return [p["name"] for p in ai_helper._build_provider_list()]


def _first_index(names, prefix):
    return next((i for i, n in enumerate(names) if n.startswith(prefix)), None)


# ---------------------------------------------------------------------------
# 1. The pipeline council's pool
# ---------------------------------------------------------------------------

def test_cerebras_is_an_independent_quota_in_the_pool():
    keys = {p["key_param"] for p in ai_helper._build_provider_list()}
    assert CEREBRAS_KEY_PARAM in keys, (
        f"{CEREBRAS_KEY_PARAM} unused — the council is back to four quotas. "
        f"Have: {sorted(keys)}"
    )


def test_cerebras_entries_call_the_openai_compatible_endpoint():
    for p in _cerebras_entries():
        assert p["url"] == CEREBRAS_URL, f"{p['name']} points at {p['url']}"


def test_cerebras_serves_exactly_the_models_it_documents():
    """A model id is a claim about a remote catalog; keep it checkable."""
    assert {p["model"] for p in _cerebras_entries()} == CEREBRAS_MODELS


def test_cerebras_is_its_own_billing_account():
    """_account_of splits the name on '/', so the prefix IS the quota bucket."""
    for p in _cerebras_entries():
        assert ai_helper._account_of(p) == "cerebras", (
            f"{p['name']} resolves to account {ai_helper._account_of(p)!r} — a "
            "Cerebras 429 would cool the wrong provider"
        )


def test_cerebras_is_tried_after_gemini_and_before_openrouter():
    """Order by evidence, not by enthusiasm.

    Gemini stays ahead: load-verified 24/24 at ~0.9s. Cerebras goes ahead of
    NVIDIA (4/6 under concurrency) and OpenRouter (50 requests/day without
    credits, routinely exhausted) on its published limits, but behind the one
    provider in the pool that has been measured under sustained load.
    """
    names = _names()
    gem = _first_index(names, "gemini/")
    cer = _first_index(names, "cerebras/")
    orr = _first_index(names, "openrouter/")
    assert cer is not None, f"no cerebras provider in the chain: {names}"
    assert gem < cer < orr, (
        f"expected gemini({gem}) < cerebras({cer}) < openrouter({orr}); order: {names}"
    )


# ---------------------------------------------------------------------------
# 2. Cooldown scope — the table is only useful if it cools the right thing
# ---------------------------------------------------------------------------

def test_a_cerebras_429_does_not_cool_groq_or_openrouter():
    cerebras = _cerebras_entries()[0]
    ai_helper.note_provider_failure(cerebras, 429)

    assert ai_helper._is_available(cerebras) is False
    for other in ai_helper._build_provider_list():
        if other["key_param"] == CEREBRAS_KEY_PARAM:
            continue
        assert ai_helper._is_available(other) is True, (
            f"a Cerebras 429 cooled {other['name']} — different account"
        )


def test_a_groq_429_does_not_cool_cerebras():
    """The converse, because the bug is symmetric and only one direction of it
    would show up in a Groq-first chain."""
    cerebras = _cerebras_entries()
    groq = next(p for p in ai_helper._build_provider_list()
                if p["name"].startswith("groq/"))
    ai_helper.note_provider_failure(groq, 429)
    assert all(ai_helper._is_available(p) for p in cerebras), (
        "a Groq 429 cooled Cerebras — then it is not a failover"
    )


def test_cerebras_gets_a_short_cooldown_because_its_limit_refills_continuously():
    """Cerebras rate-limits with a token bucket that replenishes continuously
    (its docs say so explicitly), and the free cap is 1M tokens/day against
    30k/minute — so the limit a real run hits is the per-minute one, which
    clears in about a minute. The unknown-account default of 300s would bench
    a healthy provider for five times longer than it needs.
    """
    assert "cerebras" in ai_helper._RATE_LIMIT_COOLDOWN_S, (
        "cerebras falls through to the 300s unknown-account default"
    )
    assert ai_helper._RATE_LIMIT_COOLDOWN_S["cerebras"] <= 300


def test_a_cerebras_429_naming_a_daily_cap_still_cools_until_midnight():
    """The body-derived policy is account-agnostic and must stay that way."""
    cerebras = _cerebras_entries()[0]
    secs = ai_helper._rate_limit_cooldown_seconds(
        "cerebras", "Rate limit exceeded: tokens per day")
    assert secs > ai_helper._RATE_LIMIT_COOLDOWN_S["cerebras"]
    assert ai_helper._account_of(cerebras) == "cerebras"


def test_the_council_still_has_a_cross_family_critic_with_groq_and_openrouter_down():
    """The reason this provider is worth a PR, stated as a measurement.

    Cool the two accounts that actually run out — Groq's 8k-tokens/minute
    budget and OpenRouter's account-wide free-models-per-day cap — and count
    what is left. Before Cerebras the survivors were Gemini and NVIDIA: two
    families, which a 2-generator council consumes entirely, leaving
    select_critic to fall back to "any provider" and letting a generator's own
    family review its output. That is the 2026-09-28 failure recorded in
    test_provider_rotation.py, with Groq added to it.

    Counted on the real pool, families available in that state:
      before  {gemini, nemotron}                  -> no critic
      after   {gemini, nemotron, gpt-oss, qwen3}  -> two spare families

    The two extra families are Groq's own models reached through a different
    account, which is the whole argument: the missing capacity was never
    another opinion, it was another quota.
    """
    pool = ai_helper._build_provider_list()
    ai_helper.note_provider_failure(
        {"name": "groq/gpt-oss-120b", "model": "openai/gpt-oss-120b"}, 429)
    ai_helper.note_provider_failure(
        {"name": "openrouter/anything", "model": "m:free"}, 429,
        "Rate limit exceeded: free-models-per-day")

    usable = [p for p in pool if ai_helper._is_available(p)]
    accounts = {ai_helper._account_of(p) for p in usable}
    assert "cerebras" in accounts, f"only {sorted(accounts)} survived"

    gens = ai_helper._select_diverse_providers(usable, n=2)
    assert len(gens) == 2, f"only {len(gens)} generator(s) available"
    gen_families = {ai_helper._model_family(g["model"]) for g in gens}
    critic = ai_helper._select_diverse_providers(
        usable, n=1, exclude_families=gen_families)
    assert critic, "no cross-family critic with Groq and OpenRouter cooled"
    assert ai_helper._model_family(critic[0]["model"]) not in gen_families


# ---------------------------------------------------------------------------
# 3. Family dedup — same weights on two hosts must not read as two opinions
# ---------------------------------------------------------------------------

def test_cerebras_qwen_collapses_into_the_same_family_as_groqs_qwen():
    """`qwen-3.8-27b` and `qwen/qwen3.8-27b` are one model on two hosts.

    _model_family's prefix table matches "qwen3"; Cerebras' hyphenated
    spelling does not, so without normalisation it falls through to
    `return m` and becomes its own family. _select_diverse_providers would
    then pick both as distinct generators, and a council whose two
    "independent" generators are the same weights is one generator.
    """
    assert ai_helper._model_family("qwen-3.8-27b") == \
        ai_helper._model_family("qwen/qwen3.8-27b")


def test_cerebras_gpt_oss_collapses_into_groqs_gpt_oss_family():
    assert ai_helper._model_family("gpt-oss-120b") == \
        ai_helper._model_family("openai/gpt-oss-120b")


@pytest.mark.parametrize("alias", ["qwen-plus", "qwen-turbo", "qwen-max"])
def test_the_hyphen_normalisation_leaves_dashscope_aliases_alone(alias):
    """Guard the guard: only a hyphen followed by a DIGIT is collapsed.

    DashScope's qwen-plus / qwen-turbo / qwen-max are genuinely distinct
    entries in the prefix table; a blanket hyphen strip would merge them.
    """
    assert ai_helper._model_family(alias) == alias


def test_cerebras_adds_no_family_the_pool_did_not_already_have():
    """Stated so the next reader does not "fix" it.

    Cerebras is here for its QUOTA, not for family diversity — both of its
    models are ones Groq already serves. Family dedup therefore means at most
    one of each pair is picked per decision, which is correct: they are the
    same weights. The value is that when Groq's per-minute budget is spent,
    the Cerebras copy is still callable.
    """
    pool = ai_helper._build_provider_list()
    cerebras_fams = {ai_helper._model_family(p["model"])
                     for p in _cerebras_entries()}
    other_fams = {ai_helper._model_family(p["model"]) for p in pool
                  if p["key_param"] != CEREBRAS_KEY_PARAM}
    assert cerebras_fams <= other_fams, (
        f"Cerebras introduced new families {sorted(cerebras_fams - other_fams)} — "
        "if that is intended, say so here and in the pool comment"
    )


# ---------------------------------------------------------------------------
# 4. Absent key — a clean skip, before any network call
# ---------------------------------------------------------------------------

def test_absent_cerebras_key_is_a_clean_skip_not_a_crash(monkeypatch):
    """Nobody has set the SSM parameter yet, and until they do this hop must
    cost nothing but a dict lookup.

    get_param raises ParameterNotFound when the parameter does not exist.
    _call_provider must swallow that, return None so the chain moves on, and
    never reach httpx.
    """
    cerebras = _cerebras_entries()[0]

    def _not_found(_name):
        raise RuntimeError("ParameterNotFound")

    def _no_http(*_a, **_kw):
        raise AssertionError("_call_provider tried the network without a key")

    monkeypatch.setattr(ai_helper, "get_param", _not_found)
    monkeypatch.setattr(ai_helper.httpx, "post", _no_http)

    assert ai_helper._call_provider(cerebras, "hi") is None


def test_an_absent_cerebras_key_does_not_cool_the_rest_of_the_pool(monkeypatch):
    """A missing key is a fact about ONE account. Cooling anything else would
    turn an unconfigured provider into an outage."""
    cerebras = _cerebras_entries()[0]
    monkeypatch.setattr(ai_helper, "get_param",
                        lambda _n: (_ for _ in ()).throw(RuntimeError("ParameterNotFound")))
    ai_helper._call_provider(cerebras, "hi")

    for other in ai_helper._build_provider_list():
        if other["name"] == cerebras["name"]:
            continue
        assert ai_helper._is_available(other) is True, (
            f"a missing Cerebras key cooled {other['name']}"
        )


def test_an_empty_cerebras_key_is_also_a_clean_skip(monkeypatch):
    """SSM can hold the parameter with an empty value, and template.yaml
    passes CerebrasApiKey="" until the secret exists — so "present but blank"
    is a real state, not a hypothetical."""
    cerebras = _cerebras_entries()[0]
    monkeypatch.setattr(ai_helper, "get_param", lambda _n: "")
    monkeypatch.setattr(
        ai_helper.httpx, "post",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("called the API with an empty key")),
    )
    assert ai_helper._call_provider(cerebras, "hi") is None


# ---------------------------------------------------------------------------
# 5. ai_client.py — the OTHER client, which is how a provider gets forgotten
# ---------------------------------------------------------------------------
# ai_client.py serves app.py (the API / Studio path) and main.py. It has its
# own provider classes and its own assembly in from_config, and the last time
# a change touched only one of the two clients, ai_client.py sat on a retired
# model default for a month (see tests/unit/test_ai_council_models.py).

def _ai_client():
    import ai_client
    return ai_client


PROVIDER_ENV_VARS = [
    "CEREBRAS_API_KEY", "GROQ_API_KEY", "NVIDIA_API_KEY",
    "OPENROUTER_API_KEY", "QWEN_API_KEY",
]


@pytest.fixture
def no_provider_env(monkeypatch):
    """tests/conftest.py loads .env into os.environ, so a developer's real keys
    would otherwise decide what from_config builds."""
    for name in PROVIDER_ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def _config(tmp_path, **api_keys):
    return {"api_keys": api_keys,
            "ai": {"cache": {"path": str(tmp_path / "ai_cache.db")}}}


def test_ai_client_has_a_cerebras_provider_class():
    ai_client = _ai_client()
    assert hasattr(ai_client, "CerebrasProvider")
    assert issubclass(ai_client.CerebrasProvider, ai_client.AIProvider)


def test_cerebras_provider_defaults_to_a_model_cerebras_serves():
    import inspect
    default = inspect.signature(
        _ai_client().CerebrasProvider.__init__).parameters["model"].default
    assert default in CEREBRAS_MODELS, (
        f"default model {default!r} is not one of the models Cerebras serves "
        f"through shared inference: {sorted(CEREBRAS_MODELS)}"
    )


def test_cerebras_provider_is_named_and_addressed_correctly():
    p = _ai_client().CerebrasProvider(api_key="test-key-not-real")
    assert p.name == "cerebras"
    assert p.base_url == "https://api.cerebras.ai/v1"


def test_from_config_adds_cerebras_when_a_key_is_present(tmp_path, no_provider_env):
    client = _ai_client().AIClient.from_config(
        _config(tmp_path, groq="g-not-real", cerebras="c-not-real"))
    models = {p.model for p in client.providers if p.name == "cerebras"}
    assert models == CEREBRAS_MODELS, f"got {models}"


def test_from_config_skips_cerebras_when_the_key_is_absent(tmp_path, no_provider_env):
    """Not added to the pool at all — the same thing every other provider in
    from_config does, and no exception on the way."""
    client = _ai_client().AIClient.from_config(_config(tmp_path, groq="g-not-real"))
    assert not [p for p in client.providers if p.name == "cerebras"]
    assert client.providers, "from_config built nothing at all"


def test_from_config_reads_the_key_from_the_environment_by_name(
        tmp_path, no_provider_env, monkeypatch):
    """config.yaml holds "${CEREBRAS_API_KEY}"; get_key must resolve that from
    the environment under exactly that name, like every other provider."""
    monkeypatch.setenv("CEREBRAS_API_KEY", "from-env-not-real")
    client = _ai_client().AIClient.from_config(
        _config(tmp_path, groq="g-not-real", cerebras="${CEREBRAS_API_KEY}"))
    keys = {p.api_key for p in client.providers if p.name == "cerebras"}
    assert keys == {"from-env-not-real"}


def test_config_yaml_declares_the_cerebras_key():
    import yaml
    cfg = yaml.safe_load(pathlib.Path("config.yaml").read_text())
    assert cfg["api_keys"].get("cerebras") == "${CEREBRAS_API_KEY}"


# ---------------------------------------------------------------------------
# 6. The key must reach every path that needs it
# ---------------------------------------------------------------------------
# A provider wired into the code and not into the deploy works only in unit
# tests. Both mechanisms matter and they are different: the zip pipeline
# Lambdas read the key from SSM, the container Lambda reads it from its
# environment, and CI's eval gate has no AWS access at all and needs it as a
# plain secret.

def test_the_eval_gate_can_reach_cerebras():
    ci = pathlib.Path(".github/workflows/ci.yml").read_text()
    assert "CEREBRAS_API_KEY: ${{ secrets.CEREBRAS_API_KEY }}" in ci, (
        "the AI Eval Gate has no AWS credentials, so get_param cannot fall "
        "through to SSM there — without the plain secret the gate measures a "
        "pool that is missing this provider"
    )


def test_the_deployed_stack_passes_the_cerebras_key():
    template = pathlib.Path("template.yaml").read_text()
    assert "CerebrasApiKey:" in template, "no CFN parameter for the key"
    assert "CEREBRAS_API_KEY: !Ref CerebrasApiKey" in template, (
        "JobHuntApi (app.py / the Studio path) reads provider keys from its "
        "environment, not from SSM"
    )
    deploy = pathlib.Path(".github/workflows/deploy.yml").read_text()
    assert "CerebrasApiKey=" in deploy, (
        "sam deploy does not pass the parameter, so it stays at its default"
    )


def _load_template_tolerant_of_cfn_tags():
    """PyYAML cannot parse CFN short tags (!Ref, !Sub, ...); discard the tag and
    keep the node. Same helper shape as tests/unit/test_deploy_path_parity.py."""
    import yaml

    class _Loader(yaml.SafeLoader):
        pass

    def _underlying(loader, _tag_suffix, node):
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        if isinstance(node, yaml.MappingNode):
            return loader.construct_mapping(node)
        return None

    _Loader.add_multi_constructor("!", _underlying)
    with pathlib.Path("template.yaml").open() as fh:
        return yaml.load(fh, Loader=_Loader)


def test_the_cerebras_cfn_parameter_defaults_to_empty():
    """The stack must deploy before the secret exists — an absent provider is
    a skipped hop, not a broken deploy."""
    param = _load_template_tolerant_of_cfn_tags()["Parameters"]["CerebrasApiKey"]
    assert param.get("Default") == "", param
    assert param.get("NoEcho") is True, "an API key must not be echoed by CFN"
