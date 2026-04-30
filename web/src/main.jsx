import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { PostHogProvider } from 'posthog-js/react'
import './index.css'
import App from './App.jsx'
import { initPostHog } from './lib/posthog'

const ph = initPostHog()

const tree = (
  <StrictMode>
    <App />
  </StrictMode>
)

createRoot(document.getElementById('root')).render(
  ph ? <PostHogProvider client={ph}>{tree}</PostHogProvider> : tree,
)
