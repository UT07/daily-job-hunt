import '@testing-library/jest-dom'

// posthog-js is initialized in main.jsx with a key from import.meta.env. In
// tests we don't initialize it; lib/applyTelemetry.js must no-op when posthog
// isn't ready, so no global stub is needed here.
