# tests/e2e_live — live end-to-end browser suite

A real Chromium drives the **deployed** frontend, which talks to the **deployed**
API and the **production** Supabase. Unlike `tests/e2e/` (which stubs every
response), this suite can catch real-system failures: a 503 from API Gateway,
a row that never persisted, a PDF link that returns XML.

Only PostHog analytics requests are stubbed (so a test account does not pollute
product analytics). Nothing else is.

## Run

```bash
scripts/run_e2e_live.sh                 # full journey, ~25-35 min
scripts/run_e2e_live.sh -k dashboard    # a subset (later modules need earlier state)
E2E_LIVE_HEADED=1 scripts/run_e2e_live.sh
```

The script derives the configuration explicitly and passes it as environment
variables; the suite never reads `.env` and never imports `app.py`.

| Variable | Source used by the script |
|---|---|
| `E2E_LIVE_SITE_URL` | the production origin in `app.py`'s CORS `allow_origins` |
| `E2E_LIVE_API_URL` | `VITE_API_URL` in `web/.env.production` |
| `E2E_LIVE_SUPABASE_URL` | `SUPABASE_URL` in `.env` (checked equal to `VITE_SUPABASE_URL`) |
| `E2E_LIVE_SUPABASE_ANON_KEY` | `VITE_SUPABASE_ANON_KEY` in `web/.env.production` |
| `E2E_LIVE_SUPABASE_SERVICE_KEY` | `SUPABASE_SERVICE_KEY` in `.env` (account setup + cleanup only) |
| `E2E_LIVE_S3_BUCKET` | `app.py`'s `S3_BUCKET_NAME` default (cleanup only; uses your AWS credentials) |

Any of these already set in the environment wins. Without all six the suite
**refuses to run** (exit 4). Before any test it also proves the deployed bundle
contains the API and Supabase URLs it was given, and that `/api/health` is 200.

## It is never collected by CI

Journey modules are named `live_*.py`; `pytest.ini` collects `test_*.py`, so
`pytest`, `scripts/verify_like_ci.sh` and every workflow skip them. The script
passes `-o python_files=live_*.py`.

## Opt-in flags (default off, reported `SKIPPED(opt-in)`)

| Flag | What it runs | Why it is off |
|---|---|---|
| `E2E_LIVE_RUN_PIPELINE=1` | Dashboard > Run Pipeline | daily Step Functions, paid scrapers, notification email |
| `E2E_LIVE_FIND_CONTACTS=1` | Workspace > Find Contacts | paid Apify actor |
| `E2E_LIVE_REGENERATE=1` | Workspace > Regenerate, Restore | a second full single-job pipeline |
| `E2E_LIVE_COVER_LETTER=1` | Add Job > Cover Letter | a second full single-job pipeline |

Google OAuth cannot be automated; the suite asserts only that the button exists.
"Forgot password?" is opened but not submitted (it sends email); the reset flow
is exercised with an admin-minted recovery link instead.

## Cost per default run

One single-job Step Functions execution (tailor + council + compile, 6-9 min),
about six scoring LLM calls (Save & Score, Score again), one call each for
research, interview prep, email and suggestions, and three LaTeX compiles.
No scrapers, no Apify, no email.

## What each test asserts

Every interaction asserts the API call actually happened and its status, then
the persisted outcome (via the API as the test user, or the service key) after a
reload. Each page carries two monitors that fail the test at teardown:

* **network** — any API/Supabase 4xx/5xx or transport failure the test did not
  declare with `net.allow(...)`;
* **console** — any `console.error` or uncaught page error, except the small
  `CONSOLE_ALLOWLIST` in `_live.py` (each entry states its reason).

Any synchronous call over 25 s is listed as a risk even when it passed: API
Gateway kills requests at ~30 s. A test whose prerequisite failed earlier is
reported `BLOCKED` (skipped), never passed.

## Test account and cleanup guarantees

Each run creates `e2e+<timestamp>@naukribaba.test` with the admin API
(`email_confirm: true`, so no email is sent) and a random password. Teardown
runs whether tests pass or fail and deletes:

* rows with that `user_id` in **every** table that has the column (discovered
  from PostgREST's schema at run time, so new tables are covered);
* the `users` row;
* S3 objects under `users/<uid>/` and `sessions/<uid>/`;
* `jobs_raw` rows this run created (the per-run company marker, or this
  account's hashes scraped after the run started — never pre-existing rows);
* the auth user.

It then re-counts each table, the S3 prefixes and the auth user, prints
`cleanup: <table>: 0 rows remain` per table, and errors if anything is left.

If a run is killed before teardown, the account id is in
`tests/e2e_live/artifacts/last_account.json`:

```bash
E2E_LIVE_...=... .venv/bin/python -m tests.e2e_live.cleanup --user-id <uuid>
E2E_LIVE_...=... .venv/bin/python -m tests.e2e_live.cleanup --orphans
```

## Artifacts

`tests/e2e_live/artifacts/` (gitignored): a screenshot and HTML per failure,
`report.json` (every interaction, latency, slow calls, console errors, cleanup
lines), and the exported ZIP.
