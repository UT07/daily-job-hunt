/**
 * A late /api/profile response must not overwrite what the user has typed.
 *
 * Settings hydrates its form on mount:
 *
 *     apiGet('/api/profile').then(data => setProfile(prev => ({
 *       ...prev, name: data.full_name ?? prev.name, ...
 *     })))
 *
 * `data.full_name ?? prev.name` takes the server's value whenever it is not
 * null. So if the fetch resolves AFTER the user starts typing, their input is
 * silently reverted — and Save then submits the old value, showing one thing
 * and sending another. Same shape as the job-edit defect in #165.
 *
 * This was caught in production CI, not in review:
 * tests/e2e/test_critical_journeys.py::TestSettings::test_profile_update failed
 * on main at e82c2db and again at 5bc82c4 with
 *
 *     AssertionError: assert 'E2E Test User' == 'Renamed User'
 *
 * — the PUT carrying the pre-typed name. It passes locally 3/3 and fails on the
 * slower runner, which is a load-sensitive race rather than a flake. A previous
 * session diagnosed it as one, reasoning correctly that the PR merged alongside
 * it could not have caused it and then jumping to "therefore transient".
 *
 * These tests make it DETERMINISTIC: the fetch is held open until after the
 * keystroke, so the ordering is chosen rather than raced (CLAUDE.md rule 12 —
 * fix the instrument). The E2E test stays as the integration proof.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Settings from '../Settings';
import * as api from '../../api';
import * as useAuthModule from '../../auth/useAuth';

const SERVER_PROFILE = {
  full_name: 'Server Name',
  email: 'a@b.com',
  phone: '+353 1',
  location: 'Dublin, Ireland',
  github_url: '',
  linkedin_url: '',
  website: '',
  visa_status: '',
  work_authorizations: { Ireland: 'stamp_1g' },
  salary_expectation_notes: '',
  notice_period_text: '',
};

/** apiGet for /api/profile that resolves only when released. */
function deferredProfile() {
  let release;
  const gate = new Promise((res) => { release = res; });
  vi.spyOn(api, 'apiGet').mockImplementation((endpoint) => {
    if (endpoint === '/api/profile') return gate.then(() => SERVER_PROFILE);
    if (endpoint === '/api/resumes') return Promise.resolve({ resumes: [] });
    return Promise.resolve({});
  });
  return () => release();
}

describe('Settings profile hydration vs the user typing', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(useAuthModule, 'useAuth').mockReturnValue({
      user: { id: 'u1', email: 'a@b.com' }, loading: false,
    });
  });

  it('keeps a name typed before the fetch resolves', async () => {
    const release = deferredProfile();
    render(<Settings />);

    const nameInput = await screen.findByPlaceholderText('Utkarsh Singh');
    fireEvent.change(nameInput, { target: { value: 'Renamed User' } });
    expect(nameInput).toHaveValue('Renamed User');

    // The response lands only now — the exact ordering the race needs.
    release();
    await waitFor(() => expect(api.apiGet).toHaveBeenCalledWith('/api/profile'));

    await waitFor(() => expect(nameInput).toHaveValue('Renamed User'));
  });

  it('still fills fields the user has not touched', async () => {
    // The fix must not become "ignore the server". Blanks are hydrated.
    const release = deferredProfile();
    render(<Settings />);

    const nameInput = await screen.findByPlaceholderText('Utkarsh Singh');
    fireEvent.change(nameInput, { target: { value: 'Renamed User' } });
    release();

    await waitFor(() => {
      expect(screen.getByDisplayValue('Dublin, Ireland')).toBeInTheDocument();
    });
    expect(nameInput).toHaveValue('Renamed User');
  });

  it('hydrates every field when the user types nothing', async () => {
    const release = deferredProfile();
    render(<Settings />);
    release();

    await waitFor(() => {
      expect(screen.getByDisplayValue('Server Name')).toBeInTheDocument();
    });
    expect(screen.getByDisplayValue('Dublin, Ireland')).toBeInTheDocument();
  });
});

/**
 * The same race, twice more, in sibling state. Settings has THREE hydration
 * effects and the first fix covered one:
 *
 *   /api/profile        -> profile          (fixed in #176)
 *   /api/search-config  -> prefs            (assert 60 == 70 in CI)
 *   /api/search-config  -> enabledSources   (a toggle before load is undone)
 *
 * `prefs` defaults are NOT blank (min_match_score 60, days_back 7,
 * max_jobs_per_run 15), so the "fill blanks" test that works for the profile
 * cannot tell a default from a deliberate choice of the same value. These two
 * use a dirty ref instead, which works whatever the defaults are.
 *
 * Asserted here rather than in the browser suite because the browser suite
 * CANNOT see it: the E2E tests pass with and without the guard locally, and
 * failed only on the slower runner. Verified — removing either guard leaves all
 * 6 TestSettings E2E tests green.
 */
describe('Settings preference hydration vs the user editing', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(useAuthModule, 'useAuth').mockReturnValue({
      user: { id: 'u1', email: 'a@b.com' }, loading: false,
    });
  });

  // 85 deliberately differs from the component's default of 60, so "hydrated"
  // and "left at the default" are distinguishable. Asserting the server value
  // 60 would pass either way.
  function deferredConfig() {
    let release;
    const gate = new Promise((res) => { release = res; });
    vi.spyOn(api, 'apiGet').mockImplementation((endpoint) => {
      if (endpoint === '/api/search-config') {
        return gate.then(() => ({
          queries: ['sre'], locations: ['Dublin'], experience_levels: ['mid'],
          days_back: 14, max_jobs_per_run: 25, min_match_score: 85,
          enabled_sources: ['linkedin'],
        }));
      }
      return Promise.resolve(endpoint === '/api/resumes' ? { resumes: [] } : {});
    });
    return () => release();
  }

  const scoreSlider = () => document.querySelector('input[type="range"]');

  it('keeps a min-match-score changed before the fetch resolves', async () => {
    const release = deferredConfig();
    render(<Settings />);

    await waitFor(() => expect(scoreSlider()).toBeTruthy());
    fireEvent.change(scoreSlider(), { target: { value: '70' } });
    expect(screen.getByText('70')).toBeInTheDocument();

    release();
    await waitFor(() => expect(api.apiGet).toHaveBeenCalledWith('/api/search-config'));

    // 85 is what the server sent; it must not win over the user's 70.
    await waitFor(() => expect(scoreSlider()).toHaveValue('70'));
    expect(screen.queryByText('85')).not.toBeInTheDocument();
  });

  it('still hydrates preferences the user has not touched', async () => {
    // The guard must not degrade into "never load the saved config".
    const release = deferredConfig();
    render(<Settings />);
    await waitFor(() => expect(scoreSlider()).toBeTruthy());
    release();

    await waitFor(() => expect(scoreSlider()).toHaveValue('85'));
  });
});
