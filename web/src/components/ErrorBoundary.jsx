import { Component } from 'react';
import { captureException } from '../lib/posthog';

/**
 * Catches render exceptions below it so one broken page is not a white screen.
 *
 * Reports through lib/posthog's captureException: the global
 * capture_exceptions hook sees uncaught errors, and an error a boundary
 * catches is, by definition, caught.
 *
 * `resetKey`: when it changes (AppLayout passes the pathname), the boundary
 * clears, so navigating away from the broken page recovers without a reload.
 */
export default class ErrorBoundary extends Component {
  constructor(props) {
    super(props);
    this.state = { error: null, resetKey: props.resetKey };
  }

  static getDerivedStateFromError(error) {
    return { error };
  }

  static getDerivedStateFromProps(props, state) {
    if (props.resetKey !== state.resetKey) {
      return { error: null, resetKey: props.resetKey };
    }
    return null;
  }

  componentDidCatch(error, info) {
    captureException(error, {
      source: 'react_error_boundary',
      component_stack: info?.componentStack || '',
      path: typeof window !== 'undefined' ? window.location.pathname : '',
    });
  }

  render() {
    const { error } = this.state;
    if (!error) return this.props.children;
    return (
      <div className="flex items-center justify-center p-6">
        <div
          role="alert"
          className="max-w-md w-full bg-white border-2 border-black shadow-brutal p-8 text-center"
        >
          <h2 className="text-lg font-heading font-bold text-black mb-3">Something went wrong</h2>
          <p className="text-sm text-stone-500 mb-2">
            This page hit an error and could not be shown. Your data is not affected.
          </p>
          <p className="text-xs text-stone-400 font-mono mb-6 break-words">
            {error?.message || String(error)}
          </p>
          <div className="flex items-center justify-center gap-3">
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="border-2 border-black bg-black text-cream font-bold text-sm px-4 py-2 cursor-pointer"
            >
              Reload
            </button>
            {/* A plain link, not router navigation: the router state may be
                what broke, and a full load starts clean. */}
            <a href="/" className="border-2 border-black bg-cream text-black font-bold text-sm px-4 py-2">
              Back to Dashboard
            </a>
          </div>
        </div>
      </div>
    );
  }
}
