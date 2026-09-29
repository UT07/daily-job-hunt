/**
 * The app must fit the window.
 *
 * A flex item defaults to `min-width: auto`, which means it refuses to shrink
 * below its content's intrinsic width. AppLayout's main column is a flex child
 * of a row, and it holds pages with wide content (the job table, the Studio's
 * PDF pane). Without `min-w-0` that column grows to fit its content and pushes
 * the whole page wider than the viewport — which reads to a user as "the app
 * is zoomed in and doesn't fit".
 *
 * The cruel part is that the inner `overflow-x-auto` on JobTable (line 387)
 * looks like it already handles this. It cannot: a scroll container only
 * scrolls when something constrains its width, and an unshrinkable ancestor
 * hands it all the width it asks for.
 *
 * Measured in a real browser at a 1024px viewport, reproducing AppLayout's
 * exact structure with the job table's fixed column widths:
 *
 *     without min-w-0 -> main column renders 1388px   (364px of overflow)
 *     with    min-w-0 -> main column renders  800px   (1024 - 224 sidebar)
 *
 * Grid items have the same `min-width: auto` default, so the Studio's two-pane
 * grid needs it on both children.
 *
 * This is a source assertion rather than a rendered measurement because jsdom
 * has no layout engine — `getBoundingClientRect` returns zeros, so a rendering
 * test cannot observe the bug at all. Asserting on the class is weaker than
 * measuring, and it is what this environment can actually check.
 */
import { describe, expect, it } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const here = dirname(fileURLToPath(import.meta.url));
const src = (p) => readFileSync(resolve(here, '../..', p), 'utf8');

describe('the layout shell can shrink', () => {
  it('AppLayout main column carries min-w-0', () => {
    const tsx = src('layouts/AppLayout.jsx');
    const m = tsx.match(/className="flex-1 flex flex-col([^"]*)"/);
    expect(m, 'AppLayout main column not found — did the shell change shape?').toBeTruthy();
    expect(m[1]).toContain('min-w-0');
  });

  it('the shell row still contains a sidebar and the column', () => {
    // If this stops matching, the test above is asserting on nothing.
    const tsx = src('layouts/AppLayout.jsx');
    expect(tsx).toMatch(/className="flex min-h-screen/);
    expect(tsx).toContain('<Sidebar />');
  });

  it('both Studio panes carry min-w-0', () => {
    const tsx = src('pages/ResumeStudio.jsx');
    const grid = tsx.match(/grid grid-cols-1 lg:grid-cols-2[^"]*/);
    expect(grid, 'Studio two-pane grid not found').toBeTruthy();
    const panes = [...tsx.matchAll(/className="([^"]*\border-[12] lg:order-[12][^"]*)"/g)];
    expect(panes.length, 'expected two ordered panes').toBe(2);
    for (const [, cls] of panes) expect(cls).toContain('min-w-0');
  });

  it('the job workspace header title can truncate', () => {
    // A long unbreakable job title beside the back button pushes the row wide
    // for the same reason.
    const tsx = src('pages/JobWorkspace.jsx');
    expect(tsx).toMatch(/className="flex-1 min-w-0">\s*\n\s*<h1[^>]*truncate/);
  });
});
