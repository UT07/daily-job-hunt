/**
 * A visibly stale PDF is fine. An invisibly stale one is not.
 *
 * Compiles take ~15s, so the pane is out of date for most of the time the user
 * spends editing. The requirement is not freshness, it is honesty about
 * freshness.
 *
 * The iframe also depends on a backend detail: presigned URLs must be served
 * Content-Disposition: inline. An `attachment` disposition makes the browser
 * download the file instead of rendering it, which blanked the preview once
 * already — and the test covering it asserted the filename rather than the
 * disposition, so it passed while the feature was broken.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import PdfPane from '../PdfPane';

describe('PdfPane', () => {
  it('renders the PDF when one exists', () => {
    render(<PdfPane pdfUrl="https://s3/x.pdf" compiling={false} pendingChanges={0} error={null} onRecompile={() => {}} />);
    expect(screen.getByTitle(/resume preview/i)).toHaveAttribute('src', 'https://s3/x.pdf');
  });

  it('says how many changes are not yet compiled', () => {
    render(<PdfPane pdfUrl="https://s3/x.pdf" compiling={false} pendingChanges={2} error={null} onRecompile={() => {}} />);
    expect(screen.getByRole('status')).toHaveTextContent(/2 changes not yet compiled/i);
  });

  it('stays silent when the PDF is current', () => {
    render(<PdfPane pdfUrl="https://s3/x.pdf" compiling={false} pendingChanges={0} error={null} onRecompile={() => {}} />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('shows a compile error alongside the last good PDF', () => {
    render(<PdfPane pdfUrl="https://s3/x.pdf" compiling={false} pendingChanges={1} error="tectonic exploded" onRecompile={() => {}} />);
    expect(screen.getByRole('alert')).toHaveTextContent(/tectonic exploded/);
    expect(screen.getByTitle(/resume preview/i)).toBeInTheDocument();
  });

  it('offers a manual recompile', () => {
    const onRecompile = vi.fn();
    render(<PdfPane pdfUrl={null} compiling={false} pendingChanges={1} error={null} onRecompile={onRecompile} />);
    screen.getByRole('button', { name: /recompile/i }).click();
    expect(onRecompile).toHaveBeenCalledTimes(1);
  });

  it('disables recompile while one is already running', () => {
    render(<PdfPane pdfUrl={null} compiling pendingChanges={1} error={null} onRecompile={() => {}} />);
    expect(screen.getByRole('button', { name: /compiling/i })).toBeDisabled();
  });

  it('uses the singular for a single pending change', () => {
    render(<PdfPane pdfUrl="https://s3/x.pdf" compiling={false} pendingChanges={1} error={null} onRecompile={() => {}} />);
    expect(screen.getByRole('status')).toHaveTextContent(/1 change not yet compiled/i);
  });
});
