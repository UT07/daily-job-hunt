"""Contract test: every URL the frontend hits must exist as a backend route.

This catches the class of bug where a typo'd or stale URL reaches production
because nothing in CI cross-checks the two surfaces. Example: a previous
ResumeEditor.jsx hit `/api/resume/upload-pdf` but the backend declares
`/api/resumes/upload`.

How it works:
  1. Walk app.py with regex to find every @app.<verb>("...") declaration.
     Normalize FastAPI path params {id} to a placeholder.
  2. Walk web/src/**/*.{js,jsx,ts,tsx} for string literals starting with
     `/api/`. Normalize JS template-literal segments ${...} and `:id` to the
     same placeholder.
  3. Fail if any frontend URL doesn't match a backend route.

Known-bug exemptions live in KNOWN_FRONTEND_BUGS so this test passes today
while the orchestrator tracks the real fix on a separate branch.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
APP_PY = REPO_ROOT / "app.py"
WEB_SRC = REPO_ROOT / "web" / "src"

# Regex to find FastAPI route decorators: @app.get("/api/...")
BACKEND_ROUTE_RE = re.compile(
    r'@app\.(?:get|post|put|patch|delete)\(\s*["\'](?P<path>/[^"\']*)["\']'
)

# Regex to find frontend API URLs in string literals or template literals.
# Captures /api/... up to the next quote/backtick. The character class allows
# parentheses so template literals like ${encodeURIComponent(jobId)} stay
# intact for normalization.
FRONTEND_URL_RE = re.compile(r'[\'"`](/api/[^\'"`\s]+)')

# FastAPI path parameter pattern: {job_id} or {task_id}
BACKEND_PARAM_RE = re.compile(r"\{[^}]+\}")
# Frontend path patterns to normalize: ${anything} (template literal) or :id
FRONTEND_PARAM_RE = re.compile(r"\$\{[^}]+\}|:[A-Za-z_][A-Za-z0-9_]*")

# Known frontend bugs the orchestrator is tracking. Each entry is a frontend
# URL (already normalized) and the reason it's exempt. New entries should
# always link to a tracking branch/issue.
KNOWN_FRONTEND_BUGS: dict[str, str] = {
    # TYPO: should be /api/resumes/upload (plural). Source:
    # web/src/components/ResumeEditor.jsx — handleUploadPdf calls
    # apiUpload('/api/resume/upload-pdf'). Backend declares /api/resumes/upload.
    # Cluster B is fixing this on fix/audit-cluster-b-ui-hardening; remove this
    # entry once that branch merges into main.
    "/api/resume/upload-pdf": "tracked: cluster-b fix in flight",
}


def _normalize(path: str) -> str:
    """Normalize a route path so frontend and backend forms compare equal.

    Strips query strings, replaces every path-parameter spelling with a single
    placeholder, and trims trailing slashes.
    """
    if "?" in path:
        path = path.split("?", 1)[0]
    # Frontend first: ${...} contains a {...} substring, so collapsing the
    # backend form first would leave a stray '$' behind.
    path = FRONTEND_PARAM_RE.sub("<param>", path)
    path = BACKEND_PARAM_RE.sub("<param>", path)
    if len(path) > 1 and path.endswith("/"):
        path = path[:-1]
    return path


def _backend_routes() -> set[str]:
    text = APP_PY.read_text()
    return {_normalize(m.group("path")) for m in BACKEND_ROUTE_RE.finditer(text)}


def _is_comment_line(line: str) -> bool:
    """Return True if `line` looks like a JS line/block comment.

    JSDoc (` * ...`) and `// ...` lines often contain example URLs that
    aren't real callsites — skip them so the contract check focuses on
    real code.
    """
    stripped = line.lstrip()
    return stripped.startswith("//") or stripped.startswith("*")


def _frontend_urls() -> dict[str, list[str]]:
    """Return {normalized_url: [source_files...]}."""
    found: dict[str, list[str]] = {}
    for ext in ("*.js", "*.jsx", "*.ts", "*.tsx"):
        for path in WEB_SRC.rglob(ext):
            text = path.read_text(errors="replace")
            for raw_line in text.splitlines():
                if _is_comment_line(raw_line):
                    continue
                for m in FRONTEND_URL_RE.finditer(raw_line):
                    normalized = _normalize(m.group(1))
                    found.setdefault(normalized, []).append(
                        str(path.relative_to(REPO_ROOT))
                    )
    return found


def test_backend_routes_discovered() -> None:
    """Sanity guard: regex extraction must find a reasonable number of routes."""
    routes = _backend_routes()
    assert len(routes) >= 30, (
        f"Expected at least 30 backend routes, found {len(routes)}. "
        "BACKEND_ROUTE_RE may be broken."
    )


def test_frontend_urls_discovered() -> None:
    """Sanity guard: frontend extraction must find some URLs."""
    urls = _frontend_urls()
    assert len(urls) >= 10, (
        f"Expected at least 10 frontend URLs, found {len(urls)}. "
        "FRONTEND_URL_RE may be broken."
    )


def test_every_frontend_url_matches_a_backend_route() -> None:
    """No frontend URL may reference a backend route that doesn't exist."""
    backend = _backend_routes()
    frontend = _frontend_urls()

    mismatches: list[str] = []
    for url, sources in sorted(frontend.items()):
        if url in backend:
            continue
        if url in KNOWN_FRONTEND_BUGS:
            continue
        mismatches.append(f"{url}\n      sources: {', '.join(sorted(set(sources)))}")

    assert not mismatches, (
        "The following frontend URLs do not match any backend route in app.py:"
        "\n  - " + "\n  - ".join(mismatches)
        + "\n\nFix: either correct the frontend URL, add the missing backend "
        "route, or (if the mismatch is being tracked elsewhere) add it to "
        "KNOWN_FRONTEND_BUGS in this test with a reason."
    )
