"""Every NaukriBaba route a script calls must still exist in app.py.

POST /api/tailor was removed on 2026-10-08 and scripts/verify_phase1.py kept
calling it: a verification script that hits a 404 and reports one FAIL among
dozens of passes is easy to read past. This test walks every `/api/...`
literal in scripts/ and requires it to match a live route, so the next route
removal fails CI here instead of on an operator's terminal.

Third-party APIs that also live under `/api/` (OpenRouter, PostHog, LinkedIn)
are excluded by prefix; the list is explicit so a new external API has to be
named rather than silently skipped.
"""
import re
from pathlib import Path

import app as app_module

ROOT = Path(__file__).resolve().parents[2]
EXTERNAL_PREFIXES = ("/api/v1/", "/api/projects/", "/api/jobPosting/")
_LITERAL = re.compile(r"/api/[A-Za-z0-9_/{}.\-]*")


def _pattern(path: str) -> re.Pattern:
    parts = re.split(r"\{[^}]*\}", path.rstrip("/"))
    return re.compile("^" + "[^/]+".join(re.escape(p) for p in parts) + "/?$")


def _live_routes() -> list[re.Pattern]:
    return [_pattern(r.path) for r in app_module.app.routes if getattr(r, "path", "").startswith("/api/")]


def _script_references() -> list[tuple[str, str]]:
    refs = []
    for path in sorted((ROOT / "scripts").rglob("*.py")):
        # Comments may name removed routes as history; only code is checked.
        code = "\n".join(line.split("#", 1)[0] for line in path.read_text().splitlines())
        for lit in _LITERAL.findall(code):
            if lit.startswith(EXTERNAL_PREFIXES):
                continue
            refs.append((path.relative_to(ROOT).as_posix(), lit))
    return refs


def test_the_scan_sees_script_references():
    # Without this the test below passes on an empty population (CLAUDE.md #2).
    assert len(_script_references()) >= 5


def test_every_script_route_exists():
    live = _live_routes()
    dead = [(f, lit) for f, lit in _script_references()
            if not any(p.match(lit.rstrip("/")) for p in live)]
    assert dead == [], f"scripts call routes app.py no longer serves: {dead}"
