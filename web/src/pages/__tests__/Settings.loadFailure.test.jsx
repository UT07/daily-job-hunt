/**
 * A Settings section whose load failed must not be saveable.
 *
 * Before this, a failed GET /api/profile was only console.warn'd. The form
 * stayed at its blank initial state, Save stayed enabled, and clicking it sent
 * `name: ''`, `visa_status: ''`, `work_authorizations: {}` -- which the backend
 * writes (PUT /api/profile only drops None, not ''), followed by "Profile
 * saved." The same for /api/search-config: the component defaults
 * (min_match_score 60, days_back 7, ...) were upserted over the real config.
 *
 * Shapes match app.py: GET /api/profile returns ProfileResponse (full_name,
 * work_authorizations as a dict), GET /api/search-config returns the stored
 * row or {}.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Settings from '../Settings';
import * as api from '../../api';
import * as useAuthModule from '../../auth/useAuth';

const SERVER_PROFILE = {
  id: 'u1',
  email: 'a@b.com',
  full_name: 'Server Name',
  phone: '+353 1',
  location: 'Dublin, Ireland',
  github_url: null,
  linkedin_url: null,
  website: null,
  visa_status: 'Stamp 1G',
  work_authorizations: { Ireland: 'stamp_1g' },
  plan: 'free',
  salary_expectation_notes: '',
  notice_period_text: '',
  profile_complete: true,
};

const SERVER_CONFIG = {
  queries: ['sre'], locations: ['Dublin'], experience_levels: ['mid_level'],
  days_back: 14, max_jobs_per_run: 25, min_match_score: 85,
  enabled_sources: ['linkedin'],
};

function mockApi({ profileFails = false, configFails = false } = {}) {
  const calls = { profile: 0, config: 0 };
  vi.spyOn(api, 'apiGet').mockImplementation((endpoint) => {
    if (endpoint === '/api/profile') {
      calls.profile += 1;
      return profileFails && calls.profile === 1
        ? Promise.reject(new Error('503 Service Unavailable'))
        : Promise.resolve(SERVER_PROFILE);
    }
    if (endpoint === '/api/search-config') {
      calls.config += 1;
      return configFails
        ? Promise.reject(new Error('503 Service Unavailable'))
        : Promise.resolve(SERVER_CONFIG);
    }
    if (endpoint === '/api/resumes') return Promise.resolve({ resumes: [] });
    return Promise.resolve({});
  });
  vi.spyOn(api, 'apiPut').mockResolvedValue({});
  return calls;
}

const putsTo = (endpoint) => api.apiPut.mock.calls.filter(([ep]) => ep === endpoint);

function saveButtonIn(heading) {
  const card = screen.getByText(heading).closest('.space-y-6 > *') || document.body;
  return Array.from(card.querySelectorAll('button')).find((b) => /Save Changes/.test(b.textContent));
}

describe('Settings profile: a failed load cannot be saved over the real profile', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(useAuthModule, 'useAuth').mockReturnValue({
      user: { id: 'u1', email: 'a@b.com' }, loading: false,
    });
  });

  it('disables Save, shows the error, and issues no PUT when GET /api/profile fails', async () => {
    mockApi({ profileFails: true });
    render(<Settings />);

    expect(await screen.findByText(/Couldn't load your profile/i)).toBeInTheDocument();
    const save = saveButtonIn('Profile');
    expect(save).toBeDisabled();
    fireEvent.click(save);
    expect(putsTo('/api/profile')).toHaveLength(0);
    expect(screen.queryByText('Profile saved.')).not.toBeInTheDocument();
  });

  it('Retry reloads, then Save carries the loaded values', async () => {
    mockApi({ profileFails: true });
    render(<Settings />);

    fireEvent.click(await screen.findByRole('button', { name: /retry loading profile/i }));
    await waitFor(() => expect(screen.getByDisplayValue('Server Name')).toBeInTheDocument());

    const save = saveButtonIn('Profile');
    expect(save).not.toBeDisabled();
    fireEvent.click(save);
    await waitFor(() => expect(putsTo('/api/profile')).toHaveLength(1));
    const body = putsTo('/api/profile')[0][1];
    expect(body.name).toBe('Server Name');
    expect(body.visa_status).toBe('Stamp 1G');
    expect(body.work_authorizations).toEqual({ Ireland: 'stamp_1g' });
  });

  it('load succeeds -> PUT carries the loaded values, not blanks', async () => {
    mockApi();
    render(<Settings />);
    await waitFor(() => expect(screen.getByDisplayValue('Server Name')).toBeInTheDocument());

    fireEvent.click(saveButtonIn('Profile'));
    await waitFor(() => expect(putsTo('/api/profile')).toHaveLength(1));
    const body = putsTo('/api/profile')[0][1];
    expect(body).toMatchObject({
      name: 'Server Name',
      location: 'Dublin, Ireland',
      visa_status: 'Stamp 1G',
      work_authorizations: { Ireland: 'stamp_1g' },
    });
  });
});

describe('Settings preferences: a failed load cannot upsert defaults', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(useAuthModule, 'useAuth').mockReturnValue({
      user: { id: 'u1', email: 'a@b.com' }, loading: false,
    });
  });

  it('disables both search-config Saves and issues no PUT when GET /api/search-config fails', async () => {
    mockApi({ configFails: true });
    render(<Settings />);

    expect(await screen.findByText(/Couldn't load your search preferences/i)).toBeInTheDocument();
    expect(await screen.findByText(/Couldn't load your job sources/i)).toBeInTheDocument();

    const prefsSave = saveButtonIn('Search Preferences');
    const sourcesSave = screen.getByRole('button', { name: /Save Sources/ });
    expect(prefsSave).toBeDisabled();
    expect(sourcesSave).toBeDisabled();
    fireEvent.click(prefsSave);
    fireEvent.click(sourcesSave);
    expect(putsTo('/api/search-config')).toHaveLength(0);
  });

  it('load succeeds -> PUT carries the loaded config', async () => {
    mockApi();
    render(<Settings />);
    await waitFor(() => expect(document.querySelector('input[type="range"]')).toHaveValue('85'));

    fireEvent.click(saveButtonIn('Search Preferences'));
    await waitFor(() => expect(putsTo('/api/search-config')).toHaveLength(1));
    expect(putsTo('/api/search-config')[0][1]).toMatchObject({
      queries: ['sre'], days_back: 14, max_jobs_per_run: 25, min_match_score: 85,
    });
  });
});
