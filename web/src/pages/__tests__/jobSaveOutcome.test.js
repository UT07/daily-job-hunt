/**
 * resolveSaveOutcome — the decision the Overview save used to skip entirely.
 *
 * The bug: handleSave merged `editFields` into local state and reported
 * "Job updated." without ever looking at the response. title and company were
 * dropped by app.py's `_EDITABLE_FIELDS` filter, so the UI showed edits the
 * database did not have, and they reverted on reload.
 *
 * Every case below asks the rule-2 question of the helper: what does it report
 * when the server stored nothing?
 */
import { describe, it, expect } from 'vitest';
import { resolveSaveOutcome } from '../jobSaveOutcome';

describe('resolveSaveOutcome', () => {
  it('reports a field the server echoes back unchanged as persisted', () => {
    const { persisted, notStored } = resolveSaveOutcome(
      { location: 'Dublin' },
      { job_id: 'job-1', location: 'Dublin' },
    );
    expect(persisted).toEqual({ location: 'Dublin' });
    expect(notStored).toEqual({});
  });

  it('reports a field the response omits as NOT stored', () => {
    // This is the shape of the original bug: the server answered 200 with a row
    // that simply has no record of the edit.
    const { persisted, notStored } = resolveSaveOutcome(
      { title: 'Renamed', location: 'Dublin' },
      { job_id: 'job-1', location: 'Dublin' },
    );
    expect(persisted).toEqual({ location: 'Dublin' });
    expect(notStored).toEqual({ title: 'Renamed' });
  });

  it('reports a field the server kept at its old value as NOT stored', () => {
    const { persisted, notStored } = resolveSaveOutcome(
      { title: 'Renamed', location: 'Dublin' },
      { job_id: 'job-1', title: 'Original Title', location: 'Dublin' },
    );
    expect(persisted).toEqual({ location: 'Dublin' });
    expect(notStored).toEqual({ title: 'Renamed' });
  });

  it('never merges a column we did not send', () => {
    // The PATCH response is the raw Supabase row, and `resume_s3_url` holds a
    // stale presigned URL that the page refreshes elsewhere. Spreading the
    // whole row over local state would clobber the fresh one.
    const { persisted } = resolveSaveOutcome(
      { location: 'Dublin' },
      { job_id: 'job-1', location: 'Dublin', resume_s3_url: 'https://stale.example/expired' },
    );
    expect(persisted).toEqual({ location: 'Dublin' });
    expect(persisted).not.toHaveProperty('resume_s3_url');
  });

  it('treats a non-object response as nothing stored', () => {
    for (const bad of [null, undefined, 'ok', 42, []]) {
      const { persisted, notStored } = resolveSaveOutcome({ location: 'Dublin' }, bad);
      expect(persisted).toEqual({});
      expect(notStored).toEqual({ location: 'Dublin' });
    }
  });

  it('reports nothing for an empty send', () => {
    expect(resolveSaveOutcome({}, { job_id: 'job-1' })).toEqual({
      persisted: {},
      notStored: {},
    });
  });
});
