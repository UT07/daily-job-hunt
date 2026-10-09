"""`pipeline_tasks` and `scrape_runs` must not be readable or writable by the
public API keys.

Audited 2026-10-08, in supabase/migrations/00000000000000_initial_schema.sql:

- `CREATE POLICY "Service role full access" ON pipeline_tasks USING (true)`
  names neither a command nor a role, so Postgres applies it to ALL commands
  for PUBLIC: the name says service role, the policy says everyone. With
  `GRANT ALL ... TO anon` beside it, anyone holding the anon key shipped in
  the web bundle could read every user's task payloads and results, and
  rewrite them.
- `scrape_runs` never had RLS enabled at all, and is also `GRANT ALL` to anon.

Neither table is touched by the frontend (asserted below), and every backend
reader uses the service key, which bypasses RLS. So the correct end state is
RLS on, no permissive policy, and no grant to anon or authenticated.

This replays every migration in the order the Supabase CLI applies them
(filename order) and asserts the END state, so a later migration that
re-grants or re-adds the policy fails here.
"""
from __future__ import annotations

import pathlib
import re

import pytest

REPO = pathlib.Path(__file__).resolve().parents[2]
MIGRATIONS = REPO / "supabase" / "migrations"
TABLES = ("pipeline_tasks", "scrape_runs")
ALL_PRIVS = frozenset({"select", "insert", "update", "delete", "truncate", "references", "trigger"})

_TBL = r'(?:"?public"?\.)?"?(\w+)"?'


def _statements(sql: str) -> list[str]:
    sql = re.sub(r"--[^\n]*", "", sql)
    return [s.strip() for s in sql.split(";") if s.strip()]


def _privs(text: str) -> set[str]:
    text = text.lower().replace("privileges", "")
    if text.strip() == "all":
        return set(ALL_PRIVS)
    return {p.strip() for p in text.split(",") if p.strip()}


def _roles(text: str) -> set[str]:
    return {r.strip().strip('"').lower() for r in text.split(",") if r.strip()}


def replay(files: list[pathlib.Path]) -> dict:
    """End state per table: rls flag, policies {name: (cmd, roles, using)}, grants {role: privs}."""
    state = {t: {"rls": False, "policies": {}, "grants": {}} for t in TABLES}
    for path in files:
        for stmt in _statements(path.read_text()):
            flat = " ".join(stmt.split())
            m = re.match(rf'create policy "?([^"]+?)"? on {_TBL}(.*)$', flat, re.I)
            if m and m.group(2).lower() in state:
                rest = m.group(3)
                cmd = re.search(r"\bfor (\w+)", rest, re.I)
                to = re.search(r"\bto (.+?)(?= using| with check|$)", rest, re.I)
                using = re.search(r"\busing \((.*)\)", rest, re.I)
                state[m.group(2).lower()]["policies"][m.group(1)] = (
                    (cmd.group(1).lower() if cmd else "all"),
                    (_roles(to.group(1)) if to else {"public"}),
                    (using.group(1).strip().strip("()").strip().lower() if using else ""),
                )
                continue
            m = re.match(rf'drop policy (?:if exists )?"?([^"]+?)"? on {_TBL}$', flat, re.I)
            if m and m.group(2).lower() in state:
                state[m.group(2).lower()]["policies"].pop(m.group(1), None)
                continue
            m = re.match(rf"alter table (?:only )?(?:if exists )?{_TBL} (enable|disable) row level security$", flat, re.I)
            if m and m.group(1).lower() in state:
                state[m.group(1).lower()]["rls"] = m.group(2).lower() == "enable"
                continue
            m = re.match(rf"grant (.+?) on (?:table )?{_TBL} to (.+)$", flat, re.I)
            if m and m.group(2).lower() in state:
                for role in _roles(m.group(3)):
                    state[m.group(2).lower()]["grants"].setdefault(role, set()).update(_privs(m.group(1)))
                continue
            m = re.match(rf"revoke (.+?) on (?:table )?{_TBL} from (.+)$", flat, re.I)
            if m and m.group(2).lower() in state:
                for role in _roles(m.group(3)):
                    state[m.group(2).lower()]["grants"].setdefault(role, set()).difference_update(_privs(m.group(1)))
    return state


