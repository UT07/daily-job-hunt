/**
 * Pull company / title / location out of a pasted job description.
 *
 * WHY THIS SHAPE, measured rather than assumed. Three obvious designs were
 * tried against 37 real pasted JDs (web/src/lib/__fixtures__/real_jds.json,
 * ground truth = the values the user typed by hand) and each failed:
 *
 *   "the first line is the title"  — real pastes are body text with no
 *   header. One begins "About Zscaler Zscaler accelerates digital..." as a
 *   single unwrapped line; the General Motors paste is a DOM scrape broken
 *   mid-sentence. Zero of 37 lead with a title line.
 *
 *   "the LinkedIn URL slug has title-at-company" — real LinkedIn job URLs
 *   are numeric only: /jobs/view/4476593709/. No slug, ever.
 *
 *   "parse Company · Location like LinkedIn shows it" — that line lives in
 *   the page chrome, not in the description the user copies.
 *
 * What DOES survive contact with the data is the ATS URL (Greenhouse, Ashby,
 * Workday and friends all carry the employer in the path) and a small set of
 * first-paragraph openers that company boilerplate almost always uses.
 *
 * CONFIDENCE IS PART OF THE OUTPUT, not a detail. A wrong auto-fill is worse
 * than an empty box: the user does not notice it and submits the wrong
 * company, whereas an empty box is visibly their job to fill. So each field
 * reports where it came from, the form only fills blanks, and the UI says
 * what it guessed. See the precision numbers in jdExtract.test.js.
 */

// Matches the fixture window. The extractor deliberately reads only the head
// of the document: company boilerplate and location lines are front-loaded,
// while the tail is responsibilities and benefits that produce false hits.
export const HEAD_CHARS = 2500;

// Hosts that put the EMPLOYER in the URL path. The path index is the segment
// holding the company, after dropping empty segments.
const ATS_PATH_COMPANY = [
  { host: /(^|\.)greenhouse\.io$/,       segment: 0, skip: ['embed'] },
  { host: /(^|\.)job-boards\.greenhouse\.io$/, segment: 0, skip: [] },
  { host: /(^|\.)lever\.co$/,            segment: 0, skip: [] },
  { host: /(^|\.)ashbyhq\.com$/,         segment: 0, skip: [] },
  { host: /(^|\.)careerpuck\.com$/,      segment: 1, skip: [] }, // /job-board/{co}/job/..
  { host: /(^|\.)smartrecruiters\.com$/, segment: 0, skip: [] },
  { host: /(^|\.)workable\.com$/,        segment: 0, skip: [] },
  { host: /(^|\.)bamboohr\.com$/,        segment: 1, skip: ['careers'] },
  { host: /(^|\.)teamtailor\.com$/,      segment: 0, skip: [] },
  { host: /(^|\.)recruitee\.com$/,       segment: 0, skip: [] },
  { host: /(^|\.)breezy\.hr$/,           segment: 0, skip: [] },
  { host: /(^|\.)rippling\.com$/,        segment: 1, skip: ['recruiting'] },
];

// Subdomains that are never the company name.
const GENERIC_SUBDOMAINS = new Set([
  'www', 'jobs', 'job', 'careers', 'career', 'apply', 'hire', 'hiring',
  'boards', 'job-boards', 'talent', 'recruiting', 'recruit', 'work', 'join',
  'emea', 'eu', 'us', 'app', 'my', 'about', 'people',
]);

// Aggregators and ATS hosts whose DOMAIN is not the employer. A hit here means
// "no company from the URL", not "the company is LinkedIn".
const NOT_AN_EMPLOYER = new Set([
  'linkedin', 'indeed', 'glassdoor', 'ziprecruiter', 'monster', 'dice',
  'totaljobs', 'reed', 'jobs', 'irishjobs', 'gradireland', 'adzuna',
  'welcometothejungle', 'otta', 'wellfound', 'angel', 'ycombinator',
  'workatastartup', 'builtin', 'remoteok', 'weworkremotely', 'jobgether',
  'myworkdayjobs', 'workday', 'icims', 'taleo', 'successfactors', 'oraclecloud',
  'eightfold', 'phenompeople', 'avature', 'jazzhr', 'jobvite', 'gh', 'google',
  // Link shorteners. grnh.se produced the company "Grnh" on a real paste.
  'grnh', 'bit', 'lnkd', 'tinyurl', 'ow', 'buff',
]);

