# Job Scraper Pipeline Overhaul — Design Spec

**Date**: 2026-03-17
**Status**: Approved
**Scope**: 3-phase improvement covering reliability, quality, and production-readiness

---

## Context

The job automation pipeline has a solid architecture (10-step orchestrator, multi-provider AI with failover, 3-score matching) but several broken scrapers, empty LinkedIn descriptions, naive deduplication, sequential AI matching, no cross-run persistence, and print-based logging. These issues make the pipeline unreliable and wasteful with API quota.

## Bugs Already Fixed (pre-design)

- `playwright-stealth` v2 API break → updated to `Stealth().apply_stealth_async()`
- YC WATS dead API → rewrote to parse Inertia.js SSR data from `/jobs`
- IrishJobs Akamai blocking → requests-first with graceful browser fallback
- No local `.env` support → added `.env` loader in `main.py`
- Gemini provider dead code → wired into `from_config()` as priority #1
- HTTP/2 Playwright errors → added `--disable-http2` flag

## Phase 1: Foundation (Make It Run Reliably)

### 1.1 LinkedIn Job Descriptions
**File**: `scrapers/linkedin_scraper.py`
**Problem**: All LinkedIn jobs have `description=""`. Matcher scores against descriptions.
**Fix**: After collecting cards, click into the top N jobs to fetch the description side-panel. Cap at 15 jobs per query to avoid LinkedIn blocks.

### 1.2 Seen-Jobs Persistence
**File**: new `job_cache.py` + changes to `main.py`
**Problem**: Every run re-processes all jobs. Wastes API quota.
**Fix**: `output/seen_jobs.json` — dict of `{job_id: {first_seen, last_seen, score, matched}}`. Filter before matching. Already in git add line of workflow.

### 1.3 Smarter Pre-Cutoff Ranking
**File**: `main.py` (before `jobs_to_match = unique_jobs[:max_jobs]`)
**Problem**: First N jobs in arbitrary order, not best N.
**Fix**: Score locally by keyword overlap with target roles + recency. Sort descending before cutoff. Zero API cost.

### 1.4 Fuzzy Deduplication
**File**: `main.py` `global_deduplicate()`
**Problem**: "Google" vs "Google Ireland Ltd" not deduped.
**Fix**: Normalize company names (strip Ltd/Inc/GmbH), use SequenceMatcher ratio. Dupe if company > 80% AND title > 85%.

## Phase 2: Quality (Make It Smart)

### 2.1 Batch AI Matching
**File**: `matcher.py`
**Problem**: 30 jobs = 30 serial AI calls.
**Fix**: Batch 5 jobs per prompt. Return JSON array. Cuts calls from 30 to 6.

### 2.2 Quick-Reject Pre-Filter
**File**: new logic in `main.py` or `matcher.py`
**Problem**: Obviously wrong jobs burn AI tokens.
**Fix**: Local keyword filter. Reject 8+ years required, security clearance, C-level, wrong geography. Saves 30-50% tokens.

### 2.3 JSON Response Validation + Retry
**File**: `matcher.py`, `resume_scorer.py`, `contact_finder.py`
**Problem**: Malformed JSON silently drops jobs.
**Fix**: Robust JSON extractor (handles markdown fences, trailing text), field validation, one retry on parse failure.

### 2.4 Tailoring Cache Invalidation
**File**: `tailorer.py` + `ai_client.py`
**Problem**: Updated base resume still gets stale cached tailoring.
**Fix**: Include hash of base resume in cache key.

## Phase 3: Production-Grade (Make It Solid)

### 3.1 Proper Logging
**Files**: all modules
**Problem**: ~60 print() statements with inconsistent formatting.
**Fix**: Python `logging` module. INFO to console, DEBUG to file. Per-module loggers.

### 3.2 Error Recovery / Checkpointing
**File**: `main.py`
**Problem**: Crash at Step 7 loses Steps 1-6 work.
**Fix**: Save `checkpoint.json` after each step. Resume from last completed step on restart.

### 3.3 Job Database (SQLite)
**File**: new `job_db.py` + changes to `main.py`
**Problem**: Jobs only exist in-memory. No history or querying.
**Fix**: `output/jobs.db` with tables: jobs, matches, runs. Replaces seen_jobs.json. Excel tracker becomes an export view.

### 3.4 Retry with Exponential Backoff
**File**: `ai_client.py`, `scrapers/base.py`
**Problem**: Failed API calls silently swallowed or crash pipeline.
**Fix**: `@retry` decorator for all provider and scraper methods. Distinguish retryable (429, 500, timeout) from permanent (401, 404) errors.

## Build Order

Phase 1 → Phase 2 → Phase 3. Each phase is independently useful.

## Out of Scope

- Full async pipeline rewrite (ThreadPoolExecutor is adequate)
- Test suite (get it running first)
- UI/dashboard (Excel tracker + email is sufficient for now)
