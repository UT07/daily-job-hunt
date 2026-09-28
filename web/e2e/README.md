# End-to-end tests

Playwright, driving a real browser against the real bundle.

These exist because unit tests kept passing while the running app misbehaved.
Every bug that prompted them — `/api/score` persisting nothing, `hide_expired`
erasing every application ever sent, a lifecycle default sitting inside an
`if filters:` that an empty dict made falsy, a retry guard matching the wrong
error wording, two tier implementations with different bands — was a failure at
a **seam**: state, defaults, or persistence at a boundary. Nothing in the middle
of a function. So the suite lives at the boundaries too.

---

## Quick start

```bash
cd web
npm install
npx playwright install chromium   # one-off, ~100 MB
npm run test:e2e                  # the mocked project — no secrets, no network
```

That is the whole setup for the default suite. It starts its own Vite dev
server on port **5174** (not 5173, so it never fights the one you have open),
intercepts every `/api/**` call in the browser, and needs no database, no
backend and no credentials.

Useful variants:

```bash
npm run test:e2e -- --headed          # watch it
npm run test:e2e:ui                   # Playwright's UI mode, best for debugging
npm run test:e2e -- dashboard-filters # one spec
npm run test:e2e -- -g "hide_expired" # one test by name
npx playwright show-report            # the HTML report after a run
```

---

## The two projects, and why

| | `mocked` (default) | `live` (opt-in) |
|---|---|---|
| Backend | a fake installed with `page.route` | real `app.py` on `:8000` |
| Database | none | the real Supabase, as a synthetic user |
| Secrets | none | `.env` |
| Proves | what the **frontend** does | what the **backend** does |

**A mock can only ever prove things about the frontend.** Asserting
"`hide_expired=true` keeps applied jobs visible" against a fake backend proves
the fake was written correctly and nothing else. That semantics lives in
`db_client.get_jobs`'s SQL, so it is tested against the real thing in
`live/api.spec.js`. The mocked suite covers the other half of that contract:
that the app *sends* `hide_expired=true`, and renders whatever comes back
without a second client-side filter of its own.

What the mocked project is genuinely good at:

- **Recording.** Every request is captured, so specs assert on the exact query
  string the app sends — which defaults, in which combination, after a reload.
  Three of the five bugs above would have shown up here as a changed query.
- **Controlled responses.** A 503, a 401 mid-session, a FastAPI 422 validation
  array, a `saved: false` write failure, a job that is both Applied and
  expired — all trivial to produce, all awkward to produce live.

---

## Auth: what you must supply, and what you must not

**The suite never asks for your password, and must never be given one.**
There is no test that types credentials into the login form, and adding one
would mean putting a real password somewhere a test runner can read it.

Instead the session is **injected**. `e2e/global-setup.js` writes a Playwright
`storageState` file containing a session object in exactly the shape
`supabase-js` persists one:

```
localStorage["sb-<first label of VITE_SUPABASE_URL's hostname>-auth-token"]
```

On boot `supabase-js` reads that key, sees an unexpired session carrying a
`user`, and reports `SIGNED_IN` **without a single network call**. `AuthProvider`
renders the app and `api.js` attaches `Authorization: Bearer <access_token>` to
every request. The wire format is the real one; only the act of typing a
password is skipped.

`web/.env.e2e` points `VITE_SUPABASE_URL` at `https://e2e.supabase.invalid` — a
host that does not resolve — so a mocked run physically cannot reach your real
Supabase project. Nothing in that file is a secret.

### Running the `live` project

Four things, none of them a password:

1. **A `.env` with `SUPABASE_URL`, `SUPABASE_SERVICE_KEY` and
   `SUPABASE_JWT_SECRET`.** The repo root's `.env` is used by default; point
   `NAUKRIBABA_ENV_FILE` elsewhere if you are in a worktree, which has no
   `.env` of its own.

2. **Seeded fixtures.** Nine rows owned by one synthetic user:

   ```bash
   .venv/bin/python scripts/e2e_seed.py seed
   ```

   It prints the two exports you need. Other commands: `status` (what is
   currently seeded), `token` (re-mint without writing), `teardown` (delete
   everything it created).

3. **The exports it printed:**

   ```bash
   export E2E_LIVE=1
   export E2E_JWT=<the token it printed>
   ```

4. **Run it.** The backend is started for you by `scripts/e2e_backend.sh`
   (an already-running uvicorn on `:8000` is reused as-is):

   ```bash
   cd web && npm run test:e2e:live
   ```

Afterwards:

```bash
.venv/bin/python scripts/e2e_seed.py teardown
```

#### What the live mode does to your database

