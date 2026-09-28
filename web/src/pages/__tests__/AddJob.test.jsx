/**
 * AddJob used to hold jd/jobTitle/company/location/applyUrl/resumeType in
 * plain useState with no persistence, so navigating away from the page threw
 * away a job description the user had just pasted (2026-09-28 report).
 *
 * The draft now round-trips through sessionStorage, following the shape
 * Dashboard already uses for its filters (a DEFAULTS map + a per-field reader
 * in lazy useState initialisers + one write-back effect) — sessionStorage
 * rather than the query string because a JD is far too long for a URL and has
 * no business in browser history.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const { apiCall, pollPipeline } = vi.hoisted(() => ({
  apiCall: vi.fn(),
  pollPipeline: vi.fn(),
}));
vi.mock('../../api', () => ({ apiCall, pollPipeline }));

import AddJob from '../AddJob';

const STORAGE_KEY = 'naukribaba_addjob_draft';
const JD = 'We are hiring a Site Reliability Engineer to own our Kubernetes platform.';

describe('AddJob draft persistence', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    sessionStorage.clear();
  });

  it('restores everything the user typed after navigating away and back', () => {
    const { unmount } = render(<AddJob />);

    fireEvent.change(screen.getByLabelText('Job Description'), { target: { value: JD } });
    fireEvent.change(screen.getByLabelText('Job Title'), { target: { value: 'SRE' } });
    fireEvent.change(screen.getByLabelText('Company'), { target: { value: 'Stripe' } });
    fireEvent.change(screen.getByLabelText('Location (optional)'), { target: { value: 'Dublin' } });
    fireEvent.change(screen.getByLabelText('Apply URL (optional)'), { target: { value: 'https://x.test/1' } });
    fireEvent.change(screen.getByLabelText('Resume Type'), { target: { value: 'fullstack' } });

    // Navigate away.
    unmount();

    // ...and back.
    render(<AddJob />);
    expect(screen.getByLabelText('Job Description')).toHaveValue(JD);
    expect(screen.getByLabelText('Job Title')).toHaveValue('SRE');
    expect(screen.getByLabelText('Company')).toHaveValue('Stripe');
    expect(screen.getByLabelText('Location (optional)')).toHaveValue('Dublin');
    expect(screen.getByLabelText('Apply URL (optional)')).toHaveValue('https://x.test/1');
    expect(screen.getByLabelText('Resume Type')).toHaveValue('fullstack');
  });

  it('persists only the fields that differ from their defaults', () => {
    render(<AddJob />);

    fireEvent.change(screen.getByLabelText('Job Description'), { target: { value: JD } });

    // Job Title is still 'Software Engineer' and Resume Type still
    // 'sre_devops', so neither should be written — same rule Dashboard's URL
    // sync applies to keep the query string clean.
    expect(JSON.parse(sessionStorage.getItem(STORAGE_KEY))).toEqual({ jd: JD });
  });

  it('leaves no draft behind for a pristine form', () => {
    render(<AddJob />);
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('clears the stored draft once the user empties the form again', () => {
    render(<AddJob />);

    const jdField = screen.getByLabelText('Job Description');
    fireEvent.change(jdField, { target: { value: JD } });
    expect(sessionStorage.getItem(STORAGE_KEY)).not.toBeNull();

    fireEvent.change(jdField, { target: { value: '' } });
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('falls back to an empty form when the stored draft is corrupt', () => {
    sessionStorage.setItem(STORAGE_KEY, 'not json{');

    render(<AddJob />);

    expect(screen.getByLabelText('Job Description')).toHaveValue('');
    expect(screen.getByLabelText('Job Title')).toHaveValue('Software Engineer');
  });

  it('ignores non-string values in a stored draft rather than rendering them', () => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ jd: { nope: true }, company: 'Stripe' }));

    render(<AddJob />);

    expect(screen.getByLabelText('Job Description')).toHaveValue('');
    expect(screen.getByLabelText('Company')).toHaveValue('Stripe');
  });
});
