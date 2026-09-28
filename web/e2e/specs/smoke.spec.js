import { test, expect } from '../fixtures/test.js';

test.describe('harness smoke', () => {
  test('an injected session renders the dashboard, not the login page', async ({ page, dashboard, api }) => {
    await dashboard.goto();
    await expect(dashboard.heading).toBeVisible();
    expect(page.url()).not.toContain('/login');
    // The app actually talked to the (fake) backend rather than rendering a
    // shell off stale state.
    expect(api.requestsFor('/api/profile').length).toBeGreaterThan(0);
    expect(api.listRequests().length).toBeGreaterThan(0);
  });

  // `api` is required even though it is unused: it is the fixture that installs
  // the route handlers. Without it the app's fetches fall through to the Vite
  // proxy and nothing ever answers.
  test('every request the app makes carries the session bearer token', async ({ page, dashboard, api }) => {
    const authHeaders = [];
    page.on('request', (req) => {
      if (req.url().includes('/api/')) authHeaders.push(req.headers()['authorization'] || null);
    });
    await dashboard.goto();
    await dashboard.waitForList();
    expect(authHeaders.length).toBeGreaterThan(0);
    for (const h of authHeaders) expect(h).toMatch(/^Bearer /);
  });

  test('the fake backend serves every endpoint the dashboard asks for', async ({ dashboard, api }) => {
    await dashboard.goto();
    await dashboard.waitForList();
    // A 501 from the fake means the app grew a call this suite does not model
    // -- which would make some other spec silently assert against an error
    // state. Fail loudly here instead.
    expect(api.unhandled, `unmocked endpoints: ${api.unhandled.join(', ')}`).toEqual([]);
  });
});
