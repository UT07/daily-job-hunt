/**
 * The job editor must not offer an upload that replaces the BASE résumé.
 *
 * "Upload PDF" here posted to POST /api/resumes/upload, whose `resume_key`
 * defaults to "default": it upserted the user's base résumé (the document
 * every later tailoring starts from), overwrote users.name / phone / location
 * / candidate_context from the parsed file, returned no pdf_url (so this
 * preview never changed), and accepted .tex despite the label. No endpoint
 * replaces a job's tailored PDF, so the control is removed rather than
 * repointed. Base-résumé upload remains in Settings and Onboarding.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const apiGet = vi.fn();
const apiCall = vi.fn();
const apiUpload = vi.fn();
vi.mock('../../api', () => ({
  apiGet: (...a) => apiGet(...a),
  apiCall: (...a) => apiCall(...a),
  apiUpload: (...a) => apiUpload(...a),
}));

import ResumeEditor from '../ResumeEditor';

beforeEach(() => {
  vi.clearAllMocks();
  apiGet.mockResolvedValue({ sections: { summary: 'Original summary.' }, jd_analysis: null });
});

describe('ResumeEditor', () => {
  it('offers no upload control that would overwrite the base résumé', async () => {
    const { container } = render(
      <ResumeEditor job={{ job_id: 'j1', resume_s3_url: 'https://s3/a.pdf' }} />,
    );
    await waitFor(() => expect(screen.getByText('Save & Compile')).toBeInTheDocument());

    expect(screen.queryByText(/Upload PDF/)).not.toBeInTheDocument();
    expect(container.querySelector('input[type="file"]')).toBeNull();
    expect(apiUpload).not.toHaveBeenCalled();
  });
});
