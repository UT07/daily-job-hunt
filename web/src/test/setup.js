import '@testing-library/jest-dom'

// posthog-js is initialized in main.jsx with a key from import.meta.env. In
// tests we don't initialize it; lib/applyTelemetry.js must no-op when posthog
// isn't ready, so no global stub is needed here.

// @testing-library/dom's waitFor checks `typeof jest` to detect fake timers
// and auto-advance them during polling. Vitest doesn't inject `jest` as a
// global even with `globals: true`, so we alias it here so that
// jestFakeTimersAreEnabled() returns the correct value when vi.useFakeTimers()
// is active.  The only method waitFor actually calls is jest.advanceTimersByTime,
// so we expose that single method (vitest's vi.advanceTimersByTime is equivalent).
if (typeof globalThis.jest === 'undefined') {
  globalThis.jest = {
    advanceTimersByTime: (ms) => vi.advanceTimersByTime(ms),
  }
}
