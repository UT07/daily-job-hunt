# Phase 2: Resume Editor, UI/UX Redesign, and Quality Testing

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Build an Overleaf-style resume editor, redesign all UI screens with a cohesive dark theme, and add comprehensive automated testing for AI quality, formatting, and UI.

**Architecture:** React split-pane editor with backend LaTeX compilation endpoint. Playwright for UI tests. AI quality tests compare council output vs single-model output and validate formatting constraints (page count, section presence). All screens redesigned with a consistent "Command Center" dark theme.

**Tech Stack:** React 19, Vite, Tailwind v4, React Router v7, Supabase, FastAPI, tectonic (LaTeX), Playwright (UI tests), pytest (backend tests)

---

## Merge Criteria (for current feature/enterprise-saas-pivot branch)

Before merging to main, ALL of these must pass:

- [ ] E2E backend tests: 86/86 passing (currently 85/86)
- [ ] Frontend builds without errors
- [ ] Council produces resumes (verified by `output/ai_quality_log.jsonl` showing `provider: council`)
- [ ] LaTeX PDFs compile and are exactly 2 pages (automated check)
- [ ] Dashboard shows all jobs with scores, assets, and apply links
- [ ] Auth flow works (login, protected endpoints, JWT validation)
- [ ] Status updates persist across page reloads
- [ ] Pipeline dry-run completes without errors
- [ ] No console errors in browser

---

## Screen-by-Screen UI/UX Plan

### Screen 1: Login Page
**Current:** Dark theme, functional
**Needs:** Polish — add background animation, better error states, "forgot password" flow
**Files:** `web/src/pages/LoginPage.jsx`

### Screen 2: Dashboard (Home after login)
**Current:** Dark theme, job table, KPI cards
**Needs:**
- Run history sidebar (last 5 pipeline runs with stats)
- Job count by source (pie chart or bar)
- Quick actions: "Run Pipeline Now", "Export All"
- Column for AI model that generated each resume (from provenance)
- Initial vs tailored score comparison
**Files:** `web/src/pages/Dashboard.jsx`, `web/src/components/StatsBar.jsx`, `web/src/components/JobTable.jsx`

### Screen 3: Resume Editor (NEW — core feature)
**Current:** Does not exist
**Design:** Split-pane Overleaf-style editor
**Left pane:** Section breakdown with per-section scores
**Right pane:** Live PDF preview (iframe or embedded PDF viewer)
**Files:**
- Create: `web/src/pages/ResumeEditor.jsx`
- Create: `web/src/components/SectionEditor.jsx`
- Create: `web/src/components/PdfPreview.jsx`
- Create: `web/src/components/SectionScoreCard.jsx`
- Modify: `app.py` — add `POST /api/compile-latex` endpoint (takes LaTeX, returns PDF)
- Modify: `app.py` — add `POST /api/score-section` endpoint (scores one section)
- Modify: `app.py` — add `POST /api/improve-section` endpoint (AI improves one section using council)

### Screen 4: Tailor Page (existing, needs redesign)
**Current:** JD textarea + 4 action buttons + results cards
**Needs:**
- After tailoring, redirect to Resume Editor with the tailored content loaded
- Show which AI model generated the result
- Add "Upload Your Resume" option (PDF upload → parse → tailor)
- Progress indicator during AI processing
**Files:** `web/src/App.jsx` (the AppContent component)

### Screen 5: Onboarding
**Current:** Light theme, 3-step wizard
**Needs:** Dark theme, resume upload in step 2 actually works, save to Supabase
**Files:** `web/src/pages/Onboarding.jsx`

### Screen 6: Settings
**Current:** Light theme, save fails
**Needs:** Dark theme, working save, resume management section, search config
**Files:** `web/src/pages/Settings.jsx`

### Screen 7: Privacy & Data Export
**Current:** Light theme
**Needs:** Dark theme, working data export ZIP download
**Files:** `web/src/pages/Privacy.jsx`, `web/src/pages/DataExport.jsx`

