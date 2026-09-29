/**
 * The highest-value "AI is visible" surface, and it needs no new AI work.
 *
 * key_matches, gaps and requirement_map are computed on every scoring run and
 * stored on the jobs row. They have never been displayed anywhere in the app.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import CoveragePanel from '../CoveragePanel';

describe('CoveragePanel', () => {
  it('shows matched and missing requirements distinctly', () => {
    render(
      <CoveragePanel
        keyMatches={['Kubernetes', 'Terraform']}
        gaps={['Go']}
        requirementMap={[
          { requirement: 'operate multi-tenant Kubernetes', evidence: 'ran a 40-node cluster', severity: 'met' },
          { requirement: 'running production incident response', evidence: null, severity: 'blocker_gap' },
        ]}
      />,
    );
    expect(screen.getByText('operate multi-tenant Kubernetes')).toBeInTheDocument();
    expect(screen.getByText(/ran a 40-node cluster/)).toBeInTheDocument();

    const blocker = screen.getByTestId('req-1');
    expect(blocker).toHaveTextContent('running production incident response');
    expect(blocker).toHaveAttribute('data-severity', 'blocker_gap');
  });

  it('counts coverage so the user sees the ratio at a glance', () => {
    render(
      <CoveragePanel
        keyMatches={[]}
        gaps={[]}
        requirementMap={[
          { requirement: 'a', evidence: 'x', severity: 'met' },
          { requirement: 'b', evidence: 'y', severity: 'met' },
          { requirement: 'c', evidence: null, severity: 'blocker_gap' },
        ]}
      />,
    );
    expect(screen.getByTestId('coverage-count')).toHaveTextContent('2/3');
  });

  it('falls back to key_matches and gaps when requirement_map is empty', () => {
    render(<CoveragePanel keyMatches={['Python']} gaps={['Rust']} requirementMap={[]} />);
    expect(screen.getByText('Python')).toBeInTheDocument();
    expect(screen.getByText('Rust')).toBeInTheDocument();
  });

  it('renders nothing rather than an empty shell when there is no data', () => {
    const { container } = render(<CoveragePanel keyMatches={[]} gaps={[]} requirementMap={[]} />);
    expect(container).toBeEmptyDOMElement();
  });
});
