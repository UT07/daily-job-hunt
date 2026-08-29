/**
 * Unit tests for `formatErrorDetail` — pins the contract that no API error
 * ever surfaces in the UI as "[object Object]".
 *
 * Origin: onboarding "Search Preferences" step rendered "[object Object]"
 * because FastAPI 422 validation responses use `detail: [{loc, msg, type}]`,
 * and the throw sites in api.js called String() on the array directly.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest'

// Mock the supabase client so the fetch wrappers can call auth.signOut()
// and auth.getSession() without a real backend.
const { signOut, getSession } = vi.hoisted(() => ({
  signOut: vi.fn(),
  getSession: vi.fn(),
}))
vi.mock('../lib/supabase', () => ({
  supabase: { auth: { signOut, getSession } },
}))

import { formatErrorDetail, apiGet, apiCall } from '../api'

describe('formatErrorDetail', () => {
  it('returns empty string for null/undefined', () => {
    expect(formatErrorDetail(null)).toBe('')
    expect(formatErrorDetail(undefined)).toBe('')
  })

  it('passes string detail through unchanged', () => {
    expect(formatErrorDetail('Profile incomplete')).toBe('Profile incomplete')
  })

  it('formats a FastAPI 422 single-error array with loc + msg', () => {
    const detail = [{ loc: ['body', 'queries'], msg: 'field required', type: 'value_error.missing' }]
    expect(formatErrorDetail(detail)).toBe('queries: field required')
  })

  it('formats a multi-error array joined by semicolons', () => {
    const detail = [
      { loc: ['body', 'queries'], msg: 'field required', type: 'value_error.missing' },
      { loc: ['body', 'min_match_score'], msg: 'ensure this value is greater than or equal to 0', type: 'value_error.number.not_ge' },
    ]
    expect(formatErrorDetail(detail)).toBe(
      'queries: field required; min_match_score: ensure this value is greater than or equal to 0'
    )
  })

  it('strips the leading "body" segment from loc paths', () => {
    const detail = [{ loc: ['body', 'profile', 'phone'], msg: 'invalid format' }]
    expect(formatErrorDetail(detail)).toBe('profile.phone: invalid format')
  })

  it('handles an array of plain strings', () => {
    expect(formatErrorDetail(['one', 'two'])).toBe('one; two')
  })

  it('handles a single object with `message` field', () => {
    expect(formatErrorDetail({ message: 'unauthorized' })).toBe('unauthorized')
  })

  it('handles a single object with `error` field as fallback', () => {
    expect(formatErrorDetail({ error: 'rate_limited' })).toBe('rate_limited')
  })

  it('falls back to JSON.stringify for objects without known fields', () => {
    expect(formatErrorDetail({ foo: 'bar' })).toBe('{"foo":"bar"}')
  })

  it('handles array element that is an object without msg/message', () => {
    const detail = [{ code: 42, type: 'unexpected' }]
    expect(formatErrorDetail(detail)).toBe('{"code":42,"type":"unexpected"}')
  })

  it('REGRESSION: never returns "[object Object]" for any reasonable input', () => {
    // The original bug — sanity check across all variants.
    const inputs = [
      null,
      undefined,
      '',
      'string',
      [],
      [{ loc: ['body', 'x'], msg: 'bad' }],
      [{ msg: 'bad' }],
      { message: 'bad' },
      { error: 'bad' },
      { foo: 'bar' },
    ]
    for (const input of inputs) {
      const out = formatErrorDetail(input)
      expect(out).not.toContain('[object Object]')
    }
  })
})

/**
 * App-wide 401 handling. A 401 means the Supabase session is missing or
 * expired (e.g. the refresh token died while the user was away). Every
 * fetch wrapper must clear the stale local session so AuthProvider →
 * AppLayout redirects to /login, instead of letting callers render a
 * silent broken state (the Smart Apply blank-modal bug).
 */
describe('api 401 session-expiry handling', () => {
  beforeEach(() => {
    signOut.mockReset().mockResolvedValue({ error: null })
    getSession.mockReset().mockResolvedValue({ data: { session: { access_token: 'tok' } } })
    global.fetch = vi.fn()
  })

  it('apiGet clears the local session and throws SESSION_EXPIRED on 401', async () => {
    global.fetch.mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: 'Invalid or expired token' }),
    })
    await expect(apiGet('/api/apply/preview/x')).rejects.toMatchObject({ code: 'SESSION_EXPIRED' })
    expect(signOut).toHaveBeenCalledWith({ scope: 'local' })
  })

  it('apiCall clears the local session and throws SESSION_EXPIRED on 401', async () => {
    global.fetch.mockResolvedValue({
      ok: false,
      status: 401,
      json: async () => ({ detail: 'Missing authorization header' }),
    })
    await expect(apiCall('/api/apply/record', { job_id: 'x' })).rejects.toMatchObject({ code: 'SESSION_EXPIRED' })
    expect(signOut).toHaveBeenCalledWith({ scope: 'local' })
  })

  it('does NOT sign out on a 422 validation error (only 401 triggers logout)', async () => {
    global.fetch.mockResolvedValue({
      ok: false,
      status: 422,
      json: async () => ({ detail: [{ loc: ['body', 'x'], msg: 'bad' }] }),
    })
    await expect(apiGet('/x')).rejects.toThrow('x: bad')
    expect(signOut).not.toHaveBeenCalled()
  })

  it('does NOT sign out on a 500 server error', async () => {
    global.fetch.mockResolvedValue({
      ok: false,
      status: 500,
      json: async () => ({ detail: 'boom' }),
    })
    await expect(apiGet('/x')).rejects.toThrow('boom')
    expect(signOut).not.toHaveBeenCalled()
  })
})
