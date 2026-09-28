/**
 * A PDF pane must never silently show an older document than the editor.
 *
 * Compiles take ~15s and are enqueued on blur, so two can be outstanding at
 * once and they can finish out of order. Without a key, the LAST response to
 * arrive wins — which may be the OLDER document, rendered with no indication
 * that it is stale. Keying each result by the hash of the content it was
 * compiled from, and discarding any result whose hash is no longer current,
 * makes that case impossible.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useHashedCompile } from '../useHashedCompile';

const A = { summary: 'first' };
const B = { summary: 'second' };

describe('useHashedCompile', () => {
  it('discards a stale result that lands after a newer one', async () => {
    let resolveA, resolveB;
    const compile = vi.fn((s) =>
      s.summary === 'first'
        ? new Promise((r) => { resolveA = () => r('pdf-A'); })
        : new Promise((r) => { resolveB = () => r('pdf-B'); }));

    const { result, rerender } = renderHook(({ s }) => useHashedCompile(s, compile), {
      initialProps: { s: A },
    });

    act(() => result.current.requestCompile());
    rerender({ s: B });
    act(() => result.current.requestCompile());

    // B finishes first, then the older A arrives late.
    await act(async () => { resolveB(); });
    await waitFor(() => expect(result.current.pdfUrl).toBe('pdf-B'));
    await act(async () => { resolveA(); });

    expect(result.current.pdfUrl).toBe('pdf-B');
  });

  it('does not recompile when the content has not changed', async () => {
    const compile = vi.fn(() => Promise.resolve('pdf-1'));
    const { result } = renderHook(() => useHashedCompile(A, compile));

    await act(async () => { result.current.requestCompile(); });
    await waitFor(() => expect(result.current.pdfUrl).toBe('pdf-1'));
    await act(async () => { result.current.requestCompile(); });

    expect(compile).toHaveBeenCalledTimes(1);
  });

  it('counts uncompiled changes so the pane can say so', async () => {
    const compile = vi.fn(() => Promise.resolve('pdf-1'));
    const { result, rerender } = renderHook(({ s }) => useHashedCompile(s, compile), {
      initialProps: { s: A },
    });
    await act(async () => { result.current.requestCompile(); });
    await waitFor(() => expect(result.current.pdfUrl).toBe('pdf-1'));

    expect(result.current.pendingChanges).toBe(0);
    rerender({ s: B });
    expect(result.current.pendingChanges).toBe(1);
  });

  it('surfaces a compile failure without clearing the last good PDF', async () => {
    let call = 0;
    const compile = vi.fn(() => {
      call += 1;
      return call === 1 ? Promise.resolve('pdf-1') : Promise.reject(new Error('tectonic exploded'));
    });
    const { result, rerender } = renderHook(({ s }) => useHashedCompile(s, compile), {
      initialProps: { s: A },
    });
    await act(async () => { result.current.requestCompile(); });
    await waitFor(() => expect(result.current.pdfUrl).toBe('pdf-1'));

    rerender({ s: B });
    await act(async () => { result.current.requestCompile(); });
    await waitFor(() => expect(result.current.error).toMatch(/tectonic exploded/));
    expect(result.current.pdfUrl).toBe('pdf-1');
  });
});
