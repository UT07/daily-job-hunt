/**
 * A render exception anywhere under the router used to unmount the whole tree:
 * a white screen, no message, no way back but the address bar. There was no
 * error boundary.
 *
 * Reporting: lib/posthog.js inits with capture_exceptions: true, which hooks
 * window.onerror / unhandledrejection. An error CAUGHT by a React boundary is
 * handled, so that autocapture alone would not reliably see it; the boundary
 * reports explicitly through the same guarded-helper pattern as identifyUser.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const { captureException } = vi.hoisted(() => ({ captureException: vi.fn() }));
vi.mock('../../lib/posthog', () => ({ captureException }));

import ErrorBoundary from '../ErrorBoundary';

function Boom() {
  throw new Error('kaboom in render');
}

beforeEach(() => {
  captureException.mockReset();
  // React logs caught render errors to console.error; keep the output clean.
  vi.spyOn(console, 'error').mockImplementation(() => {});
});

describe('ErrorBoundary', () => {
  it('renders children when nothing throws', () => {
    render(<ErrorBoundary><p>fine</p></ErrorBoundary>);
    expect(screen.getByText('fine')).toBeInTheDocument();
  });

  it('shows a recovery UI instead of a white screen', () => {
    render(<ErrorBoundary><Boom /></ErrorBoundary>);
    expect(screen.getByRole('alert')).toHaveTextContent(/Something went wrong/);
    expect(screen.getByText(/kaboom in render/)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: 'Reload' })).toBeInTheDocument();
    expect(screen.getByRole('link', { name: 'Back to Dashboard' })).toHaveAttribute('href', '/');
  });

  it('reports the error to PostHog with the component stack', () => {
    render(<ErrorBoundary><Boom /></ErrorBoundary>);
    expect(captureException).toHaveBeenCalledTimes(1);
    const [err, props] = captureException.mock.calls[0];
    expect(err).toBeInstanceOf(Error);
    expect(err.message).toBe('kaboom in render');
    expect(props).toEqual(expect.objectContaining({
      source: 'react_error_boundary',
      component_stack: expect.stringContaining('Boom'),
    }));
  });

  it('Reload reloads the page', () => {
    const reload = vi.fn();
    const original = window.location;
    Object.defineProperty(window, 'location', { configurable: true, value: { ...original, reload } });
    try {
      render(<ErrorBoundary><Boom /></ErrorBoundary>);
      fireEvent.click(screen.getByRole('button', { name: 'Reload' }));
      expect(reload).toHaveBeenCalled();
    } finally {
      Object.defineProperty(window, 'location', { configurable: true, value: original });
    }
  });

  it('recovers when resetKey changes (e.g. the user navigates elsewhere)', () => {
    let shouldThrow = true;
    function Maybe() {
      if (shouldThrow) throw new Error('first page broke');
      return <p>second page</p>;
    }
    const { rerender } = render(<ErrorBoundary resetKey="/a"><Maybe /></ErrorBoundary>);
    expect(screen.getByRole('alert')).toBeInTheDocument();
    shouldThrow = false;
    rerender(<ErrorBoundary resetKey="/b"><Maybe /></ErrorBoundary>);
    expect(screen.getByText('second page')).toBeInTheDocument();
  });
});

describe('lib/posthog captureException', () => {
  it('is a no-op before PostHog is initialised', async () => {
    const real = await vi.importActual('../../lib/posthog');
    expect(() => real.captureException(new Error('x'), {})).not.toThrow();
  });
});
