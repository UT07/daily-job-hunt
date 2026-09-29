/**
 * Anchoring, staleness and undo — the whole of Phase 3's correctness.
 *
 * A suggestion is generated against one version of the document and applied
 * against another. Between those two moments the user is typing. The spec
 * (§5.3) requires that a suggestion "points at a specific place" and greys out
 * when the text it was written against has changed, rather than silently
 * applying to content it was not written for.
 *
 * The anchor is therefore two things, not one:
 *
 *   path        where it was — ["experience", 0, "bullets", 2]
 *   anchor_text what was there — the verbatim source string
 *
 * `path` alone is not an anchor. Delete the bullet above and index 2 is now a
 * different sentence; the path still resolves, and applying would overwrite
 * something the suggestion never saw. `anchor_text` alone is not an anchor
 * either: it says what but not where, and identical text can occur twice.
 *
 * So resolution is: try the path; if the text there no longer matches, look
 * for the exact text among the siblings of the last indexed step (the same
 * bullet list, or the same skills list); if it is nowhere, the suggestion is
 * stale. Three outcomes, and only the third disables Apply:
 *
 *   exact  the text is where it was
 *   moved  the text is intact but its index shifted — apply THERE
 *   stale  the text is gone or rewritten — grey out, offer re-analyse
 *
 * The search is confined to the siblings of the last indexed step, never the
 * whole document, so a summary rewrite can never relocate into a bullet.
 *
 * Undo is the same operation with the two texts swapped, which is why it costs
 * no round trip to the model and why it refuses for the same reason Apply
 * does: if you edited the applied text afterwards, undo would clobber it.
 */
import { describe, expect, it } from 'vitest';
import { applySuggestion, getAtPath, resolveAnchor } from '../suggestions';

const BULLET = 'Developed React frontends for internal ops dashboards';

function baseSections() {
  return {
    summary: 'Platform engineer with six years across cloud infrastructure.',
    skills: [
      { category: 'Cloud', items: 'AWS, GCP' },
      { category: 'IaC', items: 'Terraform' },
    ],
    experience: [
      {
        company: 'Clover IT Services',
        title: 'Engineer',
        bullets: ['Maintained CI pipelines', BULLET],
      },
      { company: 'Acme', title: 'SRE', bullets: ['Ran Kubernetes'] },
    ],
  };
}

const SUGGESTION = {
  id: 's4',
  path: ['experience', 0, 'bullets', 1],
  label: 'Experience · Clover IT Services',
  anchor_text: BULLET,
  replacement: 'Shipped React ops dashboards used by 400 staff daily',
  why: 'no quantified impact, and the JD asks for scale',
};

describe('getAtPath', () => {
  it('resolves every shape the editor can produce', () => {
    const s = baseSections();
    expect(getAtPath(s, ['summary'])).toBe(s.summary);
    expect(getAtPath(s, ['skills', 1, 'items'])).toBe('Terraform');
    expect(getAtPath(s, ['experience', 0, 'bullets', 1])).toBe(BULLET);
  });

  it('returns undefined rather than throwing on a path that no longer exists', () => {
    const s = baseSections();
    expect(getAtPath(s, ['experience', 9, 'bullets', 0])).toBeUndefined();
    expect(getAtPath(s, ['projects', 0, 'bullets', 0])).toBeUndefined();
    expect(getAtPath(s, ['skills'])).toBeUndefined(); // an array is not text
    expect(getAtPath(null, ['summary'])).toBeUndefined();
  });
});

