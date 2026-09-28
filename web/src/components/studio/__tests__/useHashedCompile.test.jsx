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
import { hashSections, useHashedCompile } from '../useHashedCompile';

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

describe('useHashedCompile with a PDF already on screen', () => {
  it('does not recompile content it was told is already rendered', async () => {
    // The Studio's case: GET .../sections parses the same _tailored.tex the
    // existing PDF was built from, so opening a job and blurring a field you
    // did not change must not spend 15s rebuilding an identical document.
    const compile = vi.fn(() => Promise.resolve('pdf-new'));
    const { result } = renderHook(
      () => useHashedCompile(A, compile, { renderedHashSeed: hashSections(A) }),
    );
    await act(async () => { result.current.requestCompile(); });
    expect(compile).not.toHaveBeenCalled();
    expect(result.current.pendingChanges).toBe(0);
  });

  it('still compiles once the content actually changes', async () => {
    const compile = vi.fn(() => Promise.resolve('pdf-new'));
    const { result, rerender } = renderHook(
      ({ s }) => useHashedCompile(s, compile, { renderedHashSeed: hashSections(A) }),
      { initialProps: { s: A } },
    );
    rerender({ s: B });
    expect(result.current.pendingChanges).toBe(1);
    await act(async () => { result.current.requestCompile(); });
    await waitFor(() => expect(result.current.pdfUrl).toBe('pdf-new'));
  });

  it('without the flag, the first compile still runs', async () => {
    // A caller with no pre-existing PDF genuinely needs that first compile.
    const compile = vi.fn(() => Promise.resolve('pdf-1'));
    const { result } = renderHook(() => useHashedCompile(A, compile));
    await act(async () => { result.current.requestCompile(); });
    await waitFor(() => expect(result.current.pdfUrl).toBe('pdf-1'));
    expect(compile).toHaveBeenCalledTimes(1);
  });
});
