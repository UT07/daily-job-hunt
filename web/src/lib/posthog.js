import posthog from 'posthog-js'

let initialised = false

export function initPostHog() {
  if (initialised) return posthog
  const key = import.meta.env.VITE_POSTHOG_KEY
  if (!key) {
    if (import.meta.env.DEV) {
      console.info('[posthog] VITE_POSTHOG_KEY not set — analytics disabled')
    }
    return null
  }
  posthog.init(key, {
    api_host: import.meta.env.VITE_POSTHOG_HOST || 'https://eu.i.posthog.com',
    capture_pageview: 'history_change',
    capture_exceptions: true,
    person_profiles: 'identified_only',
  })
  initialised = true
  return posthog
}

export function identifyUser(user) {
  if (!initialised || !user) return
  posthog.identify(user.id, {
    email: user.email,
  })
}

// For errors the app CATCHES (e.g. a React error boundary). capture_exceptions
// above only sees uncaught ones. No-op until initialised, like the helpers
// around it, so tests and key-less builds need no stub.
export function captureException(error, properties = {}) {
  if (!initialised) return
  try {
    posthog.captureException(error, properties)
  } catch {
    // Reporting must never be the second failure on an error screen.
  }
}

export function resetUser() {
  if (!initialised) return
  posthog.reset()
}

export { posthog }
