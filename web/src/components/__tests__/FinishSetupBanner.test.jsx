/**
 * FinishSetupBanner must name what is actually missing.
 *
 * The backend decides completeness (shared/profile_completeness.py):
 * first_name, last_name, email, phone, linkedin, visa_status,
 * work_authorizations, notice_period_text. first/last are derived in app.py's
 * PUT /api/profile as `v.strip().split(" ", 1)`, with last_name = parts[1]
 * stripped, or '' for a one-word name.
 *
 * The banner checked `full_name` for mere presence, so a one-word name ("Cher")
 * left the backend saying incomplete and the banner listing nothing: "Complete
 * setup to enable auto-apply." with no way to tell what to fix.
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen } from '@testing-library/react';
import { MemoryRouter } from 'react-router-dom';

vi.mock('../../hooks/useUserProfile', () => ({ useUserProfile: vi.fn() }));
import { useUserProfile } from '../../hooks/useUserProfile';
import FinishSetupBanner from '../FinishSetupBanner';

const FILLED = {
  email: 'a@b.c', full_name: 'Jane Doe', phone: '+353 1',
  linkedin_url: 'https://linkedin.com/in/jane', visa_status: 'Stamp 1G',
  work_authorizations: { Ireland: 'stamp_1g' }, notice_period_text: '2 weeks',
  profile_complete: false,
};

function renderWith(profile) {
  useUserProfile.mockReturnValue({ profile });
  return render(<MemoryRouter><FinishSetupBanner /></MemoryRouter>);
}

describe('FinishSetupBanner', () => {
  it('names the real reason when the only gap is a one-word name', () => {
    renderWith({ ...FILLED, full_name: 'Cher' });
    expect(screen.getByText(/Full name needs a first and last name/)).toBeInTheDocument();
  });

  it('treats a name that is one word after trimming as one word', () => {
    renderWith({ ...FILLED, full_name: '  Cher  ' });
    expect(screen.getByText(/Full name needs a first and last name/)).toBeInTheDocument();
  });

  it('still lists an absent name as missing', () => {
    renderWith({ ...FILLED, full_name: '' });
    expect(screen.getByText(/Full name/)).toBeInTheDocument();
    expect(screen.queryByText(/needs a first and last name/)).not.toBeInTheDocument();
  });

  it('lists ordinary missing fields', () => {
    renderWith({ ...FILLED, phone: '', linkedin_url: null });
    expect(screen.getByText(/Phone, LinkedIn URL/)).toBeInTheDocument();
  });

  it('accepts a two-word name', () => {
    renderWith({ ...FILLED, phone: '' });
    expect(screen.queryByText(/Full name/)).not.toBeInTheDocument();
  });
});
