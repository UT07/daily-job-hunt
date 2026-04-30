import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * useApiMutation — small async-action hook that surfaces loading + error state.
 *
 * Replaces the silent-swallow pattern:
 *
 *   fn().catch(err => console.error(err))
 *
 * with:
 *
 *   const { run, loading, error, data, reset } = useApiMutation(fn);
 *   <button onClick={() => run(arg)} disabled={loading} />
 *   {error && <ErrorBanner message={error} />}
 *
 * Contract:
 * - `run(arg)` invokes the provided async function with `arg`. Returns the
 *   resolved value on success, or `undefined` on failure (the error is
 *   captured into `error` state instead of throwing).
 * - `loading` is true while the call is in flight.
 * - `error` is a string (the message) or `null`.
 * - `data` is the latest successful result, or `null`.
 * - `reset()` clears `loading`, `error`, and `data`.
 *
 * The hook intentionally does NOT auto-render a toast/banner — components
 * decide where to surface the error (banner above form, inline next to a
 * button, etc.). The point is errors stop being silently console.error'd.
 *
 * Stale-call protection: if the hook is called twice in flight, only the
 * latest call's result/error is committed to state.
 */
export default function useApiMutation(fn) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const [data, setData] = useState(null);

  // Track the latest invocation so an earlier (slower) call can't overwrite
  // a newer call's state when it eventually resolves.
  const callIdRef = useRef(0);
  // Keep `fn` in a ref so `run` is stable and consumers don't need useCallback
  // around their function passed in. Update via effect (not during render —
  // see react-hooks/refs lint rule) so concurrent rendering stays safe.
  const fnRef = useRef(fn);
  useEffect(() => {
    fnRef.current = fn;
  }, [fn]);

  const run = useCallback(async (arg) => {
    const id = ++callIdRef.current;
    setLoading(true);
    setError(null);
    try {
      const result = await fnRef.current(arg);
      if (callIdRef.current === id) {
        setData(result);
        setLoading(false);
      }
      return result;
    } catch (e) {
      const msg = (e && (e.message || String(e))) || 'Request failed';
      if (callIdRef.current === id) {
        setError(msg);
        setLoading(false);
      }
      // Swallow the throw — the consumer reads `error`. This is the whole
      // point of the hook: no more uncaught rejections, no more silent fails.
      return undefined;
    }
  }, []);

  const reset = useCallback(() => {
    callIdRef.current++; // invalidate any in-flight call
    setLoading(false);
    setError(null);
    setData(null);
  }, []);

  return { run, loading, error, data, reset };
}
