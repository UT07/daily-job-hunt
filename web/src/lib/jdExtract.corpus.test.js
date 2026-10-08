/**
 * Measures jdExtract against the real pasted-JD corpus, when it is present.
 *
 * The corpus is gitignored (it is the user's own job-search history and this
 * repo is public), so in CI this file reports a skip rather than a pass. That
 * distinction matters: a skip that printed "ok" would be exactly the kind of
 * status CLAUDE.md #2 is about. Regenerate locally with
 * `.venv/bin/python scripts/dump_jd_fixture.py`.
 *
 * The floors are set at what was measured, minus nothing. Tuning a floor down
 * to make this green is the wrong move -- if a change lowers precision, the
 * change is what to look at.
 */
import { describe, it, expect } from 'vitest';
import fs from 'node:fs';
import path from 'node:path';
import { extractJobFields } from './jdExtract';

const FIXTURE = path.join(__dirname, '__fixtures__', 'real_jds.json');
const present = fs.existsSync(FIXTURE);

const norm = (s) => (s || '').toLowerCase().replace(/[^a-z0-9]/g, '');
const agrees = (a, b) => {
  const [x, y] = [norm(a), norm(b)];
  return Boolean(x && y) && (x === y || x.includes(y) || y.includes(x));
};

describe.skipIf(!present)('jdExtract against real pasted JDs', () => {
  const cases = present ? JSON.parse(fs.readFileSync(FIXTURE, 'utf8')) : [];

  it('has enough cases to mean anything', () => {
    expect(cases.length).toBeGreaterThanOrEqual(30);
  });

  it('proposes a company for most pastes and is almost always right', () => {
    let proposed = 0, right = 0;
    for (const c of cases) {
      const got = extractJobFields(c.jd, c.apply_url).fields.company;
      if (!got) continue;
      proposed++;
      if (agrees(got, c.expect.company)) right++;
    }
    const fill = proposed / cases.length;
    const precision = right / proposed;
    // Measured 2026-10-08: 76% fill, 96% precision.
    expect(fill, `company fill rate ${(fill * 100).toFixed(0)}%`).toBeGreaterThan(0.6);
    expect(precision, `company precision ${(precision * 100).toFixed(0)}%`).toBeGreaterThan(0.9);
  });

  it('is right every time it reads the company off a URL', () => {
    // 26 of 28 proposals come from a URL and all 26 agreed. This is the signal
    // the feature rests on, so it is pinned separately from the body fallback.
    let proposed = 0, right = 0;
    for (const c of cases) {
      const { fields, sources } = extractJobFields(c.jd, c.apply_url);
      if (!fields.company || !['ats-url', 'domain'].includes(sources.company)) continue;
      proposed++;
      if (agrees(fields.company, c.expect.company)) right++;
    }
    expect(proposed).toBeGreaterThan(20);
    expect(right, `${proposed - right} of ${proposed} URL-derived companies disagreed`)
      .toBe(proposed);
  });

  it('almost never proposes a location, which is the point', () => {
    // Deliberately a CEILING, not a floor. Every attempt to raise this number
    // was measured and refused: arrangement-words 47% right, company-HQ prose
    // 67% right. If a change makes this fire often, it has reintroduced a
    // guess. CLAUDE.md #16.
    const proposed = cases.filter(
      (c) => extractJobFields(c.jd, c.apply_url).fields.location).length;
    expect(proposed / cases.length,
      `location fired on ${proposed}/${cases.length} pastes — verify precision before raising`)
      .toBeLessThan(0.2);
  });

  it('never proposes a title it was not told', () => {
    let proposed = 0, right = 0;
    for (const c of cases) {
      const got = extractJobFields(c.jd, c.apply_url).fields.title;
      if (!got) continue;
      proposed++;
      if (agrees(got, c.expect.title)) right++;
    }
    expect(right, `${proposed - right} of ${proposed} titles disagreed`).toBe(proposed);
  });

  it('never throws on a real paste', () => {
    for (const c of cases) {
      expect(() => extractJobFields(c.jd, c.apply_url)).not.toThrow();
    }
  });
});

describe.skipIf(present)('corpus absent', () => {
  it('is not a pass', () => {
    // Visible in the CI log, so "20 passed" is never mistaken for
    // "the real-data measurement ran".
    console.warn(
      '[jdExtract] real-JD corpus not present — precision was NOT measured in '
      + 'this run. Regenerate with scripts/dump_jd_fixture.py.');
    expect(present).toBe(false);
  });
});