- It writes **nine `jobs` rows and one `users` row**, all carrying
  `user_id = 00000000-0000-4000-8000-00000000e2e2` — a fixed, obviously
  synthetic UUID that no human account will ever have.
- Teardown deletes **by that one predicate**, so it cannot reach your data even
  if run at the wrong moment. `cmd_teardown` additionally refuses to run if
  that constant has been edited.
- Two tests write: one flips a seeded job's status and puts it back in a
  `finally`, one asserts an invalid status is rejected. Both touch only seeded
  rows.
- **No `auth.users` row is created.** The token is minted locally with HS256
  and your project's `SUPABASE_JWT_SECRET` — the same signature Supabase
  issues, which `auth.py` then verifies for real. No password exists anywhere.
- `scripts/e2e_backend.sh` blanks `POSTHOG_API_KEY`, so a run does not emit
  analytics events.
- Nothing calls an AI provider, Step Functions, or S3. `/api/score` and
  `/api/tailor` are exercised in the **mocked** project only, because live they
  would spend real model calls on a synthetic job.

---

## Layout

```
e2e/
  global-setup.js        writes the storageState the projects authenticate with
  fixtures/
    session.js           the injected session, and the storage key derivation
    jobs.js              deterministic job fixtures (real column names)
    mockApi.js           the recording fake backend
    dashboard.js         page object for the dashboard
    test.js              the extended `test` every mocked spec imports
  specs/                 the mocked project
  live/                  the live project (collected only when E2E_LIVE=1)
```

---

## Writing a test here

**A test that cannot fail is worse than no test.** Before a new assertion is
finished, break the thing it covers, watch it go red, and put it back. Every
significant assertion in this suite was checked that way; the two commits that
introduced them list the mutations and the resulting failures.

Things that will bite you:

- **`api` is an auto fixture.** You get it whether or not you name it. Do not
  remove that: without the route handlers, every fetch falls through Vite's
  proxy to a backend that is not running, and the symptom is a 30-second
  timeout rather than "you forgot a fixture".

- **Set the corpus with `api.setJobs([...])`, not `test.use()`.** Playwright
  unwraps an array passed through `use()` as its own `[value, options]` fixture
  tuple, and treats a function as a fixture body, so neither shape survives.
  Call `setJobs` before the first navigation.

- **Every job is rendered twice.** `JobTable` has an `md:hidden` card stack and
  a `hidden md:block` table, so any unscoped `getByText('Acme')` is ambiguous
  by construction and may resolve to the hidden copy first. Go through the
  `Dashboard` page object, which scopes to one or the other.

- **Wait for the request, not for the clock.** `dashboard.withListResponse(fn)`
  runs an action and resolves when the resulting list request returns. A bare
  `waitForResponse` *after* an action usually hangs, because the response has
  already landed.

- **A response landing and React committing are two different moments.** After
  a list response, poll (`expect.poll`) rather than reading the DOM once, or
  you will read the previous filter's rows.

- **The dashboard stays mounted during a route transition.** `JobWorkspace` is
  `lazy()`, so clicking a row flips the URL while the dashboard is still on
  screen. Going "back" inside that window means the dashboard never unmounted,
  so it never refetches. Wait for the job page to actually render first.

- **React StrictMode double-invokes effects on the dev server.** Every
  effect-driven fetch legitimately fires twice here and once in a production
  build, so assert bounds, not exact counts.

---

## `test.fail` means "this bug is real"

Six tests are marked `test.fail`. Each describes behaviour the app **does not
have today**, with a repro and a suggested fix in its comment.

`test.fail` is not `skip`. The test runs, and is counted as passing **because**
it fails — so the suite is green today. The moment someone fixes the underlying
defect, Playwright reports `Expected to fail, but passed` and the run goes red.
That is the signal to delete the marker, not a regression.

Search the specs for `KNOWN DEFECT` to read them. As of this commit:

| Where | What |
|---|---|
| `regressions.spec.js` | `ScoreCard` ignores the `saved` flag, so a scored-but-unsaved job looks identical to a saved one |
| `filter-options.spec.js` | the Status filter offers "Expired" (not a status — matches nothing) and omits "Phone Screen" and "Accepted" (which are) |
| `pagination.spec.js` | tier tabs do not reset the page, so tier-switching from page 3 shows an empty board |
| `regressions.spec.js` | stale (14–30 day) jobs appear in **both** the working list and the Past / Outdated shelf |
| `job-workspace.spec.js` | the four inline-edit fields have labels associated with nothing |
| `auth-gate.spec.js` | a 401 does not bounce you to login when Supabase itself is unreachable |
