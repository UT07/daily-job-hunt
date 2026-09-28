/**
 * Auth injection for the E2E suite.
 *
 * The owner's password is not available to this suite and must never be. So
 * we skip the interactive sign-in and hand the browser a session directly, in
 * exactly the shape supabase-js persists one:
 *
 *   localStorage["sb-<first-label-of-hostname>-auth-token"] = JSON.stringify(session)
 *
 * supabase-js reads that key on boot, checks `expires_at` against the clock,
 * and -- because the session carries a `user` and is nowhere near expiry --
 * reports SIGNED_IN without a single network call. AuthProvider then renders
 * the app, and api.js attaches `Authorization: Bearer <access_token>` to every
 * request. The wire format is therefore the real one; only the act of typing
 * a password is skipped.
 *
 * Two grades of token:
 *  - mocked mode: an unsigned placeholder. Nothing verifies it, because
 *    Playwright answers every /api/** call itself.
 *  - live mode:   a real HS256 JWT minted from the project's SUPABASE_JWT_SECRET
 *    by scripts/e2e_seed.py, passed in as E2E_JWT. The FastAPI backend verifies
 *    it for real (auth.py `_decode_token`), so the auth seam is genuinely
 *    exercised without creating an auth user or knowing anyone's password.
 */

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const HERE = path.dirname(fileURLToPath(import.meta.url));
export const WEB_ROOT = path.resolve(HERE, '..', '..');
export const AUTH_STATE_PATH = path.join(HERE, '..', '.auth', 'state.json');

/**
 * The synthetic user the suite acts as. The UUID is fixed, obviously
 * non-random, and shared with scripts/e2e_seed.py, which is what makes
 * cleanup safe: every seeded row is `user_id = E2E_USER_ID`, so teardown can
 * delete by that one predicate and cannot touch the owner's data.
 */
export const E2E_USER_ID = '00000000-0000-4000-8000-00000000e2e2';
export const E2E_USER_EMAIL = 'playwright-e2e@naukribaba.test';

/** Read a VITE_* var out of web/.env.e2e (Playwright's config runs in Node, not Vite). */
export function readE2EEnv(key, fallback = '') {
  const file = path.join(WEB_ROOT, '.env.e2e');
  if (!fs.existsSync(file)) return fallback;
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const eq = trimmed.indexOf('=');
    if (eq === -1) continue;
    if (trimmed.slice(0, eq).trim() === key) return trimmed.slice(eq + 1).trim();
  }
  return fallback;
}

/**
 * Mirrors supabase-js's own derivation (SupabaseClient: `sb-${hostname.split('.')[0]}-auth-token`).
 * Kept as a function of the configured URL rather than a hardcoded string so
 * changing .env.e2e cannot silently de-authenticate the whole suite.
 */
export function storageKeyForSupabaseUrl(url) {
  const hostname = new URL(url).hostname;
  return `sb-${hostname.split('.')[0]}-auth-token`;
}

/** A placeholder access token: three base64url segments, so anything that
 *  merely *splits* a JWT keeps working. Not signed; not accepted by a real
 *  backend, which is the point -- mocked mode must not be able to reach one. */
function placeholderToken() {
  const enc = (obj) => Buffer.from(JSON.stringify(obj)).toString('base64url');
  const exp = Math.floor(Date.now() / 1000) + 60 * 60 * 24 * 365;
  return [
    enc({ alg: 'HS256', typ: 'JWT' }),
    enc({ sub: E2E_USER_ID, email: E2E_USER_EMAIL, aud: 'authenticated', exp, role: 'authenticated' }),
    'e2e-not-a-real-signature',
  ].join('.');
}

export function buildSession(accessToken = placeholderToken()) {
  // A year out: far past EXPIRY_MARGIN_MS, so supabase-js never tries to
  // refresh against the unreachable VITE_SUPABASE_URL mid-test.
  const expiresAt = Math.floor(Date.now() / 1000) + 60 * 60 * 24 * 365;
  return {
    access_token: accessToken,
    refresh_token: 'e2e-refresh-token',
    token_type: 'bearer',
    expires_in: 60 * 60 * 24 * 365,
    expires_at: expiresAt,
    user: {
      id: E2E_USER_ID,
      aud: 'authenticated',
      role: 'authenticated',
      email: E2E_USER_EMAIL,
      email_confirmed_at: '2026-01-01T00:00:00Z',
      phone: '',
      confirmed_at: '2026-01-01T00:00:00Z',
      last_sign_in_at: '2026-01-01T00:00:00Z',
      app_metadata: { provider: 'email', providers: ['email'] },
      user_metadata: {},
      identities: [],
      created_at: '2026-01-01T00:00:00Z',
      updated_at: '2026-01-01T00:00:00Z',
      is_anonymous: false,
    },
  };
}

/**
 * Playwright storageState for a signed-in user.
 *
 * `gdpr_consent` is pre-set because ConsentBanner is `fixed bottom-0 z-50` and
 * would otherwise cover the pagination controls in every dashboard test --
 * a returning user has consented, so this is the honest default. The consent
 * banner gets its own test instead (specs/consent-banner.spec.js).
 */
export function buildStorageState({ origin, accessToken, extraLocalStorage = [] } = {}) {
  const supabaseUrl = readE2EEnv('VITE_SUPABASE_URL', 'https://e2e.supabase.invalid');
  return {
    cookies: [],
    origins: [
      {
        origin,
        localStorage: [
          {
            name: storageKeyForSupabaseUrl(supabaseUrl),
            value: JSON.stringify(buildSession(accessToken)),
          },
          { name: 'gdpr_consent', value: 'true' },
          // Pin list view: the dashboard remembers 'list' vs 'card' in
          // localStorage, and a run that inherited 'card' from a previous
          // test would silently change which selectors exist.
          { name: 'naukribaba_view', value: 'list' },
          ...extraLocalStorage,
        ],
      },
    ],
  };
}

/** Written by globalSetup so every project can point `storageState` at a file. */
export function writeAuthState(origin) {
  const token = process.env.E2E_JWT || undefined;
  const state = buildStorageState({ origin, accessToken: token });
  fs.mkdirSync(path.dirname(AUTH_STATE_PATH), { recursive: true });
  fs.writeFileSync(AUTH_STATE_PATH, JSON.stringify(state, null, 2));
  return AUTH_STATE_PATH;
}
