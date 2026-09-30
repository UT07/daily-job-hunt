""""Deployed" has to mean "a client can connect".

Measured live on 2026-09-30 against
https://paie9w92c1.execute-api.eu-west-1.amazonaws.com/prod/mcp/sse:

    no Authorization header   -> 401 {"detail":"Missing authorization header"}
    Authorization: Bearer nope -> 401 {"detail":"Invalid or expired token"}
    a valid HS256 Supabase JWT -> 421 "Invalid Host header"     <-- the defect

The 421 comes from the MCP SDK's own DNS-rebinding protection
(mcp/server/transport_security.py). `FastMCP()` defaults `host="127.0.0.1"`,
and FastMCP treats a localhost host as "auto-enable DNS rebinding protection"
with `allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"]` — nothing else.
So the JWT gate was never what kept clients out: the transport rejected every
request whose Host was not localhost, which is every request that reaches API
Gateway. That is why the tool list has never been fetched by anything.

The fix is configuration, not a hole: DNS-rebinding protection stays ON and
the deployed host is named explicitly, from the environment, so the allowed
set is whatever the stack actually serves (template.yaml passes
MCP_ALLOWED_HOSTS: !Sub "${HttpApi}.execute-api.${AWS::Region}.amazonaws.com").
"""
from __future__ import annotations

import os
from unittest.mock import patch

import pytest

from mcp_server import server

GATEWAY_HOST = "paie9w92c1.execute-api.eu-west-1.amazonaws.com"


def _security(**env):
    with patch.dict(os.environ, env, clear=False):
        return server.build_server().settings.transport_security


def test_dns_rebinding_protection_is_never_silently_switched_off():
    """Turning the check off would also "fix" the 421. It is not the fix."""
    settings = _security(MCP_ALLOWED_HOSTS=GATEWAY_HOST)
    assert settings is not None
    assert settings.enable_dns_rebinding_protection is True


def test_the_deployed_host_is_allowed_when_the_environment_names_it():
    settings = _security(MCP_ALLOWED_HOSTS=GATEWAY_HOST)
    assert GATEWAY_HOST in settings.allowed_hosts
    assert f"https://{GATEWAY_HOST}" in settings.allowed_origins


def test_localhost_stays_allowed_so_a_local_client_keeps_working():
    settings = _security(MCP_ALLOWED_HOSTS=GATEWAY_HOST)
    for host in ("127.0.0.1:*", "localhost:*", "[::1]:*"):
        assert host in settings.allowed_hosts


def test_several_hosts_can_be_named_at_once():
    settings = _security(MCP_ALLOWED_HOSTS=f"{GATEWAY_HOST}, api.example.test")
    assert GATEWAY_HOST in settings.allowed_hosts
    assert "api.example.test" in settings.allowed_hosts


def test_an_unnamed_host_is_still_rejected():
    settings = _security(MCP_ALLOWED_HOSTS=GATEWAY_HOST)
    assert "evil.example" not in settings.allowed_hosts


def test_with_no_environment_variable_only_localhost_is_allowed():
    """Fail closed. An empty MCP_ALLOWED_HOSTS must not mean "allow anything"."""
    with patch.dict(os.environ, {}, clear=False):
        os.environ.pop("MCP_ALLOWED_HOSTS", None)
        settings = server.build_server().settings.transport_security
    assert settings.enable_dns_rebinding_protection is True
    assert "127.0.0.1:*" in settings.allowed_hosts
    assert not [h for h in settings.allowed_hosts if "amazonaws.com" in h]


def test_the_sdk_would_reject_the_gateway_host_without_this_configuration():
    """Proves the double is sound (CLAUDE.md rule 6).

    If the SDK's validator accepted an arbitrary host anyway, every assertion
    above would pass for the wrong reason and the live 421 would be
    unexplained. So check the validator itself, with the SDK's own defaults.
    """
    from mcp.server.transport_security import TransportSecurityMiddleware, TransportSecuritySettings

    localhost_only = TransportSecurityMiddleware(
        TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"],
        )
    )
    assert localhost_only._validate_host(GATEWAY_HOST) is False

    configured = TransportSecurityMiddleware(_security(MCP_ALLOWED_HOSTS=GATEWAY_HOST))
    assert configured._validate_host(GATEWAY_HOST) is True
    assert configured._validate_host("127.0.0.1:8000") is True


def test_template_passes_the_deployed_host_to_the_api_lambda():
    """CLAUDE.md "audit all deploy paths": the env var has to be set somewhere.

    Configuring it in code and forgetting the template is the same defect as
    a package that is in the layer and not in the image.
    """
    import pathlib

    repo = pathlib.Path(__file__).resolve().parents[2]
    template = (repo / "template.yaml").read_text()
    assert "MCP_ALLOWED_HOSTS:" in template, (
        "JobHuntApi must receive MCP_ALLOWED_HOSTS or the deployed transport "
        "keeps answering 421 Invalid Host header"
    )
    assert "execute-api" in template.split("MCP_ALLOWED_HOSTS:")[1].splitlines()[0]


@pytest.mark.parametrize("path", ["/sse", "/messages"])
def test_the_sse_routes_are_still_the_ones_documented(path):
    """Mounted under /mcp by app.py, so these become /mcp/sse and /mcp/messages/.

    `/messages` is registered as a Starlette Mount, whose `.path` drops the
    trailing slash the client actually posts to; mcp 1.30 builds the URI it
    advertises in the SSE `endpoint` event from `scope["root_path"]` plus the
    endpoint string, so the mount prefix is included and a client posts to
    /mcp/messages/?session_id=... — docs/mcp-client-setup.md documents these
    two paths and this pins them.
    """
    routes = {getattr(r, "path", None) for r in server.build_server().sse_app().routes}
    assert path in routes
