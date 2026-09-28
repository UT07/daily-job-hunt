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
  it('discards a result whose content was superseded while it ran', async () => {
    // Compiles are serialized, so two can no longer be in flight at once and a
    // literal out-of-order completion is impossible by construction. The
    // staleness guard still matters for the case that remains: the user keeps
    // typing while the single compile runs, so by the time it returns, its PDF
    // describes a document that no longer exists in the editor. Rendering it
    // would be an invisibly stale pane.
    let resolveA;
    const compile = vi.fn(() => new Promise((r) => { resolveA = () => r('pdf-A'); }));

    const { result, rerender } = renderHook(({ s }) => useHashedCompile(s, compile), {
      initialProps: { s: A },
    });

    act(() => result.current.requestCompile());
    expect(compile).toHaveBeenCalledTimes(1);

    rerender({ s: B });                       // edited while A is still compiling
    await act(async () => { resolveA(); });

    expect(result.current.pdfUrl).toBeNull(); // A's PDF is for content that is gone
    expect(result.current.pendingChanges).toBe(0); // nothing rendered yet, so nothing stale
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

describe('compiles are serialized, not just de-duplicated', () => {
  it('never runs two compiles at once', async () => {
    // The hash guard stops a STALE result being DISPLAYED, but both compiles
    // still reach the server, and both write the same S3 key
    // (users/{uid}/resumes/{job_id}_tailored.pdf). Whichever finishes last
    // wins on disk — and that can be the older document. The client's view
    // would then be correct while the stored resume is silently wrong.
    let live = 0, maxLive = 0;
    const resolvers = [];
    const compile = vi.fn(() => {
      live += 1; maxLive = Math.max(maxLive, live);
      return new Promise((r) => resolvers.push(() => { live -= 1; r('pdf-' + resolvers.length); }));
    });

    const { result, rerender } = renderHook(({ s }) => useHashedCompile(s, compile), {
      initialProps: { s: A },
    });
    act(() => result.current.requestCompile());
    rerender({ s: B });
    act(() => result.current.requestCompile());

    expect(maxLive).toBe(1);
    expect(compile).toHaveBeenCalledTimes(1);

    await act(async () => { resolvers[0](); });
    await waitFor(() => expect(compile).toHaveBeenCalledTimes(2));
    expect(maxLive).toBe(1);
  });

  it('the queued compile uses the LATEST content, not the content at queue time', async () => {
    const seen = [];
    const resolvers = [];
    const compile = vi.fn((s) => {
      seen.push(s.summary);
      return new Promise((r) => resolvers.push(r));
    });
    const { result, rerender } = renderHook(({ s }) => useHashedCompile(s, compile), {
      initialProps: { s: A },
    });
    act(() => result.current.requestCompile());          // compiles A
    rerender({ s: B });
    act(() => result.current.requestCompile());          // queued
    rerender({ s: { summary: 'third' } });               // superseded before it ran

    await act(async () => { resolvers[0]('pdf-A'); });
    await waitFor(() => expect(compile).toHaveBeenCalledTimes(2));
    expect(seen).toEqual(['first', 'third']);            // never 'second'
  });
});
