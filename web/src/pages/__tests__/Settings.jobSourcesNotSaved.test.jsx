/**
 * "Job sources saved." must mean the job sources were saved.
 *
 * Live run 2026-10-09: Save Sources showed "Job sources saved." while the
 * database had no enabled_sources column, so the next load came back without
 * them. PUT /api/search-config now answers 409 when it stored none of the
 * request, and 200 with `not_saved` + `warning` when it stored only some.
 * Shapes match app.py's update_search_config.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import Settings from '../Settings';
import * as api from '../../api';
import * as useAuthModule from '../../auth/useAuth';

function mockApi(putResult) {
  vi.spyOn(api, 'apiGet').mockImplementation((endpoint) => {
    if (endpoint === '/api/profile') return Promise.resolve({ id: 'u1', email: 'a@b.com', full_name: 'X' });
    if (endpoint === '/api/search-config') return Promise.resolve({ queries: ['sre'] });
    if (endpoint === '/api/resumes') return Promise.resolve({ resumes: [] });
    return Promise.resolve({});
  });
  vi.spyOn(api, 'apiPut').mockImplementation(() => putResult());
}

async function clickSaveSources() {
  render(<Settings />);
  const button = await screen.findByRole('button', { name: /Save Sources/ });
  await waitFor(() => expect(button).not.toBeDisabled());
  fireEvent.click(button);
}

describe('Settings Job Sources: the save status reports what was stored', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(useAuthModule, 'useAuth').mockReturnValue({
      user: { id: 'u1', email: 'a@b.com' }, loading: false,
    });
  });

  it('shows the server error when nothing was saved (409)', async () => {
    mockApi(() => Promise.reject(new Error(
      'Not saved: enabled_sources. The database has no column for it yet (a migration is pending).')));
    await clickSaveSources();
    expect(await screen.findByText(/Save failed: Not saved: enabled_sources/)).toBeInTheDocument();
    expect(screen.queryByText('Job sources saved.')).not.toBeInTheDocument();
  });

  it('a 200 that names enabled_sources as not saved is an error, not success', async () => {
    mockApi(() => Promise.resolve({
      user_id: 'u1',
      not_saved: ['enabled_sources'],
      warning: 'Saved, except enabled_sources: the database has no column for it yet.',
    }));
    await clickSaveSources();
    expect(await screen.findByText(/except enabled_sources/)).toBeInTheDocument();
    expect(screen.queryByText('Job sources saved.')).not.toBeInTheDocument();
  });

  it('a clean save still says saved', async () => {
    mockApi(() => Promise.resolve({ user_id: 'u1', enabled_sources: ['linkedin'] }));
    await clickSaveSources();
    expect(await screen.findByText('Job sources saved.')).toBeInTheDocument();
  });
});
