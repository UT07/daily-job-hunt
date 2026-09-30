"""Phase 0: LangSmith must say, out loud, whether it is exporting anything.

Measured 2026-09-30 against the live system:

- `/naukribaba/LANGSMITH_API_KEY` does not exist. The parameter the code
  actually reads is `/naukribaba/LANGCHAIN_API_KEY` (SecureString, version 2,
  last written 2026-09-23).
- That parameter holds a well-formed `lsv2_sk_` key which LangSmith rejects
  with **403** on every authenticated endpoint, on both the US and the EU
  host. A deliberately fabricated key gets the same 403; sending no key at all
  gets 401. So the stored credential is in the "not recognised" class, not the
  "wrong region" or "malformed" class.
- CloudWatch for naukribaba-tailor-resume on 2026-09-28 carries 20+
  `Failed to POST https://api.smith.langchain.com/runs/multipart ... 403`
  warnings from the langsmith SDK itself, which proves the LANGCHAIN_* wiring
  reaches the exporter. Only the credential is dead.

What this file guards is the part that is ours: after
`_configure_langsmith_tracing()` runs, exactly one greppable line says which
of six outcomes happened, every outcome that means "no traces" is at WARNING
or above, and a `[council]` line can be matched back to a LangSmith run by
trace_id.

Rule 2 in CLAUDE.md: a status that cannot distinguish "did the work" from
"did nothing" is a lie. Before this, a 403 and a function with tracing switched
off produced the same observable: silence.
"""
import logging
import re
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import yaml

sys.path.insert(0, "lambdas/pipeline")
from agents import graph  # noqa: E402
from agents import nodes  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "template.yaml"

CAND = {"content": "x", "provider": "p", "model": "m"}
P1 = {"name": "groq/a", "model": "openai/gpt-oss-120b"}


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("LANGCHAIN_API_KEY", raising=False)
    monkeypatch.delenv("LANGCHAIN_TRACING_V2", raising=False)
    monkeypatch.delenv("LANGCHAIN_ENDPOINT", raising=False)
    monkeypatch.setattr(graph, "_LANGSMITH_STATUS", None, raising=False)
    yield


def _resp(code):
    r = MagicMock()
    r.status_code = code
    return r


def _status_lines(caplog):
    return [r for r in caplog.records if graph.LANGSMITH_LOG_MARKER in r.getMessage()]


# ---------------------------------------------------------------------------
# 1. Every outcome is named, once, at a level that reaches CloudWatch.
# ---------------------------------------------------------------------------


def test_the_six_outcomes_are_distinct_tokens():
    """Six statuses, six strings. Two outcomes sharing a token would put us
    back to not being able to tell them apart in a log filter."""
    tokens = [
        graph.LANGSMITH_OFF,
        graph.LANGSMITH_ON,
        graph.LANGSMITH_ON_KEY_PRESET,
        graph.LANGSMITH_OFF_SSM_ERROR,
        graph.LANGSMITH_OFF_KEY_ABSENT,
        graph.LANGSMITH_OFF_KEY_REJECTED,
    ]
    assert len(set(tokens)) == 6, tokens


def test_tracing_not_requested_still_logs_a_status(caplog):
    """A function that never got LANGCHAIN_TRACING_V2 (the PostScoreFunction
    defect class) used to return in silence, indistinguishable from a
    container where this code never ran at all."""
    with caplog.at_level(logging.INFO):
        status = graph._configure_langsmith_tracing()
    assert status == graph.LANGSMITH_OFF
    assert len(_status_lines(caplog)) == 1


def test_a_rejected_key_logs_at_warning_and_names_the_http_status(caplog, monkeypatch):
    """This is the live failure. The log must carry the actual status code and
    the parameter to rotate — "(401/403)" does not tell you which happened, and
    a reader cannot act on it."""
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    with caplog.at_level(logging.INFO), \
         patch("ai_helper.get_param", return_value="lsv2_sk_dead"), \
         patch("httpx.get", return_value=_resp(403)):
        status = graph._configure_langsmith_tracing()
    assert status == graph.LANGSMITH_OFF_KEY_REJECTED
    lines = _status_lines(caplog)
    assert len(lines) == 1
    assert lines[0].levelno >= logging.WARNING
    message = lines[0].getMessage()
    assert "403" in message
    assert graph.LANGSMITH_KEY_PARAM in message


