/**
 * ScoreCard must surface whether the job was SAVED, not just scored.
 *
 * /api/score gained `saved` and `job_id` in commit c94494d specifically so a
 * failed write could be distinguished from a success. The card read neither,
 * so a failed save rendered an ordinary green Score Card and the user went
 * looking for a job that was never written — the same silent failure the
 * backend fix removed, one layer up.
 */
import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import ScoreCard from '../ScoreCard';

const base = {
  ats_score: 92, hiring_manager_score: 84, tech_recruiter_score: 88,
  avg_score: 88, reasoning: 'Strong match.', matched_resume: 'sre_devops',
};

describe('ScoreCard save outcome', () => {
  it('warns clearly when the job was scored but NOT saved', () => {
    render(<ScoreCard data={{ ...base, saved: false, job_id: null }} company="Susquehanna" />);
    const note = screen.getByRole('status');
    expect(note).toHaveTextContent(/not saved/i);
    expect(note).toHaveTextContent(/will not appear on your dashboard/i);
  });

  it('confirms the save when it succeeded', () => {
    render(<ScoreCard data={{ ...base, saved: true, job_id: 'abc123' }} company="Susquehanna" />);
    expect(screen.getByText(/saved to your dashboard/i)).toBeInTheDocument();
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('stays silent for a response predating the field', () => {
    // `saved` undefined must not be read as a failure.
    render(<ScoreCard data={base} company="Susquehanna" />);
    expect(screen.queryByRole('status')).not.toBeInTheDocument();
    expect(screen.queryByText(/not saved/i)).not.toBeInTheDocument();
  });

  it('still shows the scores in every case', () => {
    render(<ScoreCard data={{ ...base, saved: false }} company="Susquehanna" />);
    expect(screen.getByText('Strong match.')).toBeInTheDocument();
    expect(screen.getByText(/92/)).toBeInTheDocument();
  });
});
