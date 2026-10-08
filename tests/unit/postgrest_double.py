"""An in-memory stand-in for the supabase-py query builder that APPLIES filters.

Why this exists rather than a MagicMock: a MagicMock accepts `.eq("user_id", x)`
and ignores it, so a test of user scoping written against one passes whether or
not the scope is there (CLAUDE.md #5, #6). This double does what PostgREST does
with each call the app makes, and refuses calls it does not model rather than
silently accepting them:

* every `eq` / `in_` / `is_` / `not_.is_` narrows the rows a statement touches;
* `update` and `delete` with no filter at all raise, as Supabase's
  pg-safeupdate does ("UPDATE requires a WHERE clause");
* `update` / `insert` / `delete` return the affected rows (supabase-py's
  default `returning=representation`), so `bool(res.data)` means "a row was
  written", exactly as in production;
* `insert` enforces the table's primary key and raises on a duplicate;
* `range(lo, hi)` is inclusive and pages are capped at 1000, like PostgREST;
* an unknown method raises AttributeError instead of returning a mock.

`test_postgrest_double.py` proves each of these before any other test leans on
them.
"""
from __future__ import annotations

import copy
from types import SimpleNamespace

PRIMARY_KEYS = {
    "jobs": ("job_id", "user_id"),
}

PAGE_CAP = 1000


class DuplicateKey(Exception):
    """What PostgREST answers (409 / 23505) for a primary-key collision."""


class MissingWhere(Exception):
    """pg-safeupdate: an UPDATE or DELETE with no filter is rejected."""


class _Not:
    def __init__(self, q):
        self._q = q

    def is_(self, col, val):
        target = None if val in (None, "null") else val
        self._q._filters.append(lambda r: r.get(col) is not target if target is None
                                else r.get(col) != target)
        return self._q


class Query:
    def __init__(self, db, table):
        self._db = db
        self._table = table
        self._op = "select"
        self._payload = None
        self._filters = []
        self._order = None
        self._limit = None
        self._range = None
        self._single = None

    # ── statement kind ────────────────────────────────────────────
    def select(self, *_cols, count=None):
        self._op = "select"
        return self

    def update(self, values):
        self._op, self._payload = "update", dict(values)
        return self

    def insert(self, row):
        self._op, self._payload = "insert", row
        return self

    def delete(self):
        self._op = "delete"
        return self

    # ── filters ───────────────────────────────────────────────────
    def eq(self, col, val):
        self._filters.append(lambda r: r.get(col) == val)
        return self

    def in_(self, col, vals):
        vals = list(vals)
        self._filters.append(lambda r: r.get(col) in vals)
        return self

    def is_(self, col, val):
        target = None if val in (None, "null") else val
        self._filters.append(lambda r: r.get(col) is target if target is None
                             else r.get(col) == target)
        return self

    @property
    def not_(self):
        return _Not(self)

    # ── shaping ───────────────────────────────────────────────────
    def order(self, col, desc=False):
        self._order = (col, desc)
        return self

    def limit(self, n):
        self._limit = n
        return self

    def range(self, lo, hi):
        self._range = (lo, hi)
        return self

    def maybe_single(self):
        self._single = "maybe"
        return self

    def single(self):
        self._single = "one"
        return self

    # ── execution ─────────────────────────────────────────────────
    def _matches(self, row):
        return all(f(row) for f in self._filters)

    def execute(self):
        rows = self._db.tables.setdefault(self._table, [])
        self._db.log.append((self._table, self._op, len(self._filters)))

        if self._op == "insert":
            new = self._payload if isinstance(self._payload, list) else [self._payload]
            pk = PRIMARY_KEYS.get(self._table)
            for r in new:
                if pk and any(all(e.get(k) == r.get(k) for k in pk) for e in rows):
                    raise DuplicateKey(f"duplicate key value violates unique constraint {pk}")
            for r in new:
                rows.append(copy.deepcopy(r))
            return SimpleNamespace(data=copy.deepcopy(new), count=None)

        if self._op in ("update", "delete") and not self._filters:
            raise MissingWhere(f"{self._op.upper()} requires a WHERE clause")

        hit = [r for r in rows if self._matches(r)]

        if self._op == "update":
            for r in hit:
                r.update(self._payload)
            return SimpleNamespace(data=copy.deepcopy(hit), count=None)

        if self._op == "delete":
            self._db.tables[self._table] = [r for r in rows if not self._matches(r)]
            return SimpleNamespace(data=copy.deepcopy(hit), count=None)

        if self._order:
            col, desc = self._order
            hit = sorted(hit, key=lambda r: (r.get(col) is None, r.get(col)), reverse=desc)
        if self._range:
            lo, hi = self._range
            hit = hit[lo:hi + 1]
        hit = hit[:PAGE_CAP]
        if self._limit is not None:
            hit = hit[: self._limit]
        if self._single == "maybe":
            if len(hit) > 1:
                raise ValueError("maybe_single matched more than one row")
            return SimpleNamespace(data=copy.deepcopy(hit[0]) if hit else None, count=None)
        if self._single == "one":
            if len(hit) != 1:
                raise ValueError(f"single matched {len(hit)} rows")
            return SimpleNamespace(data=copy.deepcopy(hit[0]), count=None)
        return SimpleNamespace(data=copy.deepcopy(hit), count=len(hit))


class FakeSupabase:
    """`.client.table(name)` like SupabaseClient; rows live in `.tables`."""

    def __init__(self, tables: dict | None = None):
        self.tables = {k: [dict(r) for r in v] for k, v in (tables or {}).items()}
        self.log: list = []
        self.client = self

    def table(self, name):
        return Query(self, name)

    @staticmethod
    def _all_rows(build_query, page: int = PAGE_CAP) -> list:
        # The production walker, not a copy of it: a second implementation here
        # would let the test and the code drift apart unnoticed.
        from db_client import SupabaseClient
        return SupabaseClient._all_rows(build_query, page)

    def rows(self, table, **where):
        return [r for r in self.tables.get(table, [])
                if all(r.get(k) == v for k, v in where.items())]
