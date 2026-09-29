/**
 * The only lossless resume format could not be selected.
 *
 * Settings and Onboarding both gated on `file.type === 'application/pdf'`,
 * while their file inputs advertised `accept=".tex,.latex,.pdf"`. A .tex file
 * reports MIME `text/x-tex`, `application/x-tex`, or very often the empty
 * string — browsers do not reliably type it. So picking a .tex silently did
 * nothing in Settings (the file was never stored, so Upload had nothing to
 * send) and showed "Please upload a PDF file." in Onboarding.
 *
 * The backend has always handled .tex, and handles it BETTER than PDF:
 *
 *     if is_latex_document(text):
 *         tex_content, converted_from_pdf = text, False
 *
 * That path stores the source verbatim. The PDF path runs AI extraction and
 * re-renders, which on 2026-09-29 turned a 16,953-character resume into 8,121
 * characters of LaTeX and dropped whole roles — the UTA IT Support and Seattle
 * Kraken entries vanished, and the rendered output had a page of whitespace.
 *
 * So the format that needs no conversion was the one the UI refused.
 */
import { describe, expect, it } from 'vitest';
import { isAcceptedResumeFile, resumeRejectionMessage } from '../resumeUploadFile';

const f = (name, type) => ({ name, type });

describe('isAcceptedResumeFile', () => {
  it('accepts a PDF', () => {
    expect(isAcceptedResumeFile(f('cv.pdf', 'application/pdf'))).toBe(true);
  });

  it('accepts a .tex whatever the browser calls it', () => {
    // These are all real values browsers report for the same file.
    for (const type of ['text/x-tex', 'application/x-tex', 'text/plain', '']) {
      expect(isAcceptedResumeFile(f('resume.tex', type))).toBe(true);
    }
  });

  it('accepts .latex too, since the input advertises it', () => {
    expect(isAcceptedResumeFile(f('resume.latex', ''))).toBe(true);
  });

  it('is case-insensitive about the extension', () => {
    expect(isAcceptedResumeFile(f('RESUME.TEX', ''))).toBe(true);
    expect(isAcceptedResumeFile(f('CV.PDF', ''))).toBe(true);
  });

  it('still rejects a Word document', () => {
    expect(isAcceptedResumeFile(f('cv.docx',
      'application/vnd.openxmlformats-officedocument.wordprocessingml.document'))).toBe(false);
  });

  it('rejects nothing gracefully', () => {
    expect(isAcceptedResumeFile(null)).toBe(false);
    expect(isAcceptedResumeFile(undefined)).toBe(false);
  });

  it('does not accept a .tex.exe', () => {
    expect(isAcceptedResumeFile(f('payload.tex.exe', ''))).toBe(false);
  });
});

describe('resumeRejectionMessage', () => {
  it('names both accepted formats, not just PDF', () => {
    const msg = resumeRejectionMessage(f('cv.docx', ''));
    expect(msg).toMatch(/\.tex/i);
    expect(msg).toMatch(/pdf/i);
  });

  it('says why .tex is preferable', () => {
    // The user should know the lossless option exists — that is the whole
    // point of unblocking it.
    expect(resumeRejectionMessage(f('cv.docx', ''))).toMatch(/without|lossless|exactly|conversion/i);
  });
});