// Multi-part public suffixes we must not mistake for the company label.
const COMPOUND_TLDS = /\.(co|com|org|net|ac|gov|edu)\.(uk|ie|au|nz|in|za|jp|br|sg)$/;

function titleCaseSlug(slug) {
  return slug
    .split(/[-_]+/)
    .filter(Boolean)
    .map((w) => (w.length <= 3 && w === w.toLowerCase() && !/^[aeiou]/i.test(w)
      ? w.toUpperCase()                       // "gm", "ey", "ibm"
      : w.charAt(0).toUpperCase() + w.slice(1)))
    .join(' ');
}

/** Company from the apply URL. The highest-precision signal available. */
export function companyFromUrl(rawUrl) {
  if (!rawUrl || typeof rawUrl !== 'string') return null;
  let u;
  try {
    u = new URL(rawUrl.trim().startsWith('http') ? rawUrl.trim() : `https://${rawUrl.trim()}`);
  } catch {
    return null;
  }
  const host = u.hostname.toLowerCase().replace(/\.$/, '');
  const segments = u.pathname.split('/').filter(Boolean).map((s) => s.toLowerCase());

  for (const rule of ATS_PATH_COMPANY) {
    if (!rule.host.test(host)) continue;
    const usable = segments.filter((s) => !rule.skip.includes(s));
    const seg = usable[rule.segment];
    if (seg && !/^\d+$/.test(seg) && seg.length > 1 && !NOT_AN_EMPLOYER.has(seg)) {
      return { value: titleCaseSlug(seg), source: 'ats-url' };
    }
  }

  // Own careers domain: careers.datadoghq.com, stripe.com/jobs, jobs.apple.com.
  const stripped = host.replace(COMPOUND_TLDS, '');
  const labels = stripped.split('.').filter((l) => !GENERIC_SUBDOMAINS.has(l));
  // The registrable label is the last one once the TLD is dropped.
  const candidate = labels.length > 1 ? labels[labels.length - 2] : labels[0];
  if (!candidate || candidate.length < 2) return null;
  if (NOT_AN_EMPLOYER.has(candidate)) return null;
  // datadoghq -> Datadog, getepic -> GetEpic is not recoverable; leave as-is.
  return { value: titleCaseSlug(candidate.replace(/hq$/, '')), source: 'domain' };
}

// "About Zscaler", "GitLab is the intelligent orchestration platform",
// "Join Stripe", "At Toast, we ...". Anchored at a sentence start so a
// mid-paragraph "about our customers" cannot match.
const COMPANY_PATTERNS = [
  /(?:^|\n)\s*About\s+(?:us\s+at\s+)?([A-Z][\w&.''-]*(?:[ \t]+[A-Z][\w&.''-]*){0,3})\b/,
  /(?:^|\n)\s*(?:Welcome\s+to|Join)\s+([A-Z][\w&.''-]*(?:[ \t]+[A-Z][\w&.''-]*){0,3})\b/,
  /(?:^|\n)\s*At\s+([A-Z][\w&.''-]*(?:[ \t]+[A-Z][\w&.''-]*){0,3}),/,
  /(?:^|\n)\s*([A-Z][\w&.''-]*(?:[ \t]+[A-Z][\w&.''-]*){0,2})\s+is\s+(?:a|an|the|on\s+a)\b/,
  /(?:^|\n)\s*Company:?\s*([^\n]{2,60})/i,
];

// Words that mean the match grabbed prose, not a name.
const NOT_A_COMPANY = /^(the|this|our|we|you|your|job|role|position|team|company|description|us|it|they|i)$/i;

export function companyFromBody(jd) {
  const head = (jd || '').slice(0, HEAD_CHARS);
  for (const re of COMPANY_PATTERNS) {
    const m = head.match(re);
    if (!m) continue;
    let value = m[1].trim().replace(/[.,;:]+$/, '');
    // "About Zscaler Zscaler accelerates" -- the name repeats because the
    // heading and the first sentence were concatenated by the copy. Collapse
    // an immediate doubling.
    const words = value.split(/\s+/);
    if (words.length >= 2 && words[0].toLowerCase() === words[1].toLowerCase()) {
      value = words[0];
    }
    if (!value || NOT_A_COMPANY.test(value)) continue;
    if (value.length < 2 || value.length > 60) continue;
    return { value, source: 'body' };
  }
  return null;
}

