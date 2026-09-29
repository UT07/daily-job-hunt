/**
 * Scores display as a BAND, never a point.
 *
 * Measured 2026-09-28: the same job and resume scored 25 to 80 across 30
 * verified models, and one model at temperature=0 returned three different
 * answers to three identical consecutive calls. Temperature controls sampling;
 * it does not control mixture-of-experts routing or request batching.
 *
 * Printing "88" claims a precision the measurement does not have. A band that
 * collapses to a point when the calls agree is itself information.
 *
 * Phase 1 derives the band from the three stored perspective scores. Phase 2
 * derives it from repeat calls (num_calls=3, cache bypassed).
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import ScoreStrip from '../ScoreStrip';

describe('ScoreStrip', () => {
  it('renders a band spanning the perspectives, not a single number', () => {
    render(<ScoreStrip ats={86} hiringManager={84} techRecruiter={90} />);
    expect(screen.getByTestId('score-band')).toHaveTextContent('84–90');
  });

  it('collapses to a point when the perspectives agree', () => {
    render(<ScoreStrip ats={85} hiringManager={85} techRecruiter={85} />);
    expect(screen.getByTestId('score-band')).toHaveTextContent('85');
    expect(screen.getByTestId('score-band')).not.toHaveTextContent('–');
  });

  it('still shows each perspective individually', () => {
    render(<ScoreStrip ats={86} hiringManager={84} techRecruiter={90} />);
    expect(screen.getByTestId('score-ats')).toHaveTextContent('86');
    expect(screen.getByTestId('score-hm')).toHaveTextContent('84');
    expect(screen.getByTestId('score-tr')).toHaveTextContent('90');
  });

  it('greys itself when stale rather than showing a spinner', () => {
    render(<ScoreStrip ats={86} hiringManager={84} techRecruiter={90} stale />);
    const strip = screen.getByTestId('score-strip');
    expect(strip).toHaveAttribute('data-stale', 'true');
    // A greyed previous number is still information; a spinner is not.
    expect(screen.getByTestId('score-band')).toHaveTextContent('84–90');
  });

  it('says so plainly when the job has not been scored', () => {
    render(<ScoreStrip ats={null} hiringManager={null} techRecruiter={null} />);
    expect(screen.getByTestId('score-strip')).toHaveTextContent(/not scored/i);
  });

  it('does not invent a band from a partially scored job', () => {
    // A row with only one perspective populated must not render "86–86" as
    // though three independent judgements had agreed.
    render(<ScoreStrip ats={86} hiringManager={null} techRecruiter={null} />);
    expect(screen.getByTestId('score-band')).toHaveTextContent('86');
    expect(screen.queryByTestId('score-hm')).not.toBeInTheDocument();
  });
});

describe('a measured band beats the perspective range', () => {
  it('prefers the band from repeat calls when one is supplied', () => {
    // Phase 1's band was min/max across ATS/HM/TR — whether the three LENSES
    // disagree about the same document. This one is min/max across three
    // repeat calls — whether the MODEL disagrees with itself. Only the second
    // is the reliability claim, and they are not interchangeable.
    render(<ScoreStrip ats={86} hiringManager={84} techRecruiter={90} band={[85, 87]} calls={3} />);
    expect(screen.getByTestId('score-band')).toHaveTextContent('85–87');
  });

  it('says how many calls produced the band', () => {
    render(<ScoreStrip ats={86} hiringManager={84} techRecruiter={90} band={[85, 87]} calls={3} />);
    expect(screen.getByTestId('score-strip')).toHaveTextContent(/3 calls/i);
  });

  it('does not imply agreement when only one call was made', () => {
    // A [86, 86] band from ONE sample looks identical to three calls agreeing.
    // They are very different claims.
    render(<ScoreStrip ats={86} hiringManager={84} techRecruiter={90} band={[86, 86]} calls={1} />);
    const strip = screen.getByTestId('score-strip');
    // textContent concatenates sibling elements with no separator, so the
    // caption reads "...1 callATS 86..." — \b finds no boundary before "ATS".
    expect(strip).toHaveTextContent(/1 call/i);
    expect(strip).not.toHaveTextContent(/1 calls/i);
  });

  it('falls back to the perspective range when no band is supplied', () => {
    render(<ScoreStrip ats={86} hiringManager={84} techRecruiter={90} />);
    expect(screen.getByTestId('score-band')).toHaveTextContent('84–90');
  });
})
