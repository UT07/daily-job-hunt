/**
 * The suggestions panel: what Apply actually does, and when it refuses.
 *
 * Spec §5.3. Apply edits one section — it does not regenerate the resume —
 * so the change is small enough to read and undo is a single edit. The panel's
 * whole job is to make that true in the face of the user editing while
 * suggestions sit on screen:
 *
 *   - a suggestion whose source text has changed greys out and says so,
 *     rather than applying to content it was not written for;
 *   - a suggestion whose source text merely moved still applies, at the new
 *     place;
 *   - Undo is a local inversion, never a second model call, and it refuses
 *     once the applied text has itself been edited.
 *
 * The panel is rendered inside a harness that owns `sections`, because that is
 * how the Studio holds it: the panel proposes, the page keeps the document.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import { useState } from 'react';
import { describe, expect, it, vi } from 'vitest';
import SuggestionsPanel from '../SuggestionsPanel';

const BULLET = 'Developed React frontends for internal ops dashboards';
const BETTER = 'Shipped React ops dashboards used by 400 staff daily';

function baseSections() {
  return {
    summary: 'Platform engineer.',
    skills: [{ category: 'Cloud', items: 'AWS, GCP' }],
    experience: [{
      company: 'Clover IT Services',
      title: 'Engineer',
      bullets: ['Maintained CI pipelines', BULLET],
    }],
  };
}

const SUGGESTION = {
  id: 's4',
  path: ['experience', 0, 'bullets', 1],
  label: 'Experience · Clover IT Services',
  anchor_text: BULLET,
  replacement: BETTER,
  why: 'no quantified impact, and the JD asks for scale',
};

/** Holds `sections` the way ResumeStudio does, and reports every change. */
function Harness({ initial = baseSections(), onChange = () => {}, ...props }) {
  const [sections, setSections] = useState(initial);
  return (
    <SuggestionsPanel
      sections={sections}
      onApplySections={(next) => { setSections(next); onChange(next); }}
      {...props}
    />
  );
}

function renderPanel(props = {}) {
  return render(<Harness {...props} />);
}

describe('before anything has been generated', () => {
  it('offers to analyse rather than showing an empty box', () => {
    const onRequest = vi.fn();
    renderPanel({ suggestions: null, onRequest });
    fireEvent.click(screen.getByRole('button', { name: /suggest improvements/i }));
    expect(onRequest).toHaveBeenCalledTimes(1);
  });

  it('says it is working and cannot be asked twice at once', () => {
    const onRequest = vi.fn();
    renderPanel({ suggestions: null, loading: true, onRequest });
    const button = screen.getByRole('button', { name: /analysing/i });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(onRequest).not.toHaveBeenCalled();
  });

  it('shows a failure instead of pretending there was nothing to say', () => {
    renderPanel({ suggestions: null, error: 'All AI providers failed' });
    expect(screen.getByRole('alert')).toHaveTextContent('All AI providers failed');
  });

  it('distinguishes "analysed, nothing to say" from "not analysed yet"', () => {
    renderPanel({ suggestions: [] });
    expect(screen.getByTestId('suggestions')).toHaveTextContent(/nothing to suggest/i);
  });
});