---

## Task Breakdown

### Phase 2A: Testing Framework (do first — enables quality verification)

#### Task 1: LaTeX PDF Quality Tests
**Files:**
- Create: `tests/test_latex_quality.py`

- [ ] Write test: PDF is exactly 2 pages
- [ ] Write test: PDF contains all required sections (Summary, Skills, Experience, Projects, Education, Certs)
- [ ] Write test: No LaTeX compilation errors
- [ ] Write test: Filename follows naming convention
- [ ] Write test: File size is reasonable (50KB-500KB)
- [ ] Run tests against existing generated PDFs
- [ ] Commit

#### Task 2: AI Quality Tests
**Files:**
- Create: `tests/test_ai_quality.py`

- [ ] Write test: Council produces output (quality_log.jsonl has entries with provider=council)
- [ ] Write test: Tailored resume differs from base resume (not just copied)
- [ ] Write test: Tailored resume preserves all LaTeX structure (\documentclass, \section, \end{document})
- [ ] Write test: Cover letter has 3-4 paragraphs, < 1 page
- [ ] Write test: Match scores are within valid range (0-100)
- [ ] Write test: Quality log tracks provider/model for every artifact
- [ ] Commit

#### Task 3: Playwright UI Tests
**Files:**
- Create: `tests/ui/test_login.py`
- Create: `tests/ui/test_dashboard.py`
- Create: `tests/ui/test_tailor.py`
- Create: `tests/ui/conftest.py`

- [ ] Set up Playwright test config (conftest with browser fixture)
- [ ] Write test: Login page loads, shows email/password form
- [ ] Write test: Dashboard loads, shows KPI cards and job table
- [ ] Write test: Dashboard filter changes update table
- [ ] Write test: Status dropdown changes persist
- [ ] Write test: Tailor page loads, accepts JD input
- [ ] Write test: Score button returns results
- [ ] Write test: Navigation between pages works
- [ ] Run all UI tests
- [ ] Commit

### Phase 2B: Resume Editor (core feature)

#### Task 4: Backend — LaTeX Compilation Endpoint
**Files:**
- Modify: `app.py`

- [ ] Add `POST /api/compile-latex` — accepts LaTeX string, compiles via tectonic, returns PDF bytes
- [ ] Add `POST /api/score-section` — accepts section text + JD, returns per-section ATS/HM/TR scores
- [ ] Add `POST /api/improve-section` — accepts section text + JD + feedback, returns improved section via council
- [ ] Write tests for each endpoint
- [ ] Commit

#### Task 5: Frontend — Split-Pane Editor Component
**Files:**
- Create: `web/src/pages/ResumeEditor.jsx`
- Create: `web/src/components/SectionEditor.jsx`
- Create: `web/src/components/PdfPreview.jsx`
- Create: `web/src/components/SectionScoreCard.jsx`

- [ ] Build SectionEditor: editable textarea per section with score badge
- [ ] Build PdfPreview: iframe that loads compiled PDF from backend
- [ ] Build SectionScoreCard: shows ATS/HM/TR for one section with "Improve" button
- [ ] Build ResumeEditor: split pane layout combining all components
- [ ] Wire "Improve" button to POST /api/improve-section (council)
- [ ] Wire edit changes to trigger recompilation (debounced)
- [ ] Add route `/editor/:jobId` to App.jsx
- [ ] Test: open editor, edit section, see PDF update
- [ ] Commit

#### Task 6: Wire Tailor → Editor Flow
**Files:**
- Modify: `web/src/App.jsx`

- [ ] After tailoring completes, add "Open in Editor" button
- [ ] Clicking it navigates to `/editor/{jobId}` with the tailored LaTeX loaded
- [ ] Dashboard "Resume" asset link also opens the editor
- [ ] Commit

### Phase 2C: UI/UX Redesign (all screens dark theme + polish)

