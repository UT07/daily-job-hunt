/**
 * Edits in the Studio editor did not survive, and the preview showed the old PDF.
 *
 * Two independent defects, both in state plumbing rather than in the save path.
 * The save path itself is sound: apiCall auto-polls the 202 task and returns
 * the finished result carrying `pdf_url`, so "Saved and compiled successfully"
 * is an accurate message.
 *
 * 1. The loader effect depends on `[jobId, job.resume_s3_url]`, and EVERY save
 *    changes `job.resume_s3_url` -- the backend mints a fresh presigned URL for
 *    the same S3 key, so the value differs even when the document does not. The
 *    effect then refetched and called setSections(server copy), discarding
 *    whatever had been typed since. Same class as the three hydration races in
 *    Settings.jsx (#171, #176).
 *
 * 2. `pdfUrl` seeds from the prop once, via useState(job.resume_s3_url), and
 *    never re-synced. A regenerate or re-tailor elsewhere on the page updated
 *    the prop and the preview went on rendering the previous document.
 */
import { render, screen, waitFor, act, fireEvent } from '@testing-library/react';
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

// `summary` only: SectionEditor treats experience/projects as LIST sections
// whose value must be an array of entry objects, and skills has its own
// renderer. A string there renders nothing editable -- which is how the
// first version of this test came to assert against an empty box.
const SECTIONS = { summary: 'Original summary.' };

beforeEach(() => {
  vi.clearAllMocks();
  apiGet.mockResolvedValue({ sections: { ...SECTIONS }, jd_analysis: null });
  apiCall.mockResolvedValue({ pdf_url: 'https://s3/new.pdf' });
});

// ResumeEditor takes `job` and derives jobId = job.job_id internally; it is
// NOT a prop. The first version of this helper hard-coded job_id:'j1', so the
// "switch jobs" case never switched jobs and the component was right to keep
// the unsaved edits. Five readings of the component chased that fixture.
function job(url, id = 'j1') {
  return { job_id: id, resume_s3_url: url };
}

async function typeInto(text) {
  const boxes = await screen.findAllByRole('textbox');
  fireEvent.change(boxes[0], { target: { value: text } });
  return boxes[0];
}

describe('ResumeEditor state sync', () => {
  it('keeps unsaved edits when the job prop changes underneath', async () => {
    const { rerender } = render(<ResumeEditor job={job('https://s3/a.pdf')} jobId="j1" />);
    await screen.findAllByRole('textbox');

    const box = await typeInto('My edited summary.');
    expect(box).toHaveValue('My edited summary.');


    // A save elsewhere mints a new presigned URL for the SAME document.
    apiGet.mockResolvedValue({ sections: { ...SECTIONS }, jd_analysis: null });
    await act(async () => {
      rerender(<ResumeEditor job={job('https://s3/a.pdf?sig=refreshed')} jobId="j1" />);
    });

    // Queried fresh, not through the handle captured before the rerender: a
    // controlled textarea keeps its DOM node across re-renders, so the stale
    // handle reported the edited value even when the loader had overwritten
    // state. Mutation testing caught that -- removing the dirty guard entirely
    // left this test green.
    expect(await screen.findByDisplayValue('My edited summary.')).toBeInTheDocument();
    expect(screen.queryByDisplayValue('Original summary.')).toBeNull();
  });

  it('refreshes the preview when the job prop gets a new URL', async () => {
    const { container, rerender } = render(
      <ResumeEditor job={job('https://s3/old.pdf')} jobId="j1" />);
    await screen.findAllByRole('textbox');
    expect(container.querySelector('iframe')).toHaveAttribute('src', 'https://s3/old.pdf');

    await act(async () => {
      rerender(<ResumeEditor job={job('https://s3/regenerated.pdf')} jobId="j1" />);
    });

    await waitFor(() => {
      expect(container.querySelector('iframe')).toHaveAttribute('src', 'https://s3/regenerated.pdf');
    });
  });

  it('takes the server copy again once the edits have been saved', async () => {
    const { rerender } = render(<ResumeEditor job={job('https://s3/a.pdf')} jobId="j1" />);
    await screen.findAllByRole('textbox');
    await typeInto('Edited.');

    fireEvent.click(screen.getByRole('button', { name: /save & compile/i }));
    await waitFor(() => expect(apiCall).toHaveBeenCalled());

    // Server now holds a DIFFERENT body; nothing is unsaved, so it must win.
    apiGet.mockResolvedValue({
      sections: { ...SECTIONS, summary: 'Server rewrote this.' }, jd_analysis: null,
    });
    await act(async () => {
      rerender(<ResumeEditor job={job('https://s3/a.pdf?sig=2')} jobId="j1" />);
    });

    await waitFor(async () => {
      const boxes = await screen.findAllByRole('textbox');
      expect(boxes[0]).toHaveValue('Server rewrote this.');
    });
  });

  it('does not carry one job’s unsaved edits into another', async () => {
    const { rerender } = render(<ResumeEditor job={job('https://s3/a.pdf')} jobId="j1" />);
    await screen.findAllByRole('textbox');
    await typeInto('Draft for job one.');

    apiGet.mockResolvedValue({
      sections: { summary: 'Job two summary.' },
      jd_analysis: null,
    });
    await act(async () => {
      rerender(<ResumeEditor job={job('https://s3/b.pdf', 'j2')} jobId="j2" />);
    });

    // findByDisplayValue waits for the value itself. Grabbing boxes[0] inside a
    // waitFor raced the loading spinner: during the refetch the component early
    // returns with no textbox at all, so the handle captured before the switch
    // was detached by the time it was asserted on.
    expect(await screen.findByDisplayValue('Job two summary.')).toBeInTheDocument();
  });
});
