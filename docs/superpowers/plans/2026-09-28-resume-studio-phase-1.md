# Resume Studio Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship a two-pane Resume Studio at `/jobs/:jobId/studio` — structured section editor on the left with the JD coverage and scores above it, live compiled PDF on the right — using only data the system already produces.

**Architecture:** Sections are the source of truth; LaTeX is derived server-side and never shown. The left column holds an always-visible AI panel (coverage + banded scores) above a section editor. Editing a section and blurring it derives a content hash and enqueues one compile keyed by that hash; a result whose hash is no longer current is discarded rather than displayed, so the PDF can never silently show an older document than the editor.

**Tech Stack:** React 18, React Router 6, Vite, Tailwind, Vitest + @testing-library/react. Backend endpoints already exist and are **not modified by this plan**.

**Spec:** `docs/superpowers/specs/2026-09-28-resume-studio-design.md` (committed `8f6ed5c`)

## Global Constraints

- **`web/src/pages/JobWorkspace.jsx` must not be modified.** 1,401 lines at 0% coverage. The Studio is a new route and new components. The entry link goes in `JobTable.jsx`.
- **No LaTeX reaches the user.** No Studio component may render, accept, or hold `tex_content`. Asserted structurally in Task 7.
- **No backend changes.** Every endpoint this plan uses exists today. If a task appears to need a new endpoint, stop and escalate rather than adding one.
- **Scores display as a band, not a point** (`86–90`, not `88`). Phase 1 derives the band from the three stored perspective scores; Phase 2 derives it from repeat calls.
- Run frontend tests with `cd web && npm test`. Lint with `cd web && npm run lint`.
- Tailwind classes follow the existing Neo-Brutalist convention in `web/src/components/` — `border-2`, `border-stone-200`, `bg-stone-50`, `font-mono` for numbers.

### Data shapes (verified against the running backend, 2026-09-28)

`GET /api/dashboard/jobs/{job_id}/sections` → 200:
```js
{
  sections: {
    header:         { name: "", title: "", contact: "" },
    summary:        "plain text string",
    skills:         [{ category: "", items: "" }],
    experience:     [{ company: "", title: "", dates: "", bullets: ["", ""] }],
    projects:       [{ company: "", title: "", dates: "", bullets: [] }],
    education:      [{ ... }],
    certifications: [{ ... }],
  },
  jd_analysis: { keywords_matched: [], keywords_missing: [], coverage_score: 0 },
  tex_s3_key: "users/{uid}/resumes/{job_id}_tailored.tex",
}
```
404 when no tailored `.tex` exists yet (`"No tailored .tex found for job {job_id}. Run tailoring first."`).

`POST /api/dashboard/jobs/{job_id}/sections` body `{ sections }` → **202** `{ task_id, poll_url }`.
Poll `GET /api/tasks/{task_id}` via the existing `pollPipeline(pollUrl, {...})` helper in `web/src/api.js`.

`GET /api/dashboard/jobs/{job_id}` → the whole `jobs` row, including `ats_score`,
`hiring_manager_score`, `tech_recruiter_score`, `match_score`, `key_matches`,
`gaps`, `requirement_map`, `resume_s3_url`, `title`, `company`.

---

## File Structure

| File | Responsibility |
|---|---|
| `web/src/pages/ResumeStudio.jsx` | Route component. Loads job + sections, owns the sections state, composes the two panes. |
| `web/src/components/studio/useHashedCompile.js` | The compile queue: hashing, enqueue-on-change, stale-result discard. Pure logic, no JSX. |
| `web/src/components/studio/CoveragePanel.jsx` | Renders `key_matches` / `gaps` / `requirement_map` as matched-vs-missing. |
| `web/src/components/studio/ScoreStrip.jsx` | The three perspective scores as a band, with a stale (greyed) state. |
| `web/src/components/studio/StudioSections.jsx` | The section editor: summary textarea, skills/experience/etc. lists, blur callbacks. |
| `web/src/components/studio/PdfPane.jsx` | The PDF iframe, Recompile button, and the "N changes not yet compiled" warning. |
| `web/src/components/studio/__tests__/*.test.jsx` | One test file per component above. |
| `web/src/App.jsx:64` | Add the route. One line. |
| `web/src/components/JobTable.jsx` | Add the Studio entry link. |

Splitting the hook out of the page is the load-bearing decision: the stale-discard
rule is the one piece with real failure modes, and it is far easier to test as a
hook with a stubbed compile function than through a rendered page.

---

## Task 1: Route and Studio shell

**Files:**
- Create: `web/src/pages/ResumeStudio.jsx`
- Create: `web/src/pages/__tests__/ResumeStudio.test.jsx`
- Modify: `web/src/App.jsx` (add one `<Route>` beside line 64)

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: default-exported `ResumeStudio` component, routed at `/jobs/:jobId/studio`. Later tasks mount their components inside it.