#### Task 7: Onboarding Dark Theme + Fix Save
**Files:**
- Modify: `web/src/pages/Onboarding.jsx`

- [ ] Apply dark theme (slate-900 bg, dark inputs, blue accents)
- [ ] Fix resume upload to call POST /api/resumes/upload
- [ ] Fix search config save
- [ ] Commit

#### Task 8: Settings Dark Theme + Fix Save
**Files:**
- Modify: `web/src/pages/Settings.jsx`

- [ ] Apply dark theme
- [ ] Fix profile save (verify PUT /api/profile works)
- [ ] Add "Connected AI Models" section showing provider status
- [ ] Commit

#### Task 9: Privacy + Data Export Dark Theme
**Files:**
- Modify: `web/src/pages/Privacy.jsx`
- Modify: `web/src/pages/DataExport.jsx`

- [ ] Apply dark theme to both pages
- [ ] Test data export ZIP download
- [ ] Test delete account flow
- [ ] Commit

#### Task 10: Dashboard Enhancements
**Files:**
- Modify: `web/src/components/JobTable.jsx`
- Modify: `web/src/pages/Dashboard.jsx`

- [ ] Add "AI Model" column showing which model generated each resume
- [ ] Add "Initial Score" column (pre-tailoring) when available
- [ ] Add "Run Pipeline" button in dashboard header
- [ ] Add run history section (last 5 runs with stats)
- [ ] Fix missing location/contacts/apply_url for pipeline-generated jobs
- [ ] Commit

### Phase 2D: Pipeline Data Quality Fixes

#### Task 11: Fix Pipeline → Supabase Data Flow
**Files:**
- Modify: `main.py`

- [ ] After matching, insert/update all matched jobs in Supabase (including location, apply_url, description)
- [ ] After tailoring, update Supabase with resume URLs (S3 + local path)
- [ ] After cover letter, update Supabase with cover letter URLs
- [ ] After contact finding, update Supabase with contact JSON (proper LinkedIn URLs)
- [ ] Record AI provenance (provider/model) in Supabase job row
- [ ] Commit

#### Task 12: Fix Contact Finder for Real LinkedIn Profiles
**Files:**
- Modify: `contact_finder.py`

- [ ] Enhance AI prompt to use company-specific knowledge for name guessing
- [ ] Generate Google "site:linkedin.com/in" search URLs that actually find people
- [ ] Add validation: reject contacts with "Find on LinkedIn" as URL
- [ ] Test with 3 real companies and verify LinkedIn searches return relevant people
- [ ] Commit

---

## Dependency Graph

```
Phase 2A (Testing) — do first, enables quality verification
  Task 1 (LaTeX tests)
  Task 2 (AI quality tests)
  Task 3 (Playwright UI tests)

Phase 2B (Resume Editor) — core feature, after 2A
  Task 4 (Backend endpoints) → Task 5 (Frontend editor) → Task 6 (Wire flows)

Phase 2C (UI/UX) — can parallel with 2B
  Task 7 (Onboarding)
  Task 8 (Settings)
  Task 9 (Privacy/Export)
  Task 10 (Dashboard enhancements)

Phase 2D (Pipeline fixes) — can parallel with 2B/2C
  Task 11 (Pipeline → Supabase)
  Task 12 (Contact finder)
```

---

## Verification After Phase 2

1. **Resume Editor**: Open a job → see split pane → edit summary → PDF updates → score updates
2. **Council Quality**: Compare council resume vs single-model resume side by side
3. **LaTeX Quality**: All PDFs are 2 pages, all sections present, no compilation errors
4. **UI Tests**: All Playwright tests pass
5. **Dashboard**: All jobs have scores, assets, contacts, apply links, AI model info
6. **Pipeline**: Full run produces correct artifacts + updates Supabase
7. **Auth**: Login → dashboard → edit → save → logout → login again → data persists
