/**
 * Add Job reads the company, and sometimes the title and location, off the
 * paste. "i hate the add job form it should pickup the details like company
 * name, location, title etc" — 2026-10-08.
 *
 * The rules those fields follow, and why:
 *
 *   fill blanks only. A wrong value in a box the user already filled is worse
 *   than an empty box, because nobody re-reads a field they typed. So a value
 *   the user supplied is never overwritten, and neither is one they cleared.
 *
 *   say where it came from. The apply link is a fact (26 of 26 right against
 *   real pastes); the description is a guess. The hint wording differs.
 *
 * Precision was measured before any of this shipped — see jdExtract.test.js.
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const { apiCall, pollPipeline } = vi.hoisted(() => ({
  apiCall: vi.fn(), pollPipeline: vi.fn(),
}));
vi.mock('../../api', () => ({ apiCall, pollPipeline }));

import AddJob from '../AddJob';

const STORAGE_KEY = 'naukribaba_addjob_draft';
const JD = 'About Grinds360\n\nGrinds360 is an education platform hiring engineers to '
         + 'scale our Kubernetes estate and own reliability end to end.';

const field = (name) => screen.getByLabelText(name);
const type = (name, value) => fireEvent.change(field(name), { target: { value } });

describe('Add Job picks details out of the paste', () => {
  beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear(); });

  it('fills the company from the pasted description', () => {
    render(<AddJob />);
    expect(field('Company')).toHaveValue('');
    type('Job Description', JD);
    expect(field('Company')).toHaveValue('Grinds360');
  });

  it('prefers the apply link over the description', () => {
    render(<AddJob />);
    // The description says Grinds360; the link says gitlab. The link wins,
    // because it is the signal that measured 100% on real pastes.
    type('Apply URL (optional)', 'https://job-boards.greenhouse.io/gitlab/jobs/8654230002');
    expect(field('Company')).toHaveValue('Gitlab');
  });

  it('reads the company off a company careers domain', () => {
    render(<AddJob />);
    type('Apply URL (optional)', 'https://careers.datadoghq.com/detail/8080628/');
    expect(field('Company')).toHaveValue('Datadog');
  });

  it('fills a labelled title and location', () => {
    render(<AddJob />);
    type('Job Description',
      'Job Title: Staff Site Reliability Engineer\nLocation: Dublin City Centre\n\n'
      + 'You will own our platform and its reliability targets end to end.');
    expect(field('Job Title')).toHaveValue('Staff Site Reliability Engineer');
    expect(field('Location (optional)')).toHaveValue('Dublin City Centre');
  });

  // One render per test: two in the same block leaves both trees mounted, so
  // every `screen` query sees duplicate labels.
  it('says a company came from the apply link', () => {
    render(<AddJob />);
    type('Apply URL (optional)', 'https://job-boards.greenhouse.io/zscaler/jobs/5045817007');
    expect(screen.getByText(/auto-filled from the apply link/i)).toBeInTheDocument();
  });

  it('words a guess at prose differently from a fact read off a link', () => {
    render(<AddJob />);
    type('Job Description', JD);
    expect(screen.getByText(/auto-filled from the description/i)).toBeInTheDocument();
    expect(screen.queryByText(/from the apply link/i)).not.toBeInTheDocument();
  });

  // ---- The rule that matters most ----

  it('never overwrites a company the user typed', () => {
    render(<AddJob />);
    type('Company', 'Grinds 360 Ltd');          // the user's own spelling
    type('Job Description', JD);                 // would detect "Grinds360"
    expect(field('Company')).toHaveValue('Grinds 360 Ltd');
  });

  it('never refills a field the user deliberately cleared', () => {
    render(<AddJob />);
    type('Job Description', JD);
    expect(field('Company')).toHaveValue('Grinds360');
    type('Company', '');                         // the user empties it on purpose
    type('Job Description', `${JD} Additional responsibilities apply.`);
    expect(field('Company'), 'a cleared field was refilled behind the user')
      .toHaveValue('');
  });

  it('drops the auto-filled hint once the user edits that field', () => {
    render(<AddJob />);
    type('Job Description', JD);
    expect(screen.getAllByText(/auto-filled/i).length).toBeGreaterThan(0);
    type('Company', 'Acme');
    expect(screen.queryByText(/auto-filled/i)).not.toBeInTheDocument();
  });

  it('does not overwrite a title restored from the draft', () => {
    // The draft is the user's own earlier work, so it counts as typed.
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ job_title: 'Platform Engineer' }));
    render(<AddJob />);
    type('Job Description', 'Job Title: Staff SRE\n\nYou will own the platform.');
    expect(field('Job Title')).toHaveValue('Platform Engineer');
  });

  it('leaves the title at its default when the paste does not state one', () => {
    render(<AddJob />);
    type('Job Description', JD);   // no "Job Title:" line
    expect(field('Job Title')).toHaveValue('Software Engineer');
  });

  it('leaves the location empty rather than guessing it', () => {
    render(<AddJob />);
    // "hybrid cloud" used to be read as the location "Hybrid" — it fired on 15
    // of 37 real pastes at 47% precision and would have replaced a real office.
    type('Job Description',
      'About Toast\n\nToast is a platform company. You will build hybrid cloud '
      + 'infrastructure and work remote-first with our San Jose teams.');
    expect(field('Location (optional)')).toHaveValue('');
  });
});

describe('a restored draft announces itself', () => {
  beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear(); });

  it('says so, and offers a way out', () => {
    // Reported 2026-10-08: the previous night's Grinds360 JD was still in the
    // box when the user came to add a different job, with nothing on screen to
    // say it had been restored.
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ jd: JD, company: 'Grinds360' }));
    render(<AddJob />);
    expect(screen.getByText(/restored the draft/i)).toBeInTheDocument();

    fireEvent.click(screen.getByRole('button', { name: /start fresh/i }));
    expect(field('Job Description')).toHaveValue('');
    expect(field('Company')).toHaveValue('');
    expect(field('Job Title')).toHaveValue('Software Engineer');
    expect(screen.queryByText(/restored the draft/i)).not.toBeInTheDocument();
    expect(sessionStorage.getItem(STORAGE_KEY)).toBeNull();
  });

  it('says nothing on a genuinely fresh form', () => {
    render(<AddJob />);
    expect(screen.queryByText(/restored the draft/i)).not.toBeInTheDocument();
  });

  it('does not resurrect the draft after Start fresh', () => {
    sessionStorage.setItem(STORAGE_KEY, JSON.stringify({ jd: JD }));
    const { unmount } = render(<AddJob />);
    fireEvent.click(screen.getByRole('button', { name: /start fresh/i }));
    unmount();
    render(<AddJob />);
    expect(field('Job Description')).toHaveValue('');
  });
});
