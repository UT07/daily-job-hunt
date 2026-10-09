/**
 * A reused score has to say it is one.
 *
 * "if I press save and score on a job multiple times the scores are different
 * we need to fix that" — 2026-10-08. They differed because a different MODEL
 * answered each time: the AI response cache is a SQLite file in the Lambda's
 * /tmp and the rate-limit state is in-memory on the client object, so each
 * container picks the first provider IT believes is usable. /api/score now
 * returns the score already on record, which makes repeats exact.
 *
 * Saying nothing would be the other half of the same failure: an instant,
 * identical result with no way to tell a stored score from a fresh one, and no
 * way to ask for a new one (CLAUDE.md #2).
 */
import { describe, it, expect, vi } from 'vitest';
import { render, screen, fireEvent } from '@testing-library/react';
import ScoreCard from '../ScoreCard';

const SCORE = {
  ats_score: 86, hiring_manager_score: 90, tech_recruiter_score: 88,
  avg_score: 88, reasoning: 'strong platform overlap',
  matched_resume: 'sre_devops', job_id: 'job-1', saved: true,
};

describe('ScoreCard and a reused score', () => {
  it('says the score is the stored one', () => {
    render(<ScoreCard data={{ ...SCORE, reused: true }} company="Acme" />);
    expect(screen.getByText(/already scored/i)).toBeInTheDocument();
    expect(screen.getByText(/will not drift/i)).toBeInTheDocument();
  });

  it('says nothing of the sort for a fresh score', () => {
    render(<ScoreCard data={{ ...SCORE, reused: false }} company="Acme" />);
    expect(screen.queryByText(/already scored/i)).not.toBeInTheDocument();
  });

  it('treats a response without the field as fresh', () => {
    // Responses from before `reused` existed must not claim to be stored.
    const { reused: _drop, ...older } = { ...SCORE, reused: true };
    render(<ScoreCard data={older} company="Acme" />);
    expect(screen.queryByText(/already scored/i)).not.toBeInTheDocument();
  });

  it('offers a re-score and calls back', () => {
    const onRescore = vi.fn();
    render(<ScoreCard data={{ ...SCORE, reused: true }} company="Acme" onRescore={onRescore} />);
    fireEvent.click(screen.getByRole('button', { name: /score again/i }));
    expect(onRescore).toHaveBeenCalledTimes(1);
  });

  it('does not offer a re-score when there is no handler', () => {
    render(<ScoreCard data={{ ...SCORE, reused: true }} company="Acme" />);
    expect(screen.queryByRole('button', { name: /score again/i })).not.toBeInTheDocument();
  });

  it('disables the button while a re-score is running', () => {
    render(<ScoreCard data={{ ...SCORE, reused: true }} company="Acme"
                      onRescore={() => {}} rescoring />);
    expect(screen.getByRole('button', { name: /scoring/i })).toBeDisabled();
  });

  it('still shows the not-saved warning alongside a reused score', () => {
    // The two notices answer different questions and must not displace each
    // other: one is about drift, the other about whether the row exists.
    render(<ScoreCard data={{ ...SCORE, reused: true, saved: false }} company="Acme" />);
    expect(screen.getByText(/already scored/i)).toBeInTheDocument();
    expect(screen.getByText(/not saved/i)).toBeInTheDocument();
  });
});
