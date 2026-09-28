import { describe, expect, it } from 'vitest';
import { artifactStatus, expectedArtifacts, summarise } from '../artifactPolicy';

const R = 'https://s3/x.pdf';

describe('tier expectations', () => {
  it('gives A and S the full set, B a resume only', () => {
    expect(expectedArtifacts('S')).toEqual(['resume', 'cover_letter']);
    expect(expectedArtifacts('A')).toEqual(['resume', 'cover_letter']);
    expect(expectedArtifacts('B')).toEqual(['resume']);
  });

  it('expects nothing below B — tailoring everything is the problem, not the feature', () => {
    expect(expectedArtifacts('C')).toEqual([]);
    expect(expectedArtifacts('D')).toEqual([]);
    expect(expectedArtifacts(null)).toEqual([]);
    expect(expectedArtifacts('nonsense')).toEqual([]);
  });
});

describe('artifactStatus', () => {
  it('reports a B-tier job with a resume as complete', () => {
    const s = artifactStatus({ score_tier: 'B', resume_s3_url: R });
    expect(s.complete).toBe(true);
    expect(s.missing).toEqual([]);
  });

  it('does NOT require a cover letter at B', () => {
    const s = artifactStatus({ score_tier: 'B', resume_s3_url: R, cover_letter_s3_url: null });
    expect(s.complete).toBe(true);
  });

  it('names exactly what an A-tier job is missing', () => {
    const s = artifactStatus({ score_tier: 'A', resume_s3_url: R });
    expect(s.missing).toEqual(['cover_letter']);
    expect(s.present).toEqual(['resume']);
    expect(s.complete).toBe(false);
  });

  it('treats the e2e placeholder URL as absent', () => {
    // A real row in production carries this; trusting any non-empty string
    // would offer it as a download.
    const s = artifactStatus({ score_tier: 'B', resume_s3_url: 'https://example.invalid/e2e-resume.pdf' });
    expect(s.complete).toBe(false);
    expect(s.missing).toEqual(['resume']);
  });

  it('treats a non-URL string as absent', () => {
    expect(artifactStatus({ score_tier: 'B', resume_s3_url: 'pending' }).complete).toBe(false);
  });

  it('never calls a C-tier job complete, even with artifacts attached', () => {
    const s = artifactStatus({ score_tier: 'C', resume_s3_url: R });
    expect(s.applicable).toBe(false);
    expect(s.complete).toBe(false);
  });

  it('survives a missing job object', () => {
    expect(() => artifactStatus(undefined)).not.toThrow();
    expect(artifactStatus(undefined).applicable).toBe(false);
  });
});

describe('summarise', () => {
  it('counts only jobs the policy applies to', () => {
    const s = summarise([
      { score_tier: 'S', resume_s3_url: R, cover_letter_s3_url: R },  // complete
      { score_tier: 'A', resume_s3_url: R },                           // missing CL
      { score_tier: 'B', resume_s3_url: R },                           // complete
      { score_tier: 'C', resume_s3_url: R },                           // not applicable
      { score_tier: 'D' },                                             // not applicable
    ]);
    expect(s).toEqual({ total: 3, complete: 2, incomplete: 1 });
  });

  it('handles an empty or missing list', () => {
    expect(summarise([])).toEqual({ total: 0, complete: 0, incomplete: 0 });
    expect(summarise(undefined)).toEqual({ total: 0, complete: 0, incomplete: 0 });
  });
});