// Labelled location lines, in descending reliability.
const LOCATION_PATTERNS = [
  /(?:^|\n)\s*(?:Office\s+)?Locations?:?\s*[-–—]?\s*([^\n]{2,80})/i,
  /(?:^|\n)\s*(?:Job\s+)?Locations?\s*[:–—-]\s*([^\n]{2,80})/i,
  /(?:^|\n)\s*Where:?\s*([^\n]{2,80})/i,
  // DELETED, measured: /\b(?:based|located)\s+in\s+(...)/ read the company's
  // HEADQUARTERS out of boilerplate and offered it as the role's location --
  // "San Jose CA" for a job whose location is "Remote - USA". Right 2 of 3 on
  // real pastes, and the failure is systematic rather than unlucky: offices
  // get named in the about-us paragraph far more often than the role's own
  // location does. The labelled forms above survive because "Location:" is an
  // author-stated fact, not an inference.
];

const REMOTE_RE = /\b(fully\s+remote|remote[\s-]?first|100%\s+remote|remote)\b/i;
const HYBRID_RE = /\bhybrid\b/i;
const ONSITE_RE = /\b(on[\s-]?site|in[\s-]office)\b/i;

export function locationFromBody(jd) {
  const head = (jd || '').slice(0, HEAD_CHARS);
  for (const re of LOCATION_PATTERNS) {
    const m = head.match(re);
    if (!m) continue;
    const value = m[1].trim().replace(/[.,;:]+$/, '');
    // "based in Galway. We ..." captured the start of the next sentence, and
    // "located in San Jose CA" sat in a JD whose actual location was
    // "Remote - USA". A label match only counts when what follows reads like a
    // place: no sentence break, no verb-ish tail.
    if (value.length < 2 || value.length > 80) continue;
    if (/[.!?]\s/.test(value) || /\b(we|you|our|the|and|is|are|will)\b/i.test(value)) continue;
    return { value, source: 'label' };
  }
  // NO ARRANGEMENT FALLBACK. Measured, then deleted: inferring the location
  // from "remote"/"hybrid"/"on-site" anywhere in the head proposed a value for
  // 15 of 37 real pastes and was right 47% of the time. Its single most common
  // output was "Hybrid" -- matched on "hybrid cloud" in SRE job descriptions --
  // and it would have overwritten a real Dublin office on four of this user's
  // own jobs. CLAUDE.md #16: measure a detector's false-positive rate before
  // shipping it, not after. A blank box the user fills is strictly better than
  // a plausible wrong value they do not re-read.
  return null;
}

// Titles: only the explicitly labelled forms. Measured against the real set,
// the "we are looking for a X" family reads sentence fragments far more often
// than role names, and a wrong title is the one field the user is guaranteed
// not to re-read before submitting.
const TITLE_PATTERNS = [
  /(?:^|\n)\s*(?:Job\s+)?Title:?\s*([^\n]{3,80})/i,
  /(?:^|\n)\s*(?:Job\s+)?Position:?\s*([^\n]{3,80})/i,
  /(?:^|\n)\s*Role:?\s*([^\n]{3,80})/i,
];

export function titleFromBody(jd) {
  const head = (jd || '').slice(0, HEAD_CHARS);
  for (const re of TITLE_PATTERNS) {
    const m = head.match(re);
    if (!m) continue;
    const value = m[1].trim().replace(/[.,;:]+$/, '');
    if (value.length >= 3 && value.length <= 80 && /[a-z]/.test(value)) {
      return { value, source: 'label' };
    }
  }
  return null;
}

/**
 * Everything that can be read off a paste.
 *
 * Returns only fields it has a candidate for. `sources` says where each came
 * from so the UI can show it and the user can tell a URL-derived company from
 * a guess at prose.
 */
export function extractJobFields(jd, applyUrl) {
  const out = {};
  const sources = {};

  const company = companyFromUrl(applyUrl) || companyFromBody(jd);
  if (company) { out.company = company.value; sources.company = company.source; }

  const location = locationFromBody(jd);
  if (location) { out.location = location.value; sources.location = location.source; }

  const title = titleFromBody(jd);
  if (title) { out.title = title.value; sources.title = title.source; }

  return { fields: out, sources };
}