- [ ] **Step 1: Write the failing test**

```jsx
// web/src/pages/__tests__/ResumeStudio.test.jsx
/**
 * The Studio must open even when generation has not run.
 *
 * The spec is explicit: "On failure the editor still opens, empty, with the
 * error and a retry — the Studio is the destination." GET .../sections returns
 * 404 for any job with no tailored .tex, which is the common case for a job
 * the user just scored. A Studio that renders a blank screen on 404 would be
 * unreachable for exactly the jobs it is most useful for.
 */
import { render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import ResumeStudio from '../ResumeStudio';
import * as api from '../../api';

const JOB = { job_id: 'j1', title: 'Platform Engineer', company: 'Acme' };

function renderStudio() {
  return render(
    <MemoryRouter initialEntries={['/jobs/j1/studio']}>
      <Routes>
        <Route path="/jobs/:jobId/studio" element={<ResumeStudio />} />
      </Routes>
    </MemoryRouter>,
  );
}

describe('ResumeStudio shell', () => {
  beforeEach(() => vi.restoreAllMocks());

  it('shows the job title and company once loaded', async () => {
    vi.spyOn(api, 'apiGet').mockImplementation((url) =>
      url.endsWith('/sections')
        ? Promise.resolve({ sections: { summary: 'hi' }, jd_analysis: {} })
        : Promise.resolve(JOB),
    );
    renderStudio();
    await waitFor(() => expect(screen.getByText(/Platform Engineer/)).toBeInTheDocument());
    expect(screen.getByText(/Acme/)).toBeInTheDocument();
  });

  it('still opens when no tailored resume exists yet', async () => {
    vi.spyOn(api, 'apiGet').mockImplementation((url) =>
      url.endsWith('/sections')
        ? Promise.reject(new Error('No tailored .tex found for job j1. Run tailoring first.'))
        : Promise.resolve(JOB),
    );
    renderStudio();
    await waitFor(() => expect(screen.getByRole('status')).toBeInTheDocument());
    expect(screen.getByRole('status')).toHaveTextContent(/no tailored resume/i);
    // The destination still rendered — the header is present, not a blank page.
    expect(screen.getByText(/Platform Engineer/)).toBeInTheDocument();
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/pages/__tests__/ResumeStudio.test.jsx`
Expected: FAIL — `Failed to resolve import "../ResumeStudio"`.

- [ ] **Step 3: Write minimal implementation**

```jsx
// web/src/pages/ResumeStudio.jsx
import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { apiGet } from '../api';

export default function ResumeStudio() {
  const { jobId } = useParams();
  const [job, setJob] = useState(null);
  const [sections, setSections] = useState(null);
  const [jdAnalysis, setJdAnalysis] = useState(null);
  const [sectionsError, setSectionsError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    Promise.all([
      apiGet(`/api/dashboard/jobs/${jobId}`).catch(() => null),
      // A 404 here is ordinary: the job has not been tailored yet. The Studio
      // is the destination either way, so this resolves rather than rejects.
      apiGet(`/api/dashboard/jobs/${jobId}/sections`).catch((e) => ({ __error: e })),
    ]).then(([jobRow, secResult]) => {
      if (cancelled) return;
      setJob(jobRow);
      if (secResult?.__error) {
        setSectionsError(secResult.__error.message || 'Could not load sections');
      } else {
        setSections(secResult.sections || null);
        setJdAnalysis(secResult.jd_analysis || null);
      }
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [jobId]);

  return (
    <div className="p-4">
      <header className="mb-4">
        <h1 className="text-xl font-bold">{job?.title || (loading ? 'Loading…' : 'Job')}</h1>
        <p className="text-sm text-stone-500">{job?.company}</p>
      </header>

      {sectionsError && (
        <div role="status" className="mb-4 p-3 border-2 border-yellow-dark bg-yellow-light text-sm">
          No tailored resume for this job yet — generate one to start editing.
          <span className="block text-xs text-stone-500 mt-1">{sectionsError}</span>
        </div>
      )}

      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <div data-testid="studio-left" />
        <div data-testid="studio-right" />
      </div>
    </div>
  );
}
```

Add to `web/src/App.jsx`, immediately after the `/jobs/:jobId` route:

```jsx
<Route path="/jobs/:jobId/studio" element={<ResumeStudio />} />
```

and the import beside the other page imports:

```jsx
import ResumeStudio from './pages/ResumeStudio';
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run src/pages/__tests__/ResumeStudio.test.jsx`
Expected: PASS, 2 tests.

- [ ] **Step 5: Commit**

