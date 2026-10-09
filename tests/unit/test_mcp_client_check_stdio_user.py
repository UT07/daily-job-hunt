"""mcp_client_check.py --stdio fails up front, by name, without NAUKRIBABA_MCP_USER_ID.

The stdio server refuses to start without that variable (mcp_server/server.py
STDIO_USER_ENV, since 2026-10-08). The check script used to spawn it anyway,
so the operator saw the MCP client's view of a child that exited at once -- a
closed pipe -- and nothing naming the missing variable. It now refuses before
spawning anything, and before the job-hash lookup that would hit Supabase.

load_env is stubbed so a developer's real .env cannot make this pass or fail.
"""
import importlib.util
import pathlib

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
VAR = "NAUKRIBABA_MCP_USER_ID"


@pytest.fixture
def script(monkeypatch):
    spec = importlib.util.spec_from_file_location(
        "mcp_client_check_under_test", ROOT / "scripts" / "mcp_client_check.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    calls = []
    monkeypatch.setattr(mod, "load_env", lambda: None)
    monkeypatch.setattr(mod, "first_job_hash", lambda: calls.append("lookup") or None)

    async def _stdio(job_hash, score=False):
        calls.append("stdio")

    async def _sse(job_hash, score=False):
        calls.append("sse")

    monkeypatch.setattr(mod, "over_stdio", _stdio)
    monkeypatch.setattr(mod, "over_sse", _sse)
    mod.calls = calls
    return mod


def _run(mod, monkeypatch, *argv):
    monkeypatch.setattr("sys.argv", ["mcp_client_check.py", *argv])
    mod.main()


def test_the_variable_name_matches_the_server():
    from mcp_server.server import STDIO_USER_ENV
    assert STDIO_USER_ENV == VAR


@pytest.mark.parametrize("argv", [("--stdio",), ()])
def test_stdio_without_the_user_id_exits_naming_it(script, monkeypatch, argv):
    monkeypatch.delenv(VAR, raising=False)
    with pytest.raises(SystemExit) as exc:
        _run(script, monkeypatch, *argv)
    assert VAR in str(exc.value.code)
    assert script.calls == [], f"work started before the check: {script.calls}"


def test_a_blank_user_id_is_treated_as_unset(script, monkeypatch):
    monkeypatch.setenv(VAR, "   ")
    with pytest.raises(SystemExit) as exc:
        _run(script, monkeypatch, "--stdio")
    assert VAR in str(exc.value.code)


def test_sse_alone_does_not_need_it(script, monkeypatch):
    # SSE acts as the JWT's `sub`; the stdio variable is irrelevant there.
    monkeypatch.delenv(VAR, raising=False)
    _run(script, monkeypatch, "--sse")
    assert script.calls == ["lookup", "sse"]


def test_with_the_user_id_stdio_runs(script, monkeypatch):
    monkeypatch.setenv(VAR, "00000000-0000-0000-0000-000000000001")
    _run(script, monkeypatch, "--stdio")
    assert script.calls == ["lookup", "stdio"]
