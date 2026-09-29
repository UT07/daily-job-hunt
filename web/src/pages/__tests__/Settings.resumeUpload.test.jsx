/**
 * An upload that cannot be tailored from must not report plain success.
 *
 * The backend already says so. /api/resumes/upload returns:
 *
 *     tailorable: false
 *     tailoring_warning: "Saved and used to fill your profile, but <why>.
 *                         Tailoring will keep using your most recent LaTeX resume."
 *
 * added specifically "so the UI can show it, rather than letting the user find
 * out from 'Regenerate failed: Pipeline failed'". Neither field was referenced
 * anywhere in web/src. Settings discarded the response entirely and rendered
 * "Resume uploaded and parsed successfully." unconditionally.
 *
 * Measured 2026-09-29: a master uploaded on 2026-09-28 was stored as plain
 * text, the pipeline silently fell back to a 2026-04-05 template, and every
 * resume for a full day was built from the wrong base. The UI said success.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ResumeSection } from '../Settings';
import * as api from '../../api';

function uploadAFile() {
  const input = document.querySelector('input[type="file"]');
  const file = new File(['%PDF-1.4'], 'master.pdf', { type: 'application/pdf' });
  fireEvent.change(input, { target: { files: [file] } });
  return screen.getByRole('button', { name: /upload/i });
}

describe('resume upload status', () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, 'apiGet').mockResolvedValue({ resumes: [] });
  });

  it('warns instead of claiming success when the upload is not tailorable', async () => {
    vi.spyOn(api, 'apiUpload').mockResolvedValue({
      resume_id: 'r1',
      tailorable: false,
      tailoring_warning:
        'Saved and used to fill your profile, but it has no \\documentclass. ' +
        'Tailoring will keep using your most recent LaTeX resume.',
    });
    render(<ResumeSection />);
    fireEvent.click(uploadAFile());

    await waitFor(() => expect(screen.getByText(/keep using your most recent LaTeX resume/i)).toBeInTheDocument());
    expect(screen.queryByText(/uploaded and parsed successfully/i)).not.toBeInTheDocument();
  });

  it('still reports success when the upload IS tailorable', async () => {
    vi.spyOn(api, 'apiUpload').mockResolvedValue({
      resume_id: 'r1', tailorable: true, tailoring_warning: '',
    });
    render(<ResumeSection />);
    fireEvent.click(uploadAFile());

    await waitFor(() => expect(screen.getByText(/uploaded and parsed successfully/i)).toBeInTheDocument());
  });

  it('treats a response with no tailorable field as success', async () => {
    // Backwards compatible: an older API build must not start warning.
    vi.spyOn(api, 'apiUpload').mockResolvedValue({ resume_id: 'r1' });
    render(<ResumeSection />);
    fireEvent.click(uploadAFile());

    await waitFor(() => expect(screen.getByText(/uploaded and parsed successfully/i)).toBeInTheDocument());
  });

  it('still surfaces a hard upload failure', async () => {
    vi.spyOn(api, 'apiUpload').mockRejectedValue(new Error('413 Payload Too Large'));
    render(<ResumeSection />);
    fireEvent.click(uploadAFile());

    await waitFor(() => expect(screen.getByText(/413 Payload Too Large/)).toBeInTheDocument());
  });
});