```bash
git add web/src/pages/ResumeStudio.jsx web/src/pages/__tests__/ResumeStudio.test.jsx web/src/App.jsx
git commit -m "feat(studio): route and shell that opens even without a tailored resume"
```

---

## Task 2: The hash-keyed compile queue

**Files:**
- Create: `web/src/components/studio/useHashedCompile.js`
- Create: `web/src/components/studio/__tests__/useHashedCompile.test.jsx`

**Interfaces:**
- Consumes: nothing.
- Produces:
  ```js
  useHashedCompile(sections, compileFn) -> {
    pdfUrl: string | null,
    compiling: boolean,
    pendingChanges: number,   // edits made since the displayed PDF was produced
    error: string | null,
    requestCompile: () => void,
  }
  ```
  `compileFn(sections) -> Promise<string>` resolves to a PDF URL. Task 7 passes the real one.

- [ ] **Step 1: Write the failing test**

```jsx
// web/src/components/studio/__tests__/useHashedCompile.test.jsx
/**
 * A PDF pane must never silently show an older document than the editor.
 *
 * Compiles take ~15s and are enqueued on blur, so two can be outstanding at
 * once and they can finish out of order. Without a key, the LAST response to
 * arrive wins — which may be the OLDER document, rendered with no indication
 * that it is stale. Keying each result by the hash of the content it was
 * compiled from, and discarding any result whose hash is no longer current,
 * makes that case impossible.
 */
import { act, renderHook, waitFor } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { useHashedCompile } from '../useHashedCompile';

const A = { summary: 'first' };
const B = { summary: 'second' };

describe('useHashedCompile', () => {
  it('discards a stale result that lands after a newer one', async () => {
    let resolveA, resolveB;
    const compile = vi.fn((s) =>
      s.summary === 'first'
        ? new Promise((r) => { resolveA = () => r('pdf-A'); })
        : new Promise((r) => { resolveB = () => r('pdf-B'); }));

    const { result, rerender } = renderHook(({ s }) => useHashedCompile(s, compile), {
      initialProps: { s: A },
    });

    act(() => result.current.requestCompile());
    rerender({ s: B });
    act(() => result.current.requestCompile());

    // B finishes first, then the older A arrives late.
    await act(async () => { resolveB(); });
    await waitFor(() => expect(result.current.pdfUrl).toBe('pdf-B'));
    await act(async () => { resolveA(); });

    expect(result.current.pdfUrl).toBe('pdf-B');
  });

  it('does not recompile when the content has not changed', async () => {
    const compile = vi.fn(() => Promise.resolve('pdf-1'));
    const { result } = renderHook(() => useHashedCompile(A, compile));

    await act(async () => { result.current.requestCompile(); });
    await waitFor(() => expect(result.current.pdfUrl).toBe('pdf-1'));
    await act(async () => { result.current.requestCompile(); });

    expect(compile).toHaveBeenCalledTimes(1);
  });

  it('counts uncompiled changes so the pane can say so', async () => {
    const compile = vi.fn(() => Promise.resolve('pdf-1'));
    const { result, rerender } = renderHook(({ s }) => useHashedCompile(s, compile), {
      initialProps: { s: A },
    });
    await act(async () => { result.current.requestCompile(); });
    await waitFor(() => expect(result.current.pdfUrl).toBe('pdf-1'));

    expect(result.current.pendingChanges).toBe(0);
    rerender({ s: B });
    expect(result.current.pendingChanges).toBe(1);
  });

  it('surfaces a compile failure without clearing the last good PDF', async () => {
    let call = 0;
    const compile = vi.fn(() => {
      call += 1;
      return call === 1 ? Promise.resolve('pdf-1') : Promise.reject(new Error('tectonic exploded'));
    });
    const { result, rerender } = renderHook(({ s }) => useHashedCompile(s, compile), {
      initialProps: { s: A },
    });
    await act(async () => { result.current.requestCompile(); });
    await waitFor(() => expect(result.current.pdfUrl).toBe('pdf-1'));

    rerender({ s: B });
    await act(async () => { result.current.requestCompile(); });
    await waitFor(() => expect(result.current.error).toMatch(/tectonic exploded/));
    expect(result.current.pdfUrl).toBe('pdf-1');
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/components/studio/__tests__/useHashedCompile.test.jsx`
Expected: FAIL — `Failed to resolve import "../useHashedCompile"`.

- [ ] **Step 3: Write minimal implementation**

