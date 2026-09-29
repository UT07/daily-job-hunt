/**
 * The regenerate poll turned three unrelated conditions into "Regenerate failed".
 *
 * Observed 2026-09-29: a regeneration reported failure in the UI while the
 * Step Functions execution SUCCEEDED at 15:01:11 after 6m40s. The status
 * endpoint answered 200 on 20/20 consecutive probes and both post-success
 * refresh calls returned 200 in under a second, so nothing server-side failed.
 *
 * Three ways the loop can report failure without the pipeline failing:
 *
 *   1. ONE transient poll error aborts the whole wait. Over a 6.5 minute run
 *      that is ~80 requests; a single 502 from API Gateway, or an access token
 *      refreshing mid-flight, ends it.
 *   2. The two refresh calls that run AFTER a successful pipeline sit inside
 *      the same try, so a failure to reload the job renders as "Regenerate
 *      failed" — the resume was in fact regenerated.
 *   3. There is no overall deadline. If status never leaves RUNNING the
 *      spinner runs forever.
 *
 * pollRegeneration is extracted so these can be tested without mounting a
 * 1,400-line page.
 */
import { describe, expect, it, vi } from 'vitest';
import { pollRegeneration } from '../regenPoll';

const ok = (status) => ({ status });

describe('pollRegeneration', () => {
  it('survives a transient poll error and keeps waiting', async () => {
    let n = 0;
    const getStatus = vi.fn(async () => {
      n += 1;
      if (n === 2) throw new Error('502 Bad Gateway');
      return n < 4 ? ok('RUNNING') : ok('SUCCEEDED');
    });
    const out = await pollRegeneration({ getStatus, intervalMs: 0 });
    expect(out.outcome).toBe('succeeded');
    expect(getStatus).toHaveBeenCalledTimes(4);
  });

  it('gives up after consecutive errors rather than hanging', async () => {
    const getStatus = vi.fn(async () => { throw new Error('network down'); });
    const out = await pollRegeneration({ getStatus, intervalMs: 0, maxConsecutiveErrors: 3 });
    expect(out.outcome).toBe('unreachable');
    expect(getStatus).toHaveBeenCalledTimes(3);
  });

  it('reports a real pipeline failure as a failure', async () => {
    const getStatus = vi.fn(async () => ({ status: 'FAILED', error: 'JobProcessingFailed' }));
    const out = await pollRegeneration({ getStatus, intervalMs: 0 });
    expect(out.outcome).toBe('failed');
    expect(out.message).toMatch(/JobProcessingFailed/);
  });

  it('stops at a deadline instead of spinning forever', async () => {
    const getStatus = vi.fn(async () => ok('RUNNING'));
    const out = await pollRegeneration({ getStatus, intervalMs: 0, maxWaitMs: 0 });
    expect(out.outcome).toBe('timeout');
  });

  it('a failed refresh is NOT reported as a failed regeneration', async () => {
    const getStatus = vi.fn(async () => ok('SUCCEEDED'));
    const refresh = vi.fn(async () => { throw new Error('500 on /versions'); });
    const out = await pollRegeneration({ getStatus, refresh, intervalMs: 0 });
    expect(out.outcome).toBe('succeeded');
    expect(out.warning).toMatch(/could not refresh/i);
    expect(out.message).toBeUndefined();
  });

  it('a successful refresh reports no warning', async () => {
    const out = await pollRegeneration({
      getStatus: async () => ok('SUCCEEDED'), refresh: async () => {}, intervalMs: 0,
    });
    expect(out.outcome).toBe('succeeded');
    expect(out.warning).toBeUndefined();
  });
});
