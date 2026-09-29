/**
 * Which files the resume uploader accepts.
 *
 * Gate on the EXTENSION, not the MIME type. Settings and Onboarding both
 * required `file.type === 'application/pdf'` while their inputs advertised
 * `accept=".tex,.latex,.pdf"`, and browsers report a .tex file as
 * `text/x-tex`, `application/x-tex`, `text/plain` or — most often — the empty
 * string. So picking a .tex silently did nothing in Settings, because the file
 * was never stored and Upload had nothing to send.
 *
 * That mattered more than a rejected file usually does, because .tex is the
 * format the backend handles BEST:
 *
 *     if is_latex_document(text):
 *         tex_content, converted_from_pdf = text, False
 *
 * The source is stored verbatim. The PDF path instead runs AI extraction and
 * re-renders the document, which on 2026-09-29 turned a 16,953-character
 * resume into 8,121 characters of LaTeX, dropped whole roles, and produced a
 * page of whitespace in the output. The one format that needs no conversion
 * was the one the UI refused.
 */

const ACCEPTED_EXTENSIONS = ['.pdf', '.tex', '.latex'];

/** True when this file is a resume we can accept. Extension decides. */
export function isAcceptedResumeFile(file) {
  const name = (file && file.name) || '';
  const lower = name.toLowerCase();
  // endsWith, so "payload.tex.exe" is not accepted on the strength of a
  // substring.
  return ACCEPTED_EXTENSIONS.some((ext) => lower.endsWith(ext));
}

/** What to tell someone whose file we cannot take. */
export function resumeRejectionMessage() {
  return (
    'Please upload a .tex, .latex or .pdf file. '
    + 'A .tex is used exactly as-is; a PDF has to be converted, which can lose '
    + 'formatting and whole sections.'
  );
}
