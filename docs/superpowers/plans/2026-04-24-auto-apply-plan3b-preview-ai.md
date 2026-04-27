# Auto-Apply Plan 3b — AI-Powered Preview Implementation Plan

> Execute AFTER Plan 3a merges. This file is a scoping stub; per-task TDD
> steps will be written at execution time when platform API shapes can be
> pinned against live responses.

**Goal:** Replace the minimal `GET /api/apply/preview/{job_id}` from Plan
3a with a full AI version per design spec
`docs/superpowers/specs/2026-04-11-auto-apply-mode-1-design.md` §7.3.

**Contract preservation:** Response shape from Plan 3a stays identical.
`answers_generated` flips to `true` and `questions` / `answers` arrays get
populated. Any frontend code written against 3a keeps working.

## Expected tasks

0. **URL slug extraction** (prereq added 2026-04-26) — extend `shared/apply_platform.py` (built in [classifier spec](../specs/2026-04-26-apply-platform-classifier-design.md)) with `extract_platform_ids(url) -> {board_token, posting_id} | None`. The greenhouse/ashby fetchers in step 1 cannot construct API URLs without these. Wire the extractor into the same scrape-time + backfill paths as the classifier (one-shot script can re-run since it's additive).

1. **Platform metadata fetchers**
   - `shared/platform_metadata/greenhouse.py` — `GET boards-api.greenhouse.io/v1/boards/{board}/jobs/{id}?questions=true`
   - `shared/platform_metadata/ashby.py` — `GET api.ashbyhq.com/posting-api/job-posting/{uuid}`
   - 404 handling: mark `jobs.is_expired=true`, surface `reason=job_no_longer_available`
   - `follow_redirects=False` to prevent redirect-based URL spoofing

2. **Question classifier** — `shared/question_classifier.py`
   Regex into `custom | eeo | confirmation | marketing | referral` per design §7.3 step 5:
   - EEO: `(gender|ethnicity|race|veteran|disability|self-identify|self identify)`
   - Confirmation: `(confirm|certify|accurate|true.*information|understand|acknowledge)`
   - Marketing: `(marketing|newsletter|subscribe|promotional|updates about)`
   - Referral: `(how.*hear|referral source|source of awareness)`
   - Else: `custom`

3. **AI answer generator** — `shared/answer_generator.py`
   Wrap `ai_complete_cached` from `ai_client.py`; per-category branching:
   - `confirmation`: skip AI, return `ai_answer=False, requires_user_action=True`
   - `eeo`: skip AI, set "Decline to self-identify" or equivalent from options
   - `marketing`: skip AI, set `False`
   - `referral`: fuzzy-match `user.default_referral_source` against options
   - `custom`: AI with 7-day cache, temperature 0.3, max_tokens 300, providers `qwen / nvidia / groq`
   - Post-process: fuzzy-match dropdown options; "Yes" default for unresolvable yes/no

4. **Cover letter loader** — `shared/cover_letter_loader.py`
   - Try `users/{uid}/cover_letters/{job_hash}.tex` from S3
   - Run through `tex_to_plaintext()`
   - Fall back to config default CL template
   - `max_length`: platform metadata value or per-platform default (Greenhouse 10000, Ashby 5000)
   - `include_by_default`: platform `cover_letter_required` → tier-based fallback (S/A on, B/C off)

5. **Preview cache** — extend `ai_cache.db`
   - Key: `apply_preview:{job_id}:{resume_version}`
   - TTL: 10 min
   - Response includes `cache_hit: bool`

6. **Swap preview endpoint orchestration** — replace Plan 3a minimal body with:
   eligibility re-check → cache check → fetch platform metadata → classify questions → load resume meta → load cover letter → generate answers → build response → write cache → return

## Dependencies

- Plan 3a merged (endpoint + response shape exist)
- **Apply Platform Classifier shipped** (spec [2026-04-26](../specs/2026-04-26-apply-platform-classifier-design.md)) — without it, `apply_platform`/`board_token`/`posting_id` are all NULL and the metadata fetchers in Step 1 have nothing to call
- `ai_client.py` AI council — used without modification
- `resume_versions` table (Stage 3.3 Tailor+) for resume metadata

## Estimated size

~6 tasks, ~500 LOC + tests. Should be one focused session of TDD.

## Reference

- Design spec: `docs/superpowers/specs/2026-04-11-auto-apply-mode-1-design.md` §7.3
- Master plan context: `docs/superpowers/specs/2026-04-03-unified-grand-plan.md` Stage 3.4 Apply
- Plan 3a (dependency): `docs/superpowers/plans/2026-04-24-auto-apply-plan3a-websocket-backend.md`