def _all_migrations() -> list[pathlib.Path]:
    return sorted(MIGRATIONS.glob("*.sql"), key=lambda p: p.name)


def _is_permissive_to_non_service(policy) -> bool:
    _cmd, roles, using = policy
    return using in ("true", "") and roles != {"service_role"}


@pytest.fixture(scope="module")
def end_state():
    return replay(_all_migrations())


@pytest.mark.parametrize("table", TABLES)
def test_rls_is_enabled(end_state, table):
    assert end_state[table]["rls"], f"{table} has RLS disabled after the latest migration"


@pytest.mark.parametrize("table", TABLES)
def test_no_policy_opens_the_table_to_everyone(end_state, table):
    bad = {n: p for n, p in end_state[table]["policies"].items() if _is_permissive_to_non_service(p)}
    assert not bad, f"{table}: USING (true) policies not restricted to service_role: {bad}"


@pytest.mark.parametrize("table", TABLES)
@pytest.mark.parametrize("role", ["anon", "public"])
def test_anon_has_no_grant(end_state, table, role):
    assert not end_state[table]["grants"].get(role), (
        f"{role} still holds {sorted(end_state[table]['grants'][role])} on {table}"
    )


@pytest.mark.parametrize("table", TABLES)
def test_authenticated_has_no_grant_because_the_frontend_never_reads_it(end_state, table):
    """If the web app ever needs these tables directly, add an
    `auth.uid() = user_id` policy for authenticated in a new migration and
    change this test -- do not re-grant without a scoped policy."""
    assert not end_state[table]["grants"].get("authenticated"), end_state[table]["grants"]["authenticated"]


@pytest.mark.parametrize("table", TABLES)
def test_the_frontend_does_not_query_the_table_directly(table):
    hits = [
        str(p.relative_to(REPO))
        for p in (REPO / "web" / "src").rglob("*")
        if p.is_file() and p.suffix in {".js", ".jsx", ".ts", ".tsx"} and table in p.read_text(errors="ignore")
    ]
    assert not hits, f"web/src references {table}; it now needs a user-scoped policy: {hits}"


@pytest.mark.parametrize("table", TABLES)
def test_service_role_keeps_access(end_state, table):
    assert end_state[table]["grants"].get("service_role", set()) >= ALL_PRIVS


# CLAUDE.md rule 6: the replayer must see the defect it exists to catch.


def test_the_replayer_detects_the_original_defect():
    initial = replay([MIGRATIONS / "00000000000000_initial_schema.sql"])
    pt = initial["pipeline_tasks"]
    assert pt["policies"]["Service role full access"] == ("all", {"public"}, "true")
    assert any(_is_permissive_to_non_service(p) for p in pt["policies"].values())
    assert pt["grants"]["anon"] == set(ALL_PRIVS)
    assert initial["scrape_runs"]["rls"] is False
    assert initial["scrape_runs"]["grants"]["anon"] == set(ALL_PRIVS)


def test_the_replayer_understands_drop_revoke_and_scoped_roles(tmp_path):
    f = tmp_path / "x.sql"
    f.write_text(
        'CREATE POLICY "p" ON public.pipeline_tasks USING (true);\n'
        'CREATE POLICY "svc" ON public.pipeline_tasks FOR ALL TO service_role USING (true);\n'
        'GRANT ALL ON TABLE public.pipeline_tasks TO anon, authenticated;\n'
        'DROP POLICY IF EXISTS "p" ON public.pipeline_tasks;\n'
        'REVOKE ALL ON TABLE public.pipeline_tasks FROM anon;\n'
    )
    s = replay([f])["pipeline_tasks"]
    assert set(s["policies"]) == {"svc"}
    assert not _is_permissive_to_non_service(s["policies"]["svc"])
    assert not s["grants"]["anon"]
    assert s["grants"]["authenticated"] == set(ALL_PRIVS)
