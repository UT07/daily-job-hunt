/**
 * Page object for the job dashboard.
 *
 * One thing worth knowing before reading the selectors: JobTable renders the
 * SAME jobs twice -- a `md:hidden` card stack for phones and a `hidden md:block`
 * table for desktop. Every job's title therefore appears twice in the DOM at
 * any viewport. Anything that reaches for text globally (`getByText('Acme')`)
 * is ambiguous by construction, so everything here is scoped to one of the two
 * containers.
 */

export class Dashboard {
  constructor(page) {
    this.page = page;

    this.heading = page.getByRole('heading', { name: 'Job Dashboard' });
    this.table = page.locator('table');
    this.rows = page.locator('table tbody tr');
    this.mobileCards = page.locator('div.md\\:hidden > div.cursor-pointer');

    this.statusFilter = page.locator('select').filter({ hasText: 'Withdrawn' }).first();
    this.sourceFilter = page.locator('select').filter({ hasText: 'gradireland' }).first();
    this.minScoreSlider = page.locator('input[type="range"]');
    this.titleSearch = page.getByPlaceholder('Search title...');
    this.companySearch = page.getByPlaceholder('Search company...');
    this.applyFilters = page.getByRole('button', { name: 'Apply Filters' });
    this.advancedToggle = page.getByRole('button', { name: /Advanced|Less/ });
    this.clearAllChip = page.getByRole('button', { name: 'Clear all', exact: true });
    this.clearAllButton = page.getByRole('button', { name: 'Clear all filters' });

    // Both toggles are an unlabelled <button> immediately after their <label>,
    // with no text of their own and no accessible name -- so they can only be
    // reached through the label they sit next to.
    this.hideExpiredToggle = page
      .getByText('Hide Expired', { exact: true })
      .locator('xpath=following-sibling::button[1]');
    this.tailoredToggle = page
      .getByText('Tailored', { exact: true })
      .locator('xpath=following-sibling::button[1]');

    this.sortSelect = page.locator('select').filter({ hasText: 'Score (highest)' });
    this.resultCount = page.locator('p', { hasText: /^\d+ jobs?/ }).first();

    this.pastShelf = page.getByRole('button', { name: /Past \/ Outdated/ });
    // The shelf's own rows. Scoped to the <section> because a stale job can
    // also appear in the main table, and an unscoped text match would find the
    // (display:none) mobile card of that row first.
    this.pastShelfSection = page.locator('section').filter({ has: this.pastShelf });
    this.pastShelfRows = this.pastShelfSection.locator('ul > li');
    this.emptyState = page.getByText('No jobs match your current filters');
    this.firstRunEmptyState = page.getByText('No jobs yet.');
    this.errorBanner = page.locator('div.border-error');
  }

  /** True for the GET whose result becomes the visible table -- i.e. not the
   *  `per_page=1` grand-total probe and not the Past/Outdated shelf. */
  static isListResponse(res) {
    return res.url().includes('/api/dashboard/jobs')
      && !res.url().includes('per_page=1')
      && !res.url().includes('lifecycle=');
  }

  /**
   * Run `action` and resolve once the resulting list request has come back.
   *
   * Written as a wrapper rather than a bare `waitForResponse()` after the fact
   * because the response frequently lands before the next line of the test
   * runs -- a post-hoc wait then hangs for the full timeout on a page that is
   * already correct.
   */
  async withListResponse(action) {
    const [res] = await Promise.all([
      this.page.waitForResponse(Dashboard.isListResponse),
      action(),
    ]);
    return res;
  }

  async goto(query = '') {
    await this.withListResponse(() => this.page.goto(`/${query}`));
    await this.heading.waitFor();
    await this.waitForList();
  }

  /** Waits until the list has finished rendering (the loading block is gone
   *  and the "N jobs" count -- which only renders when !loading -- is up). */
  async waitForList() {
    await this.page.getByText('Loading jobs...').waitFor({ state: 'hidden' });
    await this.resultCount.waitFor();
  }

  /** The S/A/B/All tab. `.first()` because once a tier is selected the same
   *  words reappear in the "Filtered by: Tier: Strong Match (A)" chip below. */
  tierTab(letter) {
    const labels = { S: 'Must Apply', A: 'Strong Match', B: 'Worth Trying', All: 'All Jobs' };
    return this.page.getByRole('button', { name: new RegExp(labels[letter]) }).first();
  }

  /** The `hidden md:block` desktop table's title cells, in render order. */
  async rowTitles() {
    return (await this.rows.locator('td:nth-child(3)').allTextContents())
      .map((t) => t.replace(/\s+/g, ' ').trim());
  }

  async rowCompanies() {
    return (await this.rows.locator('td:nth-child(4)').allTextContents())
      .map((t) => t.trim());
  }

  row(titleOrCompany) {
    return this.rows.filter({ hasText: titleOrCompany });
  }

  filterChip(label) {
    return this.page.getByRole('button', { name: new RegExp(label, 'i') })
      .filter({ has: this.page.locator('span[aria-hidden="true"]') });
  }

  pageButton(n) {
    return this.page.getByRole('button', { name: String(n), exact: true });
  }

  get nextPage() { return this.page.getByRole('button', { name: 'Next' }); }
  get prevPage() { return this.page.getByRole('button', { name: 'Prev' }); }
  get firstPage() { return this.page.getByRole('button', { name: 'First' }); }

  /** Current URL query params as a plain object. */
  params() {
    return Object.fromEntries(new URL(this.page.url()).searchParams.entries());
  }
}
