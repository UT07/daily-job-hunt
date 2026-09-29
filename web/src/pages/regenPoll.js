/**
 * Wait for a regeneration to finish, and distinguish the ways it can end.
 *
 * The inline setInterval this replaces reported "Regenerate failed" for three
 * conditions that are not a failed regeneration:
 *
 *   1. A single transient poll error. A 6m40s run is roughly 80 requests; one
 *      502 from API Gateway, or an access token refreshing mid-flight, ended
 *      the whole wait.
 *   2. A failure in either refresh call made AFTER the pipeline succeeded. Both
 *      sat inside the same try, so failing to reload the job rendered as a
 *      failed regeneration — when the resume had in fact been regenerated.
 *   3. Nothing at all, forever: there was no deadline, so a status stuck on
 *      RUNNING left the spinner going indefinitely.
 *
 * Observed 2026-09-29: a regeneration reported failure in the UI while the
 * execution SUCCEEDED after 6m40s, the status endpoint answered 200 on 20/20
 * probes, and both refresh calls returned 200 in under a second.
 *
 * Returns { outcome, message?, warning? } where outcome is one of
 * 'succeeded' | 'failed' | 'timeout' | 'unreachable'. Never throws.
 */

const TERMINAL_FAILURES = new Set(['FAILED', 'TIMED_OUT', 'ABORTED']);

export async function pollRegeneration({
  getStatus,
  refresh,
  intervalMs = 5000,
  // Generous: the observed run took 6m40s and TailorResume alone can take
  // 449s of a 600s Lambda budget.
  maxWaitMs = 20 * 60 * 1000,
  // Tolerate blips, but do not wait out a genuine outage.
  maxConsecutiveErrors = 5,
  sleep = (ms) => new Promise((r) => setTimeout(r, ms)),
  now = () => Date.now(),
} = {}) {
  const deadline = now() + maxWaitMs;
  let consecutiveErrors = 0;
  let lastError = null;

  for (;;) {
    let status;
    try {
      status = await getStatus();
      consecutiveErrors = 0;
    } catch (e) {
      consecutiveErrors += 1;
      lastError = e;
      if (consecutiveErrors >= maxConsecutiveErrors) {
        return {
          outcome: 'unreachable',
          message: `Could not reach the server (${consecutiveErrors} attempts): ${e.message}. `
            + 'The regeneration may still be running — reload to check.',
        };
      }
      if (now() >= deadline) return { outcome: 'timeout', message: timeoutMessage(lastError) };
      await sleep(intervalMs);
      continue;
    }

    const state = status?.status;
    if (state && state !== 'RUNNING') {
      if (TERMINAL_FAILURES.has(state)) {
        return {
          outcome: 'failed',
          message: status.error || status.cause || `Pipeline ${state.toLowerCase()}`,
        };
      }
      // Succeeded. The refresh is a convenience, not part of the outcome:
      // failing to reload the view does not un-regenerate the resume.
      if (refresh) {
        try {
          await refresh();
        } catch (e) {
          return {
            outcome: 'succeeded',
            warning: `Regenerated, but could not refresh the page (${e.message}). Reload to see it.`,
          };
        }
      }
      return { outcome: 'succeeded' };
    }

    if (now() >= deadline) return { outcome: 'timeout', message: timeoutMessage(lastError) };
    await sleep(intervalMs);
  }
}

function timeoutMessage(lastError) {
  const tail = lastError ? ` Last error: ${lastError.message}.` : '';
  return 'Still running after 20 minutes — it may yet finish. Reload to check.' + tail;
}
