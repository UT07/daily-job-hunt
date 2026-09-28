/**
 * Regression test for the NotificationBell half of the "app refetches
 * constantly" chain (diagnosed 2026-09-28).
 *
 * fetchNotifications was a useCallback whose dep array contained
 * `lastKnownCount` — the very state its own body called setLastKnownCount on.
 * Every poll therefore produced a new callback identity, and the
 * useEffect([fetchNotifications]) that owns the poll timer tore down its
 * setInterval and armed a fresh one, firing an immediate extra fetch each
 * time. The nominal 60s interval rarely survived to fire on its own.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, act, waitFor } from '@testing-library/react';

const { apiGet } = vi.hoisted(() => ({ apiGet: vi.fn() }));
vi.mock('../../api', () => ({ apiGet }));

import NotificationBell from '../NotificationBell';

const recentRun = (id) => ({
  id,
  status: 'SUCCEEDED',
  jobs_found: 3,
  started_at: new Date().toISOString(),
});

/** setInterval calls belonging to the bell's poll timer, ignoring any others. */
const pollIntervals = (spy) => spy.mock.calls.filter(([, ms]) => ms === 60000);

describe('NotificationBell poll timer', () => {
  let setIntervalSpy;
  let clearIntervalSpy;

  beforeEach(() => {
    vi.clearAllMocks();
    // Fake timers go in FIRST, then the spies wrap them. Spying on the real
    // timer and swapping in fakes mid-test leaves the two restore paths
    // fighting over globalThis.setInterval on teardown.
    vi.useFakeTimers();
    setIntervalSpy = vi.spyOn(globalThis, 'setInterval');
    clearIntervalSpy = vi.spyOn(globalThis, 'clearInterval');
  });

  afterEach(() => {
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  it('arms its interval exactly once across the state update from the first poll', async () => {
    apiGet.mockResolvedValue({ runs: [recentRun('r1'), recentRun('r2')] });

    render(<NotificationBell />);

    // Badge showing "2" proves the first response landed and setRuns/setCount
    // actually re-rendered the component — the state update that used to
    // re-create the callback and re-arm the timer.
    await screen.findByText('2');

    expect(pollIntervals(setIntervalSpy)).toHaveLength(1);
    expect(clearIntervalSpy).not.toHaveBeenCalled();
    expect(apiGet).toHaveBeenCalledTimes(1);
  });

  it('still arms exactly one interval when successive polls change the count', async () => {
    apiGet.mockResolvedValue({ runs: [recentRun('r1')] });

    render(<NotificationBell />);
    await act(async () => { await Promise.resolve(); });
    expect(apiGet).toHaveBeenCalledTimes(1);

    // Second poll returns MORE runs, so the toast branch fires and
    // lastKnownCount moves. Under the old code that was a setState inside the
    // callback's own dependency — the loop that kept re-arming the timer.
    apiGet.mockResolvedValue({ runs: [recentRun('r1'), recentRun('r2')] });
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });

    expect(apiGet).toHaveBeenCalledTimes(2);
    expect(pollIntervals(setIntervalSpy)).toHaveLength(1);
    expect(screen.getByText('Pipeline run completed! Check your new jobs.')).toBeTruthy();

    // A third tick must still come from that same original interval.
    await act(async () => { await vi.advanceTimersByTimeAsync(60000); });
    expect(apiGet).toHaveBeenCalledTimes(3);
    expect(pollIntervals(setIntervalSpy)).toHaveLength(1);
  });

  it('polls once per 60s window rather than once per render', async () => {
    apiGet.mockResolvedValue({ runs: [recentRun('r1')] });

    render(<NotificationBell />);
    await act(async () => { await Promise.resolve(); });

    await act(async () => { await vi.advanceTimersByTimeAsync(180000); });

    // 1 immediate + 3 ticks. The old code compounded extra fetches on top of
    // these because each state update re-ran the effect's immediate fetch.
    expect(apiGet).toHaveBeenCalledTimes(4);
  });

  it('tears the interval down exactly once on unmount', async () => {
    apiGet.mockResolvedValue({ runs: [] });

    const { unmount } = render(<NotificationBell />);
    await waitFor(() => expect(apiGet).toHaveBeenCalledTimes(1));
    expect(clearIntervalSpy).not.toHaveBeenCalled();

    unmount();
    expect(clearIntervalSpy).toHaveBeenCalledTimes(1);
  });
});
