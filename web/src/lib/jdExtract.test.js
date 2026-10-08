/**
 * Every case here comes from a real pasted job description.
 *
 * Measured against 37 real pastes (ground truth = the values the user typed
 * by hand), after the refusals below were applied:
 *
 *   company   28/37 proposed (76%), 96% right
 *               ats-url  13 proposed, 100% right
 *               domain   13 proposed, 100% right
 *               body      2 proposed,  50% right
 *   location   1/37 proposed  — labelled lines only
 *   title      2/37 proposed, 100% right
 *
 * The corpus itself is NOT in the repo: it is 37 job descriptions from the
 * user's own search, and this repo is public. `npm test` runs the behavioural
 * cases always and the corpus measurement only when the file is present, so CI
 * protects the behaviour without publishing the search history.
 */
import { describe, it, expect } from 'vitest';
import {
  extractJobFields, companyFromUrl, companyFromBody,
  locationFromBody, titleFromBody,
} from './jdExtract';

describe('company from the apply URL', () => {
  it('reads the employer out of an ATS path', () => {
    // All four shapes are from real rows.
    expect(companyFromUrl('https://job-boards.greenhouse.io/zscaler/jobs/5045817007'))
      .toMatchObject({ value: 'Zscaler', source: 'ats-url' });
    expect(companyFromUrl('https://job-boards.greenhouse.io/gitlab/jobs/8654230002'))
      .toMatchObject({ value: 'Gitlab', source: 'ats-url' });
    expect(companyFromUrl('https://app.careerpuck.com/job-board/choco/job/079ab75d'))
      .toMatchObject({ value: 'Choco', source: 'ats-url' });
    expect(companyFromUrl('https://jobs.lever.co/hatchet/abc-123'))
      .toMatchObject({ value: 'Hatchet', source: 'ats-url' });
  });

  it('reads the employer off its own careers domain', () => {
    expect(companyFromUrl('https://careers.datadoghq.com/detail/8080628/?gh_jid=8080628'))
      .toMatchObject({ value: 'Datadog', source: 'domain' });
    expect(companyFromUrl('https://stripe.com/jobs/search?gh_jid=8055050'))
      .toMatchObject({ value: 'Stripe', source: 'domain' });
  });

  it('returns nothing for an aggregator', () => {
    // The real LinkedIn job URL is numeric — there is no slug to read, and the
    // company is emphatically not "Linkedin".
    expect(companyFromUrl('https://www.linkedin.com/jobs/view/4476593709/')).toBeNull();
    expect(companyFromUrl('https://ie.indeed.com/viewjob?jk=abc123')).toBeNull();
    expect(companyFromUrl('https://www.glassdoor.ie/job-listing/x')).toBeNull();
  });

  it('returns nothing for a link shortener', () => {
    // Shipped once: grnh.se is Greenhouse's shortener and produced the
    // company "Grnh" on a real paste.
    expect(companyFromUrl('https://grnh.se/abc123def')).toBeNull();
  });

  it('survives junk instead of throwing', () => {
    for (const bad of ['', null, undefined, 'not a url', 'http://', '://x']) {
      expect(() => companyFromUrl(bad)).not.toThrow();
      expect(companyFromUrl(bad)).toBeNull();
    }
  });
});

describe('company from the body', () => {
  it('reads the standard openers', () => {
    expect(companyFromBody('About Stripe\n\nStripe is a financial infrastructure platform.'))
      .toMatchObject({ value: 'Stripe' });
    expect(companyFromBody('GitLab is the intelligent orchestration platform for DevSecOps.'))
      .toMatchObject({ value: 'GitLab' });
    expect(companyFromBody('At Toast, we empower restaurants.'))
      .toMatchObject({ value: 'Toast' });
  });

  it('collapses the name doubled by the copy', () => {
    // Verbatim from a real paste: the heading and the first sentence arrive
    // concatenated on one line.
    expect(companyFromBody('About Zscaler Zscaler accelerates digital transformation')
      .value).toBe('Zscaler');
  });

  it('does not run a capture across a line break', () => {
    // Shipped once. `\s+` between capitalised words matched newlines, so this
    // exact input produced the company "Job Description\n\nOnStar".
    const gm = 'Job Description\n\nOnStar is a cornerstone of General Motors';
    const got = companyFromBody(gm);
    if (got) {
      expect(got.value).not.toMatch(/\n/);
      expect(got.value.split(/\s+/).length).toBeLessThanOrEqual(4);
    }
  });

  it('rejects prose that merely starts with a capital', () => {
    for (const jd of ['The role is a senior position.', 'We are a fast-growing team.',
                      'This is an opportunity to join us.', 'Our team is a group of engineers.']) {
      const got = companyFromBody(jd);
      expect(got, `matched prose in ${JSON.stringify(jd)}`).toBeNull();
    }
  });
});

