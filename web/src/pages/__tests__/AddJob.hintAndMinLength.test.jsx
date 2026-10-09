/**
 * Add Job: two small things the user could see.
 *
 * 1. DetectedHint wrote `—` in JSX TEXT. JSX text is not a JS string
 *    literal, so the escape is not processed: the hint literally read
 *    "auto-filled from the apply link — edit if wrong".
 *
 * 2. Every action button was enabled for any non-empty JD, but the backend
 *    rejects `job_description` under 20 characters with a 422: ScoreRequest,
 *    SingleJobRunRequest, CoverLetterRequest and ContactsRequest in app.py all
 *    declare Field(..., min_length=20).
 */
import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';

const { apiCall, pollPipeline } = vi.hoisted(() => ({
  apiCall: vi.fn(), pollPipeline: vi.fn(),
}));
vi.mock('../../api', () => ({ apiCall, pollPipeline }));
vi.mock('../../auth/useAuth', () => ({
  useAuth: () => ({ user: { id: 'u1', email: 'a@b.com' }, loading: false }),
}));

import AddJob from '../AddJob';

const ACTIONS = ['Save & Score', 'Tailor Resume', 'Cover Letter', 'Find Contacts'];
const button = (name) => screen.getByRole('button', { name });
const typeJd = (value) =>
  fireEvent.change(screen.getByLabelText('Job Description'), { target: { value } });

beforeEach(() => { vi.clearAllMocks(); sessionStorage.clear(); });

describe('DetectedHint', () => {
  it('renders an em dash, not the escape sequence', () => {
    render(<AddJob />);
    fireEvent.change(screen.getByLabelText('Apply URL (optional)'), {
      target: { value: 'https://job-boards.greenhouse.io/gitlab/jobs/8654230002' },
    });
    const hint = screen.getByText(/auto-filled from the apply link/i);
    expect(hint.textContent).toContain('—');
    expect(hint.textContent).not.toContain('\\u2014');
  });
});

describe('JD minimum length (app.py min_length=20)', () => {
  it('disables every action below 20 characters and says why', () => {
    render(<AddJob />);
    typeJd('Too short JD here'); // 17
    for (const name of ACTIONS) expect(button(name)).toBeDisabled();
    expect(screen.getByText(/at least 20 characters/i)).toBeInTheDocument();
    fireEvent.click(button('Tailor Resume'));
    expect(apiCall).not.toHaveBeenCalled();
  });

  it('counts the trimmed text, so padding does not unlock the buttons', () => {
    render(<AddJob />);
    typeJd('   short    \n\n\n          ');
    for (const name of ACTIONS) expect(button(name)).toBeDisabled();
  });

  it('enables the actions at exactly 20 characters', () => {
    render(<AddJob />);
    typeJd('12345678901234567890');
    for (const name of ACTIONS) expect(button(name)).not.toBeDisabled();
    expect(screen.queryByText(/at least 20 characters/i)).not.toBeInTheDocument();
  });
});