describe('a live suggestion', () => {
  it('shows where it applies, what is there now, why, and what it proposes', () => {
    renderPanel({ suggestions: [SUGGESTION] });
    const row = screen.getByTestId('suggestion-s4');
    expect(row).toHaveTextContent('Clover IT Services');
    expect(row).toHaveTextContent(BULLET);
    expect(row).toHaveTextContent('no quantified impact');
    expect(row).toHaveTextContent(BETTER);
    expect(row).toHaveAttribute('data-status', 'exact');
  });

  it('Apply changes exactly that one line', () => {
    const onChange = vi.fn();
    renderPanel({ suggestions: [SUGGESTION], onChange });
    fireEvent.click(screen.getByRole('button', { name: /^apply$/i }));

    const next = onChange.mock.calls[0][0];
    expect(next.experience[0].bullets[1]).toBe(BETTER);
    expect(next.experience[0].bullets[0]).toBe('Maintained CI pipelines');
    expect(next.summary).toBe('Platform engineer.');
  });

  it('applies where the text is now, not where it was generated', () => {
    const moved = baseSections();
    moved.experience[0].bullets = ['A new first bullet', ...moved.experience[0].bullets];
    const onChange = vi.fn();
    renderPanel({ initial: moved, suggestions: [SUGGESTION], onChange });

    expect(screen.getByTestId('suggestion-s4')).toHaveAttribute('data-status', 'moved');
    fireEvent.click(screen.getByRole('button', { name: /^apply$/i }));
    const next = onChange.mock.calls[0][0];
    expect(next.experience[0].bullets[2]).toBe(BETTER);
    expect(next.experience[0].bullets[0]).toBe('A new first bullet');
  });

  it('Dismiss takes it off the list without touching the document', () => {
    const onChange = vi.fn();
    renderPanel({ suggestions: [SUGGESTION], onChange });
    fireEvent.click(screen.getByRole('button', { name: /dismiss/i }));
    expect(screen.queryByTestId('suggestion-s4')).not.toBeInTheDocument();
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe('staleness', () => {
  const edited = () => {
    const s = baseSections();
    s.experience[0].bullets[1] = 'The user already rewrote this line themselves';
    return s;
  };

  it('greys the suggestion and explains why', () => {
    renderPanel({ initial: edited(), suggestions: [SUGGESTION] });
    const row = screen.getByTestId('suggestion-s4');
    expect(row).toHaveAttribute('data-status', 'stale');
    expect(row).toHaveTextContent(/edited this since/i);
  });

  it('will not apply', () => {
    const onChange = vi.fn();
    renderPanel({ initial: edited(), suggestions: [SUGGESTION], onChange });
    const apply = screen.getByRole('button', { name: /^apply$/i });
    expect(apply).toBeDisabled();
    fireEvent.click(apply);
    expect(onChange).not.toHaveBeenCalled();
  });

  it('offers a re-analyse, because the advice is not wrong so much as out of date', () => {
    const onRequest = vi.fn();
    renderPanel({ initial: edited(), suggestions: [SUGGESTION], onRequest });
    fireEvent.click(screen.getByRole('button', { name: /re-?analyse/i }));
    expect(onRequest).toHaveBeenCalled();
  });

  it('recomputes each row from the sections it is handed, so one apply does not disturb another', () => {
    // The panel recomputes staleness from the sections it is handed. Applying
    // one suggestion changes the document, which is exactly the event that can
    // stale another one — so this is checked through a real apply.
    const second = {
      ...SUGGESTION, id: 's3', path: ['experience', 0, 'bullets', 0],
      anchor_text: 'Maintained CI pipelines', replacement: 'Kept CI green at 98% pass rate',
    };
    renderPanel({ suggestions: [SUGGESTION, second] });
    expect(screen.getByTestId('suggestion-s3')).toHaveAttribute('data-status', 'exact');
    fireEvent.click(screen.getAllByRole('button', { name: /^apply$/i })[1]);
    // s3's own text was replaced by its own apply, so it is now "applied",
    // while s4 — untouched — is still live.
    expect(screen.getByTestId('suggestion-s4')).toHaveAttribute('data-status', 'exact');
  });
});

describe('undo', () => {
  it('puts the original text back without asking the model again', () => {
    const onChange = vi.fn();
    const onRequest = vi.fn();
    renderPanel({ suggestions: [SUGGESTION], onChange, onRequest });

    fireEvent.click(screen.getByRole('button', { name: /^apply$/i }));
    expect(screen.getByTestId('suggestion-s4')).toHaveAttribute('data-status', 'applied');

    fireEvent.click(screen.getByRole('button', { name: /undo/i }));
    const reverted = onChange.mock.calls[1][0];
    expect(reverted.experience[0].bullets[1]).toBe(BULLET);
    expect(onRequest).not.toHaveBeenCalled();
  });

  it('is offered only while it can be honoured', () => {
    const { rerender } = render(
      <SuggestionsPanel sections={baseSections()} suggestions={[SUGGESTION]} onApplySections={() => {}} />,
    );
    fireEvent.click(screen.getByRole('button', { name: /^apply$/i }));

    // The user then edits the line they just accepted. Reverting now would
    // throw away their words, so Undo must stand down.
    const edited = baseSections();
    edited.experience[0].bullets[1] = `${BETTER}, while on call`;
    rerender(
      <SuggestionsPanel sections={edited} suggestions={[SUGGESTION]} onApplySections={() => {}} />,
    );
    expect(screen.getByRole('button', { name: /undo/i })).toBeDisabled();
  });

  it('can be applied again after an undo', () => {
    const onChange = vi.fn();
    renderPanel({ suggestions: [SUGGESTION], onChange });
    fireEvent.click(screen.getByRole('button', { name: /^apply$/i }));
    fireEvent.click(screen.getByRole('button', { name: /undo/i }));
    fireEvent.click(screen.getByRole('button', { name: /^apply$/i }));
    expect(onChange.mock.calls[2][0].experience[0].bullets[1]).toBe(BETTER);
  });
});

describe('the panel is not an editor', () => {
  it('exposes no LaTeX and no storage keys', () => {
    const { container } = renderPanel({ suggestions: [SUGGESTION] });
    expect(container.textContent).not.toMatch(/\\[a-z]|tex_s3_key|\\begin/);
  });
});