describe('location', () => {
  it('reads a labelled line', () => {
    expect(locationFromBody('Role: SRE\nLocation: Dublin City Centre\nType: Permanent'))
      .toMatchObject({ value: 'Dublin City Centre', source: 'label' });
  });

  // ---- The refusals. Each of these SHIPPED and was measured, then removed.
  // A test that only checks the happy path would let any of them back in.

  it('does NOT infer a location from the words remote or hybrid', () => {
    // 15 of 37 real pastes got an arrangement guess and it was right 47% of the
    // time. Its most common output was "Hybrid", matched on "hybrid cloud" in
    // SRE descriptions, and it would have overwritten a real Dublin office.
    expect(locationFromBody('You will build hybrid cloud infrastructure at scale.')).toBeNull();
    expect(locationFromBody('We run a remote-first engineering culture.')).toBeNull();
    expect(locationFromBody('Experience with on-site data centre migration.')).toBeNull();
  });

  it('does NOT offer the company HQ as the role location', () => {
    // "located in San Jose CA" in the about-us paragraph of a job whose actual
    // location is "Remote - USA". Offices get named in boilerplate far more
    // often than the role's own location does.
    // These two are the REAL shapes, taken from the corpus. An earlier version
    // of this test used "...in San Jose, California. This role is remote." and
    // "...in Galway. We are hiring." -- both of which the sentence-break guard
    // rejects on its own, so the test passed with the HQ pattern still in
    // place. The pattern's actual output on real pastes had no trailing
    // sentence to trip that guard. CLAUDE.md #6: a double that fails the way
    // the bug fails is worse than none.
    expect(locationFromBody(
      'Zscaler is located in San Jose CA and operates globally at scale',
    )).toBeNull();
    expect(locationFromBody(
      'TechHeads is based in Ireland with teams across EMEA',
    )).toBeNull();
  });

  it('does not capture into the next sentence', () => {
    const got = locationFromBody('Location: Galway. We are hiring across Europe.');
    if (got) expect(got.value).not.toMatch(/\bWe\b/);
  });
});

describe('title', () => {
  it('reads only a labelled title', () => {
    expect(titleFromBody('Job Title: Site Reliability Engineer\nAbout us...'))
      .toMatchObject({ value: 'Site Reliability Engineer' });
    expect(titleFromBody('Position: Senior Platform Engineer')).toMatchObject({
      value: 'Senior Platform Engineer' });
  });

  it('does NOT guess a title from the first line', () => {
    // Zero of 37 real pastes led with a title line. One begins "About Zscaler
    // Zscaler accelerates digital transformation to ensure our customers..."
    // as a single unwrapped line; another is a DOM scrape broken mid-sentence.
    expect(titleFromBody('About Zscaler Zscaler accelerates digital transformation')).toBeNull();
    expect(titleFromBody('GitLab is the intelligent orchestration platform.')).toBeNull();
  });

  it('does NOT guess a title from looking-for prose', () => {
    // A wrong title is the field the user is least likely to re-read before
    // submitting, and this family read sentence fragments far more often than
    // role names.
    expect(titleFromBody('We are looking for a passionate engineer to join us.')).toBeNull();
    expect(titleFromBody('We are seeking a highly motivated individual.')).toBeNull();
  });
});

describe('extractJobFields', () => {
  it('prefers the URL over the body for company', () => {
    const { fields, sources } = extractJobFields(
      'About Acme\nAcme is a great place.', 'https://job-boards.greenhouse.io/gitlab/jobs/1');
    expect(fields.company).toBe('Gitlab');
    expect(sources.company).toBe('ats-url');
  });

  it('falls back to the body when the URL is an aggregator', () => {
    const { fields, sources } = extractJobFields(
      'About Grinds360\nGrinds360 is an education platform.',
      'https://www.linkedin.com/jobs/view/4476593709/');
    expect(fields.company).toBe('Grinds360');
    expect(sources.company).toBe('body');
  });

  it('omits a field entirely rather than guessing it', () => {
    const { fields } = extractJobFields('Some generic text about the work.', '');
    expect(fields.title).toBeUndefined();
    expect(fields.location).toBeUndefined();
  });

  it('never throws on any input', () => {
    for (const jd of ['', null, undefined, 'x', 'a'.repeat(50000), '\n\n\n', '🎉']) {
      expect(() => extractJobFields(jd, null)).not.toThrow();
    }
  });
});