def test_an_absent_parameter_is_not_reported_as_a_rejected_key(caplog, monkeypatch):
    """An empty SSM value used to fall through the probe and be reported as
    "LangSmith rejected the API key", which sends the reader to rotate a key
    that isn't there. Different cause, different token."""
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    with caplog.at_level(logging.INFO), \
         patch("ai_helper.get_param", return_value=""), \
         patch("httpx.get", side_effect=AssertionError("must not probe an empty key")):
        status = graph._configure_langsmith_tracing()
    assert status == graph.LANGSMITH_OFF_KEY_ABSENT
    assert _status_lines(caplog)[0].levelno >= logging.WARNING


def test_an_ssm_failure_is_its_own_outcome(caplog, monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    with caplog.at_level(logging.INFO), \
         patch("ai_helper.get_param", side_effect=RuntimeError("AccessDenied")):
        status = graph._configure_langsmith_tracing()
    assert status == graph.LANGSMITH_OFF_SSM_ERROR
    line = _status_lines(caplog)[0]
    assert line.levelno >= logging.WARNING
    assert "AccessDenied" in line.getMessage()


def test_a_working_key_says_so_positively(caplog, monkeypatch):
    """The other half of rule 2: without a line for the success case, "tracing
    is live" is not something CloudWatch can confirm, only something nobody
    has disproved."""
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    with caplog.at_level(logging.INFO), \
         patch("ai_helper.get_param", return_value="lsv2_sk_good"), \
         patch("httpx.get", return_value=_resp(200)):
        status = graph._configure_langsmith_tracing()
    assert status == graph.LANGSMITH_ON
    assert len(_status_lines(caplog)) == 1
    assert graph.os.environ["LANGCHAIN_API_KEY"] == "lsv2_sk_good"


def test_a_preset_key_is_reported_as_unverified(caplog, monkeypatch):
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    monkeypatch.setenv("LANGCHAIN_API_KEY", "from-somewhere-else")
    with caplog.at_level(logging.INFO), \
         patch("ai_helper.get_param", side_effect=AssertionError("must not fetch")):
        status = graph._configure_langsmith_tracing()
    assert status == graph.LANGSMITH_ON_KEY_PRESET
    assert len(_status_lines(caplog)) == 1


@pytest.mark.parametrize("secret", ["lsv2_sk_shhh", "from-somewhere-else"])
def test_no_status_line_ever_contains_the_key(caplog, monkeypatch, secret):
    """The whole point of the SecureString is that it does not land in a log
    group with a 30-day retention and an IAM policy nobody audits."""
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    with caplog.at_level(logging.INFO), \
         patch("ai_helper.get_param", return_value=secret), \
         patch("httpx.get", return_value=_resp(403)):
        graph._configure_langsmith_tracing()
    assert _status_lines(caplog), "nothing was logged, so this would pass vacuously"
    for record in caplog.records:
        assert secret not in record.getMessage()


def test_the_status_is_decided_and_logged_once_per_container(caplog, monkeypatch):
    """Observed while exercising this against a local trace sink: a second call
    saw the key it had just exported into the environment and downgraded the
    status from `on` (probed, accepted) to `on_key_preset` (unprobed) — a worse
    answer than the one already established, plus a second log line. "Once per
    container" has to be a property of this function, not of the fact that
    _get_graph happens to be its only caller.
    """
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    with caplog.at_level(logging.INFO), \
         patch("ai_helper.get_param", return_value="lsv2_sk_good") as mk, \
         patch("httpx.get", return_value=_resp(200)) as probe:
        first = graph._configure_langsmith_tracing()
        second = graph._configure_langsmith_tracing()
    assert first == second == graph.LANGSMITH_ON
    assert len(_status_lines(caplog)) == 1, "logged the outcome more than once"
    assert mk.call_count == 1, "re-read the SecureString"
    assert probe.call_count == 1, "re-probed a credential already settled"


def test_the_status_can_be_re_resolved_on_purpose(caplog, monkeypatch):
    """The cache is a once-per-container guard, not a freeze — a caller that
    genuinely wants the question re-asked says so."""
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    with caplog.at_level(logging.INFO), \
         patch("ai_helper.get_param", return_value="lsv2_sk_good"), \
         patch("httpx.get", return_value=_resp(403)):
        graph._configure_langsmith_tracing()
        graph._configure_langsmith_tracing(force=True)
    assert len(_status_lines(caplog)) == 2


def test_the_resolved_status_is_readable_afterwards(monkeypatch):
    """Callers need the status without re-running the probe, so a per-run log
    line can say which trace_ids will and will not have traces."""
    assert graph.langsmith_status() == graph.LANGSMITH_UNRESOLVED
    monkeypatch.setenv("LANGCHAIN_TRACING_V2", "true")
    with patch("ai_helper.get_param", return_value="k"), \
         patch("httpx.get", return_value=_resp(403)):
        graph._configure_langsmith_tracing()
    assert graph.langsmith_status() == graph.LANGSMITH_OFF_KEY_REJECTED


# ---------------------------------------------------------------------------
# 2. The probe reports WHAT it saw, not just a boolean.
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("code", [401, 403])
def test_probe_refuses_only_on_an_explicit_refusal(code):
    with patch("httpx.get", return_value=_resp(code)):
        probe = graph._langsmith_probe("k")
    assert probe.accepted is False
    assert str(code) in probe.detail


@pytest.mark.parametrize("code", [200, 404, 429, 500, 503])
def test_probe_fails_open_on_anything_else(code):
    with patch("httpx.get", return_value=_resp(code)):
        assert graph._langsmith_probe("k").accepted is True


def test_probe_fails_open_when_the_call_raises_and_says_why():
    with patch("httpx.get", side_effect=OSError("dns")):
        probe = graph._langsmith_probe("k")
    assert probe.accepted is True
    assert "dns" in probe.detail


def test_probe_names_the_endpoint_it_asked(monkeypatch):
    """A key that is valid on eu. and refused on the default US host is a real
    failure mode, and unreadable unless the line says which host answered."""
    monkeypatch.setenv("LANGCHAIN_ENDPOINT", "https://eu.api.smith.langchain.com")
    with patch("httpx.get", return_value=_resp(403)):
        probe = graph._langsmith_probe("k")
    assert "eu.api.smith.langchain.com" in probe.detail


# ---------------------------------------------------------------------------
# 3. A CloudWatch line can be tied to a LangSmith run.
# ---------------------------------------------------------------------------


def test_each_run_logs_its_trace_id_with_the_tracing_status(caplog):
    """The join key. Without this line you have a trace_id in the database and
    a pile of [council] lines, and no way to say which run produced which."""
    with caplog.at_level(logging.INFO), \
         patch("agents.nodes.select_generators", return_value=[P1]), \
         patch("agents.nodes.call_one", return_value=CAND):
        out = graph.council_complete_langgraph("p", "s", "d", n_generators=1)
    run_lines = [
        r.getMessage() for r in caplog.records
        if out["trace_id"] in r.getMessage() and "langsmith=" in r.getMessage()
    ]
    assert run_lines, "no line ties this run's trace_id to the tracing status"


def test_the_invoke_config_carries_the_trace_id_as_run_metadata():
    """trace_id is logged to CloudWatch, so it has to be findable in LangSmith.
    thread_id lands in metadata via LangGraph, but relying on that is relying
    on an implementation detail of somebody else's library — set it
    explicitly."""
    captured = {}

    class _Graph:
        def invoke(self, state, config=None):
            captured["config"] = config
            return {"winner": CAND}

    with patch.object(graph, "_get_graph", return_value=_Graph()):
        out = graph.council_complete_langgraph("p", "s", "d", n_generators=1)
    metadata = captured["config"]["metadata"]
    assert metadata["trace_id"] == out["trace_id"]
    assert captured["config"]["configurable"]["thread_id"] == out["trace_id"]


def test_fan_out_passes_the_trace_id_to_every_generate_branch():
    """generate_node gets a Send payload, not the graph state, so without this
    its own log lines cannot name the run they belong to."""
    sends = graph._fan_out({
        "prompt": "p",
        "generators": [P1, {"name": "q/b", "model": "qwen-plus"}],
        "trace_id": "TID",
    })
    assert len(sends) == 2
    for send in sends:
        assert send.arg["trace_id"] == "TID"


def test_plan_node_logs_the_trace_id(caplog):
    with caplog.at_level(logging.INFO), \
         patch("agents.nodes.select_generators", return_value=[P1]):
        nodes.plan_node({"n_generators": 1, "trace_id": "TID-plan"})
    assert any("TID-plan" in r.getMessage() for r in caplog.records)


def test_generate_node_fallback_logs_the_trace_id(caplog):
    calls = []

    def _call_one(provider, *a, **kw):
        calls.append(provider["name"])
        return None if len(calls) == 1 else CAND

    with caplog.at_level(logging.INFO), \
         patch("agents.nodes.call_one", side_effect=_call_one), \
         patch("agents.nodes.family_of", side_effect=lambda p: p.get("name", "").split("/")[0]), \
         patch("agents.nodes.all_providers", return_value=[{"name": "qwen/b", "model": "m2"}]):
        nodes.generate_node({"provider": P1, "prompt": "p", "trace_id": "TID-gen"})
    assert any("TID-gen" in r.getMessage() for r in caplog.records)


def test_quality_gate_repair_exhaustion_logs_the_trace_id(caplog):
    with caplog.at_level(logging.INFO):
        route = nodes.quality_gate({
            "guard_report": {"passed": False},
            "repair_attempts": 2,
            "trace_id": "TID-gate",
        })
    assert route == "finalize"
    assert any("TID-gate" in r.getMessage() for r in caplog.records)


# ---------------------------------------------------------------------------
# 4. The status line has to actually reach CloudWatch on the deployed functions.
# ---------------------------------------------------------------------------


def _template_resources():
    """template.yaml with CFN's short tags stubbed to plain values.

    Subclassing SafeLoader keeps this safe: the added constructors return only
    scalars, lists and dicts. Same approach as test_council_engine_parity.py.
    """
    class Loader(yaml.SafeLoader):
        pass

    def passthrough(loader, node):
        if isinstance(node, yaml.ScalarNode):
            return loader.construct_scalar(node)
        if isinstance(node, yaml.SequenceNode):
            return loader.construct_sequence(node)
        return loader.construct_mapping(node)

    for tag in ("!Ref", "!Sub", "!GetAtt", "!Join", "!Select", "!Split",
                "!FindInMap", "!ImportValue", "!Equals", "!If", "!Not", "!Condition"):
        Loader.add_constructor(tag, passthrough)
    return yaml.load(TEMPLATE.read_text(), Loader=Loader)["Resources"]


def _council_functions():
    """Functions that run the council, by the knob that selects its engine."""
    out = {}
    for name, res in _template_resources().items():
        if res.get("Type") != "AWS::Serverless::Function":
            continue
        props = res.get("Properties", {})
        variables = (props.get("Environment") or {}).get("Variables") or {}
        if "COUNCIL_ENGINE" in variables:
            out[name] = props
    return out


def test_the_council_function_scan_finds_something():
    assert len(_council_functions()) >= 4, "the scan is broken; the rest is vacuous"


def test_every_council_function_requests_tracing():
    missing = [
        name for name, props in _council_functions().items()
        if ((props.get("Environment") or {}).get("Variables") or {}).get(
            "LANGCHAIN_TRACING_V2") != "true"
    ]
    assert not missing, (
        f"{missing} run the council with tracing off, so their runs are "
        "invisible in LangSmith however good the key is"
    )


def test_every_council_function_reports_into_one_project():
    projects = {
        name: ((props.get("Environment") or {}).get("Variables") or {}).get("LANGCHAIN_PROJECT")
        for name, props in _council_functions().items()
    }
    assert None not in projects.values(), projects
    assert len(set(projects.values())) == 1, (
        f"council runs are split across LangSmith projects: {projects}"
    )


def test_every_council_handler_lets_info_records_out():
    """The `on` / `off` status lines are INFO. AWS's Lambda runtime leaves the
    root logger at WARNING, so an INFO line from agents.graph only reaches
    CloudWatch because the handler module raised the root level first. That is
    load-bearing, not incidental: without it, a healthy container logs nothing
    and looks exactly like one where this code never ran.
    """
    silent = []
    for name, props in _council_functions().items():
        handler = props.get("Handler", "")
        code_uri = props.get("CodeUri", "")
        if not handler or not isinstance(code_uri, str):
            continue
        src = ROOT / code_uri / f"{handler.rsplit('.', 1)[0]}.py"
        if not src.exists():
            continue
        text = src.read_text()
        if not re.search(r"^logger\s*=\s*logging\.getLogger\(\)\s*$", text, re.M) or \
                "logger.setLevel(logging.INFO)" not in text:
            silent.append(name)
    assert not silent, (
        f"{silent} do not raise the root logger to INFO, so agents.graph's "
        "INFO status line never reaches CloudWatch from them"
    )