```js
// web/src/components/studio/useHashedCompile.js
import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Stable content hash for a sections object.
 *
 * JSON.stringify is key-order dependent, and React state updates can produce
 * the same content with different key order, which would look like a change
 * and trigger a needless 15s compile. Sorting the keys makes the hash a
 * function of content alone.
 */
export function hashSections(sections) {
  const canonical = JSON.stringify(sections, (_key, value) =>
    value && typeof value === 'object' && !Array.isArray(value)
      ? Object.keys(value).sort().reduce((acc, k) => { acc[k] = value[k]; return acc; }, {})
      : value,
  );
  // djb2. Not cryptographic — this only needs to distinguish edits from
  // non-edits within one browser session.
  let h = 5381;
  for (let i = 0; i < canonical.length; i += 1) h = ((h << 5) + h + canonical.charCodeAt(i)) | 0;
  return String(h);
}

export function useHashedCompile(sections, compileFn) {
  const [pdfUrl, setPdfUrl] = useState(null);
  const [compiling, setCompiling] = useState(false);
  const [error, setError] = useState(null);
  const [renderedHash, setRenderedHash] = useState(null);

  const currentHash = hashSections(sections);
  const currentHashRef = useRef(currentHash);
  useEffect(() => { currentHashRef.current = currentHash; }, [currentHash]);

  const inFlight = useRef(new Set());

  const requestCompile = useCallback(() => {
    const hash = currentHashRef.current;
    if (hash === renderedHash) return;      // nothing changed
    if (inFlight.current.has(hash)) return; // already queued for this exact content
    inFlight.current.add(hash);
    setCompiling(true);
    setError(null);

    Promise.resolve(compileFn(sections))
      .then((url) => {
        // THE RULE: a result for content that is no longer current is dropped,
        // never displayed. Without this, an out-of-order completion renders an
        // older document with nothing on screen saying so.
        if (hash !== currentHashRef.current) return;
        setPdfUrl(url);
        setRenderedHash(hash);
      })
      .catch((e) => {
        if (hash !== currentHashRef.current) return;
        // Keep the last good PDF on screen; a stale-but-labelled document is
        // more useful than an empty pane.
        setError(e.message || 'Compile failed');
      })
      .finally(() => {
        inFlight.current.delete(hash);
        if (inFlight.current.size === 0) setCompiling(false);
      });
  }, [compileFn, sections, renderedHash]);

  return {
    pdfUrl,
    compiling,
    error,
    pendingChanges: renderedHash !== null && renderedHash !== currentHash ? 1 : 0,
    requestCompile,
  };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run src/components/studio/__tests__/useHashedCompile.test.jsx`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/studio/useHashedCompile.js web/src/components/studio/__tests__/useHashedCompile.test.jsx
git commit -m "feat(studio): hash-keyed compile queue that discards stale results"
```

---

## Task 3: Coverage panel

**Files:**
- Create: `web/src/components/studio/CoveragePanel.jsx`
- Create: `web/src/components/studio/__tests__/CoveragePanel.test.jsx`

**Interfaces:**
- Consumes: nothing from earlier tasks.
- Produces: `<CoveragePanel keyMatches={string[]} gaps={string[]} requirementMap={[{requirement, evidence, severity}]} />`

- [ ] **Step 1: Write the failing test**

```jsx
// web/src/components/studio/__tests__/CoveragePanel.test.jsx
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/components/studio/__tests__/CoveragePanel.test.jsx`
Expected: FAIL — cannot resolve `../CoveragePanel`.

- [ ] **Step 3: Write minimal implementation**

```jsx
// web/src/components/studio/CoveragePanel.jsx
import { Check, X } from 'lucide-react';

const MET = 'met';

