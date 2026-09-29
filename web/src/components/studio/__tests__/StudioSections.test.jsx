/**
 * Sections are the source of truth. The user never sees LaTeX.
 *
 * Every edit path — typing here, and in later phases applied suggestions and
 * per-section AI instructions — mutates this one structured object. LaTeX is
 * derived from it server-side and spliced into a preamble the user never sees,
 * which is what keeps escaping to a single site and stops a model ever
 * emitting markup.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import StudioSections from '../StudioSections';

const SECTIONS = {
  header: { name: 'Jane Doe', title: 'SRE', contact: 'jane@example.com' },
  summary: 'Platform engineer with Kubernetes experience.',
  skills: [{ category: 'Cloud', items: 'AWS, GCP' }],
  experience: [
    { company: 'Acme', title: 'SRE', dates: '2022-2025', bullets: ['Ran a 40-node cluster'] },
  ],
  projects: [],
  education: [],
  certifications: [],
};

describe('StudioSections', () => {
  it('edits the summary through onChange', () => {
    const onChange = vi.fn();
    render(<StudioSections sections={SECTIONS} onChange={onChange} onSectionBlur={() => {}} />);
    fireEvent.change(screen.getByLabelText(/summary/i), { target: { value: 'New summary' } });
    expect(onChange).toHaveBeenCalledWith(expect.objectContaining({ summary: 'New summary' }));
  });

  it('edits an experience bullet without disturbing its siblings', () => {
    const onChange = vi.fn();
    render(<StudioSections sections={SECTIONS} onChange={onChange} onSectionBlur={() => {}} />);
    fireEvent.change(screen.getByLabelText('Acme bullet 1'), { target: { value: 'Ran a 90-node cluster' } });
    const next = onChange.mock.calls[0][0];
    expect(next.experience[0].bullets[0]).toBe('Ran a 90-node cluster');
    expect(next.experience[0].company).toBe('Acme');
    expect(next.summary).toBe(SECTIONS.summary);
  });

  it('fires onSectionBlur so the caller can compile one unit of work', () => {
    const onSectionBlur = vi.fn();
    render(<StudioSections sections={SECTIONS} onChange={() => {}} onSectionBlur={onSectionBlur} />);
    fireEvent.blur(screen.getByLabelText(/summary/i));
    expect(onSectionBlur).toHaveBeenCalledTimes(1);
  });

  it('never exposes LaTeX to the user', () => {
    const { container } = render(
      <StudioSections sections={SECTIONS} onChange={() => {}} onSectionBlur={() => {}} />,
    );
    expect(container.textContent).not.toMatch(/\\documentclass|\\begin\{|\\section/);
  });

  it('renders an empty-but-usable editor when sections are absent', () => {
    render(<StudioSections sections={null} onChange={() => {}} onSectionBlur={() => {}} />);
    expect(screen.getByRole('status')).toHaveTextContent(/nothing to edit/i);
  });

  it('survives a section the parser returned as absent', () => {
    // parse_resume_sections returns [] for a section it could not find, and a
    // resume with no projects is ordinary. Rendering must not assume presence.
    const sparse = { header: {}, summary: 'x' };
    expect(() =>
      render(<StudioSections sections={sparse} onChange={() => {}} onSectionBlur={() => {}} />),
    ).not.toThrow();
  });
});
