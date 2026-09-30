/**
 * Decide what a job-details save actually persisted.
 *
 * The inline save this replaces did:
 *
 *     await apiPatch(`/api/dashboard/jobs/${job.job_id}`, editFields);
 *     setJob((prev) => ({ ...prev, ...editFields }));
 *     setSaveStatus({ type: 'success', message: 'Job updated.' });
 *
 * `editFields` was seeded with title, company, location and apply_url, but
 * app.py's `_EDITABLE_FIELDS` was `{application_status, location, apply_url}`
 * and filtered the body down to those keys. Title and company were dropped.
 * The request still answered 200 because `update_data` was non-empty, so the
 * user edited the title, saw the new title (the optimistic merge put it there),
 * read "Job updated." — and got the old title back on reload. CLAUDE.md rule 2:
 * a status that cannot tell "did the work" from "did part of it" is a lie.
 *
 * So the merge and the success message must be derived from the server's
 * response — what it stored — never from what we hoped it would store.
 *
 * Only the keys we sent are inspected. The PATCH response is the raw Supabase
 * row and carries columns like `resume_s3_url` whose stored value is a stale
 * presigned URL; the caller refreshes those elsewhere, so spreading the whole
 * row over local state would undo that. Hence a field-by-field merge.
 *
 * Returns { persisted, notStored } — both plain objects keyed by field name,
 * `persisted` holding the value the server reports as stored.
 */
export function resolveSaveOutcome(sent, stored) {
  const persisted = {};
  const notStored = {};
  const isRow = stored !== null && typeof stored === 'object' && !Array.isArray(stored);

  for (const field of Object.keys(sent || {})) {
    // Absent from the response, or present with a different value, both mean
    // the same thing to the user: this edit is not in the database. A missing
    // key must not read as success — that is the exact hole being closed.
    if (isRow && Object.prototype.hasOwnProperty.call(stored, field) && stored[field] === sent[field]) {
      persisted[field] = stored[field];
    } else {
      notStored[field] = sent[field];
    }
  }
  return { persisted, notStored };
}