describe('resolveAnchor', () => {
  it('is exact against the document it was generated from', () => {
    expect(resolveAnchor(baseSections(), SUGGESTION))
      .toEqual({ status: 'exact', path: ['experience', 0, 'bullets', 1] });
  });

  it('survives the user editing somewhere else entirely', () => {
    // The point of per-section anchoring: rewriting the summary must not
    // invalidate advice about a bullet.
    const s = baseSections();
    s.summary = 'Completely different summary.';
    s.skills[0].items = 'AWS, GCP, Azure';
    expect(resolveAnchor(s, SUGGESTION).status).toBe('exact');
  });

  it('follows its text when an insertion above shifts the index', () => {
    const s = baseSections();
    s.experience[0].bullets = ['A brand new first bullet', ...s.experience[0].bullets];
    expect(resolveAnchor(s, SUGGESTION))
      .toEqual({ status: 'moved', path: ['experience', 0, 'bullets', 2] });
  });

  it('follows its text when a deletion above shifts the index', () => {
    const s = baseSections();
    s.experience[0].bullets = [BULLET];
    expect(resolveAnchor(s, SUGGESTION))
      .toEqual({ status: 'moved', path: ['experience', 0, 'bullets', 0] });
  });

  it('goes stale when the anchored text itself was edited', () => {
    const s = baseSections();
    s.experience[0].bullets[1] = `${BULLET} for 400 staff`;
    expect(resolveAnchor(s, SUGGESTION)).toEqual({ status: 'stale', path: null });
  });

  it('goes stale when the bullet was deleted', () => {
    const s = baseSections();
    s.experience[0].bullets = ['Maintained CI pipelines'];
    expect(resolveAnchor(s, SUGGESTION).status).toBe('stale');
  });

  it('goes stale when the whole employer was removed', () => {
    const s = baseSections();
    s.experience = [s.experience[1]];
    expect(resolveAnchor(s, SUGGESTION).status).toBe('stale');
  });

  it('follows a skills line when the categories are reordered', () => {
    const s = baseSections();
    s.skills = [s.skills[1], s.skills[0]];
    const sug = { id: 's2', path: ['skills', 0, 'items'], anchor_text: 'AWS, GCP', replacement: 'AWS, GCP, Kubernetes' };
    expect(resolveAnchor(s, sug)).toEqual({ status: 'moved', path: ['skills', 1, 'items'] });
  });

  it('does not relocate a summary suggestion into a bullet that happens to match', () => {
    const s = baseSections();
    const text = 'Platform engineer with six years across cloud infrastructure.';
    s.summary = 'Rewritten.';
    s.experience[0].bullets.push(text);
    const sug = { id: 's1', path: ['summary'], anchor_text: text, replacement: 'Sharper summary.' };
    expect(resolveAnchor(s, sug).status).toBe('stale');
  });
});

describe('applySuggestion', () => {
  it('replaces the anchored text and leaves everything else alone', () => {
    const before = baseSections();
    const { sections } = applySuggestion(before, SUGGESTION);
    expect(sections.experience[0].bullets[1]).toBe(SUGGESTION.replacement);
    expect(sections.experience[0].bullets[0]).toBe('Maintained CI pipelines');
    expect(sections.summary).toBe(before.summary);
    expect(sections.skills).toEqual(before.skills);
  });

  it('does not mutate the sections it was given', () => {
    const before = baseSections();
    applySuggestion(before, SUGGESTION);
    expect(before.experience[0].bullets[1]).toBe(BULLET);
  });

  it('writes at the resolved path, not the one it was generated with', () => {
    const s = baseSections();
    s.experience[0].bullets = ['A brand new first bullet', ...s.experience[0].bullets];
    const { sections } = applySuggestion(s, SUGGESTION);
    expect(sections.experience[0].bullets[2]).toBe(SUGGESTION.replacement);
    expect(sections.experience[0].bullets[0]).toBe('A brand new first bullet');
  });

  it('refuses a stale suggestion rather than applying it to changed text', () => {
    const s = baseSections();
    s.experience[0].bullets[1] = 'The user already rewrote this themselves';
    expect(applySuggestion(s, SUGGESTION)).toBeNull();
    expect(s.experience[0].bullets[1]).toBe('The user already rewrote this themselves');
  });
});

describe('undo', () => {
  it('round-trips exactly, with no call to the model', () => {
    const before = baseSections();
    const { sections: applied, undo } = applySuggestion(before, SUGGESTION);
    const { sections: reverted } = applySuggestion(applied, undo);
    expect(reverted).toEqual(before);
  });

  it('is itself an anchored suggestion, so it survives an edit elsewhere', () => {
    const { sections: applied, undo } = applySuggestion(baseSections(), SUGGESTION);
    applied.summary = 'Changed after applying.';
    const { sections: reverted } = applySuggestion(applied, undo);
    expect(reverted.experience[0].bullets[1]).toBe(BULLET);
    expect(reverted.summary).toBe('Changed after applying.');
  });

  it('refuses once the applied text has itself been edited', () => {
    // Undoing here would throw away the user's own words, which is exactly
    // what the staleness rule exists to prevent — in both directions.
    const { sections: applied, undo } = applySuggestion(baseSections(), SUGGESTION);
    applied.experience[0].bullets[1] = 'Shipped React ops dashboards used by 400 staff daily, on call';
    expect(applySuggestion(applied, undo)).toBeNull();
  });

  it('carries the suggestion id so the panel can pair them', () => {
    const { undo } = applySuggestion(baseSections(), SUGGESTION);
    expect(undo.id).toBe(SUGGESTION.id);
  });
});