export default function CoveragePanel({ keyMatches = [], gaps = [], requirementMap = [] }) {
  const hasReqs = requirementMap.length > 0;
  if (!hasReqs && keyMatches.length === 0 && gaps.length === 0) return null;

  const metCount = requirementMap.filter((r) => r.severity === MET).length;

  return (
    <section className="p-3 border-2 border-stone-200 bg-stone-50" aria-label="JD coverage">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">JD Coverage</span>
        {hasReqs && (
          <span data-testid="coverage-count" className="font-mono text-xs font-bold">
            {metCount}/{requirementMap.length}
          </span>
        )}
      </div>

      {hasReqs ? (
        <ul className="space-y-1">
          {requirementMap.map((r, i) => {
            const met = r.severity === MET;
            return (
              <li
                key={`${r.requirement}-${i}`}
                data-testid={`req-${i}`}
                data-severity={r.severity}
                className="flex gap-2 text-xs"
              >
                {met
                  ? <Check size={14} className="shrink-0 mt-0.5 text-success" aria-label="met" />
                  : <X size={14} className="shrink-0 mt-0.5 text-error" aria-label="gap" />}
                <span>
                  <span className="font-medium">{r.requirement}</span>
                  {r.evidence && <span className="block text-stone-500">{r.evidence}</span>}
                </span>
              </li>
            );
          })}
        </ul>
      ) : (
        <div className="flex flex-wrap gap-1">
          {keyMatches.map((k) => (
            <span key={k} className="text-[10px] px-1.5 py-0.5 border-2 border-success bg-success-light">{k}</span>
          ))}
          {gaps.map((g) => (
            <span key={g} className="text-[10px] px-1.5 py-0.5 border-2 border-error bg-error-light">{g}</span>
          ))}
        </div>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run src/components/studio/__tests__/CoveragePanel.test.jsx`
Expected: PASS, 4 tests.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/studio/CoveragePanel.jsx web/src/components/studio/__tests__/CoveragePanel.test.jsx
git commit -m "feat(studio): coverage panel — show the requirement map that was already computed"
```

---

## Task 4: Banded score strip

**Files:**
- Create: `web/src/components/studio/ScoreStrip.jsx`
- Create: `web/src/components/studio/__tests__/ScoreStrip.test.jsx`

**Interfaces:**
- Consumes: nothing.
- Produces: `<ScoreStrip ats={n} hiringManager={n} techRecruiter={n} stale={bool} />`

- [ ] **Step 1: Write the failing test**

```jsx
// web/src/components/studio/__tests__/ScoreStrip.test.jsx
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
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/components/studio/__tests__/ScoreStrip.test.jsx`
Expected: FAIL — cannot resolve `../ScoreStrip`.

- [ ] **Step 3: Write minimal implementation**

```jsx
// web/src/components/studio/ScoreStrip.jsx
const EN_DASH = '–';

export default function ScoreStrip({ ats, hiringManager, techRecruiter, stale = false }) {
  const values = [ats, hiringManager, techRecruiter].filter((v) => typeof v === 'number');
  const scored = values.length > 0;
  const lo = scored ? Math.min(...values) : null;
  const hi = scored ? Math.max(...values) : null;

  return (
    <section
      data-testid="score-strip"
      data-stale={stale ? 'true' : 'false'}
      aria-label="Match scores"
      className={`p-3 border-2 border-stone-200 ${stale ? 'opacity-50' : ''}`}
    >
      {!scored ? (
        <span className="text-xs text-stone-500">Not scored yet</span>
      ) : (
        <>
          <div className="flex items-baseline gap-2">
            <span className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">Match</span>
            <span data-testid="score-band" className="font-mono text-lg font-bold">
              {lo === hi ? `${lo}` : `${lo}${EN_DASH}${hi}`}
            </span>
          </div>
          <div className="flex gap-4 mt-1 text-xs font-mono text-stone-600">
            <span data-testid="score-ats">ATS {ats}</span>
            <span data-testid="score-hm">HM {hiringManager}</span>
            <span data-testid="score-tr">TR {techRecruiter}</span>
          </div>
        </>
      )}
    </section>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run src/components/studio/__tests__/ScoreStrip.test.jsx`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/studio/ScoreStrip.jsx web/src/components/studio/__tests__/ScoreStrip.test.jsx
git commit -m "feat(studio): score strip as a band, because the measurement is not a point"
```

---

## Task 5: Section editor

**Files:**
- Create: `web/src/components/studio/StudioSections.jsx`
- Create: `web/src/components/studio/__tests__/StudioSections.test.jsx`

**Interfaces:**
- Consumes: nothing.
- Produces: `<StudioSections sections={obj} onChange={(next) => {}} onSectionBlur={() => {}} />`
  `onChange` receives the whole updated sections object. `onSectionBlur` fires once per field blur and is what Task 7 wires to `requestCompile`.

- [ ] **Step 1: Write the failing test**

```jsx
// web/src/components/studio/__tests__/StudioSections.test.jsx
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
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/components/studio/__tests__/StudioSections.test.jsx`
Expected: FAIL — cannot resolve `../StudioSections`.

- [ ] **Step 3: Write minimal implementation**

```jsx
// web/src/components/studio/StudioSections.jsx
const FIELD = 'w-full text-sm p-2 border-2 border-stone-200 focus:border-stone-400 outline-none';

export default function StudioSections({ sections, onChange, onSectionBlur }) {
  if (!sections) {
    return (
      <div role="status" className="p-3 border-2 border-stone-200 text-sm text-stone-500">
        Nothing to edit yet — generate a tailored resume first.
      </div>
    );
  }

  // Every edit produces a whole new sections object. Structural sharing is not
  // worth the bug surface here: the object is small, and a single owner of the
  // full shape is what makes the hash in useHashedCompile meaningful.
  const set = (patch) => onChange({ ...sections, ...patch });

  const setExperienceBullet = (ei, bi, value) => {
    const experience = sections.experience.map((entry, i) =>
      i !== ei ? entry : { ...entry, bullets: entry.bullets.map((b, j) => (j === bi ? value : b)) },
    );
    set({ experience });
  };

  const setSkill = (i, value) => {
    set({ skills: sections.skills.map((s, j) => (j === i ? { ...s, items: value } : s)) });
  };

  return (
    <div className="space-y-4">
      <div>
        <label htmlFor="studio-summary" className="block text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-1">
          Summary
        </label>
        <textarea
          id="studio-summary"
          className={FIELD}
          rows={3}
          value={sections.summary || ''}
          onChange={(e) => set({ summary: e.target.value })}
          onBlur={onSectionBlur}
        />
      </div>

      {(sections.skills || []).length > 0 && (
        <div>
          <span className="block text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-1">Skills</span>
          {sections.skills.map((s, i) => (
            <div key={s.category || i} className="mb-2">
              <label htmlFor={`skill-${i}`} className="text-xs text-stone-500">{s.category}</label>
              <input
                id={`skill-${i}`}
                className={FIELD}
                value={s.items || ''}
                onChange={(e) => setSkill(i, e.target.value)}
                onBlur={onSectionBlur}
              />
            </div>
          ))}
        </div>
      )}

      {(sections.experience || []).map((entry, ei) => (
        <div key={`${entry.company}-${ei}`}>
          <span className="block text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-1">
            {entry.company} — {entry.title}
          </span>
          {(entry.bullets || []).map((b, bi) => (
            <input
              key={bi}
              aria-label={`${entry.company} bullet ${bi + 1}`}
              className={`${FIELD} mb-1`}
              value={b}
              onChange={(e) => setExperienceBullet(ei, bi, e.target.value)}
              onBlur={onSectionBlur}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run src/components/studio/__tests__/StudioSections.test.jsx`
Expected: PASS, 5 tests.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/studio/StudioSections.jsx web/src/components/studio/__tests__/StudioSections.test.jsx
git commit -m "feat(studio): structured section editor, no LaTeX surface"
```

---

## Task 6: PDF pane

**Files:**
- Create: `web/src/components/studio/PdfPane.jsx`
- Create: `web/src/components/studio/__tests__/PdfPane.test.jsx`

**Interfaces:**
- Consumes: the return shape of `useHashedCompile` (Task 2).
- Produces: `<PdfPane pdfUrl={s} compiling={b} pendingChanges={n} error={s} onRecompile={fn} />`

- [ ] **Step 1: Write the failing test**

```jsx
// web/src/components/studio/__tests__/PdfPane.test.jsx
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
 * already.
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
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/components/studio/__tests__/PdfPane.test.jsx`
Expected: FAIL — cannot resolve `../PdfPane`.

- [ ] **Step 3: Write minimal implementation**

```jsx
// web/src/components/studio/PdfPane.jsx
import Button from '../ui/Button';

export default function PdfPane({ pdfUrl, compiling, pendingChanges, error, onRecompile }) {
  return (
    <div className="flex flex-col gap-2 h-full">
      <div className="flex items-center gap-2">
        <Button onClick={onRecompile} disabled={compiling}>
          {compiling ? 'Compiling…' : 'Recompile'}
        </Button>
        {pendingChanges > 0 && !compiling && (
          <span role="status" className="text-xs text-yellow-dark">
            {pendingChanges} {pendingChanges === 1 ? 'change' : 'changes'} not yet compiled
          </span>
        )}
      </div>

      {error && (
        <div role="alert" className="p-2 border-2 border-error bg-error-light text-xs">{error}</div>
      )}

      {pdfUrl ? (
        // Requires Content-Disposition: inline on the presigned URL. An
        // `attachment` disposition makes the browser download rather than
        // render, which blanked this preview once already.
        <iframe title="Resume preview" src={pdfUrl} className="w-full flex-1 min-h-[600px] border-2 border-stone-200" />
      ) : (
        <div className="flex-1 min-h-[600px] border-2 border-dashed border-stone-200 grid place-items-center text-sm text-stone-400">
          No PDF yet
        </div>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npx vitest run src/components/studio/__tests__/PdfPane.test.jsx`
Expected: PASS, 6 tests.

- [ ] **Step 5: Commit**

```bash
git add web/src/components/studio/PdfPane.jsx web/src/components/studio/__tests__/PdfPane.test.jsx
git commit -m "feat(studio): PDF pane that is honest about staleness"
```

---

## Task 7: Compose the Studio and add the entry point

**Files:**
- Modify: `web/src/pages/ResumeStudio.jsx` (replace the two placeholder divs)
- Modify: `web/src/pages/__tests__/ResumeStudio.test.jsx` (add integration tests)
- Modify: `web/src/components/JobTable.jsx` (add the entry link)

**Interfaces:**
- Consumes: `useHashedCompile` (Task 2), `CoveragePanel` (Task 3), `ScoreStrip` (Task 4), `StudioSections` (Task 5), `PdfPane` (Task 6).
- Produces: the working Studio.

- [ ] **Step 1: Write the failing test**

Append to `web/src/pages/__tests__/ResumeStudio.test.jsx`:

```jsx
describe('ResumeStudio composition', () => {
  const FULL_JOB = {
    job_id: 'j1', title: 'Platform Engineer', company: 'Acme',
    ats_score: 86, hiring_manager_score: 84, tech_recruiter_score: 90,
    key_matches: ['Kubernetes'], gaps: ['Go'],
    requirement_map: [{ requirement: 'run Kubernetes', evidence: 'did', severity: 'met' }],
    resume_s3_url: 'https://s3/existing.pdf',
  };
  const SECTIONS = {
    header: { name: 'Jane', title: 'SRE', contact: 'j@x.com' },
    summary: 'Platform engineer.', skills: [], experience: [],
    projects: [], education: [], certifications: [],
  };

  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(api, 'apiGet').mockImplementation((url) =>
      url.endsWith('/sections')
        ? Promise.resolve({ sections: SECTIONS, jd_analysis: {} })
        : Promise.resolve(FULL_JOB));
  });

  it('shows coverage, scores, the editor and the PDF together', async () => {
    renderStudio();
    await waitFor(() => expect(screen.getByLabelText('JD coverage')).toBeInTheDocument());
    expect(screen.getByTestId('score-band')).toHaveTextContent('84–90');
    expect(screen.getByLabelText(/summary/i)).toHaveValue('Platform engineer.');
    expect(screen.getByTitle(/resume preview/i)).toHaveAttribute('src', 'https://s3/existing.pdf');
  });

  it('compiles on blur, once, through the sections endpoint', async () => {
    const apiCall = vi.spyOn(api, 'apiCall').mockResolvedValue({ task_id: 't1', poll_url: '/api/tasks/t1' });
    vi.spyOn(api, 'pollPipeline').mockResolvedValue({ status: 'done', result: { pdf_url: 'https://s3/new.pdf' } });

    renderStudio();
    await waitFor(() => expect(screen.getByLabelText(/summary/i)).toBeInTheDocument());

    const summary = screen.getByLabelText(/summary/i);
    fireEvent.change(summary, { target: { value: 'Edited summary.' } });
    fireEvent.blur(summary);

    await waitFor(() => expect(apiCall).toHaveBeenCalledTimes(1));
    expect(apiCall).toHaveBeenCalledWith(
      '/api/dashboard/jobs/j1/sections',
      expect.objectContaining({ sections: expect.objectContaining({ summary: 'Edited summary.' }) }),
    );
  });

  it('does not compile when a blur changed nothing', async () => {
    const apiCall = vi.spyOn(api, 'apiCall').mockResolvedValue({ task_id: 't1', poll_url: '/api/tasks/t1' });
    renderStudio();
    await waitFor(() => expect(screen.getByLabelText(/summary/i)).toBeInTheDocument());
    fireEvent.blur(screen.getByLabelText(/summary/i));
    await new Promise((r) => setTimeout(r, 0));
    expect(apiCall).not.toHaveBeenCalled();
  });

  it('exposes no LaTeX anywhere on the page', async () => {
    const { container } = renderStudio();
    await waitFor(() => expect(screen.getByLabelText(/summary/i)).toBeInTheDocument());
    expect(container.textContent).not.toMatch(/\\documentclass|\\begin\{document\}|tex_s3_key/);
  });
});
```

Add `fireEvent` to the existing `@testing-library/react` import at the top of the file.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd web && npx vitest run src/pages/__tests__/ResumeStudio.test.jsx`
Expected: FAIL — `getByLabelText('JD coverage')` not found; the placeholders render nothing.

- [ ] **Step 3: Write minimal implementation**

Replace the `<div className="grid ...">` block in `ResumeStudio.jsx` with:

```jsx
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        {/* Below 1100px the columns stack, PDF first: on a phone the document
            matters more than the panel. lg: is 1024px, the nearest Tailwind
            breakpoint, and order-* does the reordering. */}
        <div className="space-y-4 order-2 lg:order-1">
          <ScoreStrip
            ats={job?.ats_score}
            hiringManager={job?.hiring_manager_score}
            techRecruiter={job?.tech_recruiter_score}
            stale={pendingChanges > 0}
          />
          <CoveragePanel
            keyMatches={job?.key_matches || []}
            gaps={job?.gaps || []}
            requirementMap={job?.requirement_map || []}
          />
          <StudioSections
            sections={sections}
            onChange={setSections}
            onSectionBlur={requestCompile}
          />
        </div>
        <div className="order-1 lg:order-2">
          <PdfPane
            pdfUrl={pdfUrl || job?.resume_s3_url || null}
            compiling={compiling}
            pendingChanges={pendingChanges}
            error={compileError}
            onRecompile={requestCompile}
          />
        </div>
      </div>
```

Add above the `return`, inside the component:

```jsx
  // One compile per distinct content state. The hook keys results by a hash of
  // `sections` and discards any whose hash is no longer current, so two
  // outstanding compiles finishing out of order cannot render the older one.
  const compileSections = useCallback(async (next) => {
    const { poll_url: pollUrl } = await apiCall(
      `/api/dashboard/jobs/${jobId}/sections`, { sections: next },
    );
    const done = await pollPipeline(pollUrl, { intervalMs: 2000, maxWaitMs: 120000 });
    const url = done?.result?.pdf_url;
    if (!url) throw new Error(done?.error || 'Compile finished without a PDF');
    return url;
  }, [jobId]);

  const {
    pdfUrl, compiling, error: compileError, pendingChanges, requestCompile,
  } = useHashedCompile(sections || {}, compileSections);
```

and the imports:

```jsx
import { useCallback, useEffect, useState } from 'react';
import { apiCall, apiGet, pollPipeline } from '../api';
import CoveragePanel from '../components/studio/CoveragePanel';
import PdfPane from '../components/studio/PdfPane';
import ScoreStrip from '../components/studio/ScoreStrip';
import StudioSections from '../components/studio/StudioSections';
import { useHashedCompile } from '../components/studio/useHashedCompile';
```

In `web/src/components/JobTable.jsx`, add a Studio link in the row's action cell, beside the existing job link:

```jsx
<Link
  to={`/jobs/${job.job_id}/studio`}
  className="text-xs underline text-stone-500 hover:text-stone-900"
>
  Studio
</Link>
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd web && npm test`
Expected: PASS — the whole frontend suite, including the 4 new integration tests.

- [ ] **Step 5: Lint and commit**

```bash
cd web && npm run lint
git add web/src/pages/ResumeStudio.jsx web/src/pages/__tests__/ResumeStudio.test.jsx web/src/components/JobTable.jsx
git commit -m "feat(studio): compose the two-pane Studio and link it from the job table"
```

---

## Self-Review

**Spec coverage.** §3 layout → Tasks 1, 7 (two columns, stacking PDF-first below
the breakpoint). §4 compile pipeline → Task 2 (hash, enqueue, discard-stale) and
Task 6 (the `N changes not yet compiled` indicator). §5.1 coverage → Task 3.
§5.2 banded scores → Task 4; the `num_calls=3` half is explicitly Phase 2 and is
out of scope here, as the spec's phasing says. §5.3 suggestions, §6 generation,
§7 per-section AI → Phases 3 and 4, not this plan. §9 testing → every task ships
tests that were run against the broken behaviour first.

**Known gap, deliberate.** The spec's §6 "Score → Generate → editor opens" flow
is not in Phase 1. Phase 1's entry is the job table, and a job with no tailored
`.tex` opens the Studio with the explanatory banner from Task 1 rather than a
Generate button. Wiring Generate belongs with Phase 2, where the scoring path
changes anyway.

**Type consistency.** `useHashedCompile(sections, compileFn)` returns
`{pdfUrl, compiling, error, pendingChanges, requestCompile}` in Task 2, and
Task 7 destructures exactly those names, renaming only `error` → `compileError`
at the call site. `PdfPane`'s props in Task 6 match what Task 7 passes.
`StudioSections`' `onChange`/`onSectionBlur` in Task 5 match Task 7's
`setSections`/`requestCompile`.

**Task result shape — verified, not assumed.** Read from the source on
2026-09-28: `_do_rebuild_sections` (`app.py:1017`) returns
`{tex_s3_key, pdf_s3_key, pdf_url}`, and the dispatcher stores it as
`_save_task(task_id, user_id, {"status": "done", "result": result})`
(`app.py:1057`). So `done.result.pdf_url` in Task 7's `compileSections` is
correct. Worth stating because Task 7's test stubs `pollPipeline`, and a stub
agreeing with the code proves nothing on its own.

Note that the task result also carries `tex_s3_key`. That is a key, not LaTeX
content, and `compileSections` reads only `pdf_url` — but Task 7's last test
asserts `tex_s3_key` never reaches the DOM, which is what keeps it that way.

**Real risk to watch during execution.** The Studio's PDF is an `<iframe>`, so
the presigned URL must carry `Content-Disposition: inline`. `_refresh_s3_urls`
emits both an inline and an attachment URL; `job.resume_s3_url` must be the
inline one. This regressed once already — an `attachment` disposition blanked
the preview, and the test covering it asserted the filename rather than the
disposition, so it passed while the feature was broken. Verify by loading a
real job in the Studio and confirming the PDF renders rather than downloads.
