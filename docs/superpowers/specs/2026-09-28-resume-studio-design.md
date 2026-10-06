# Resume Studio — design

**Date:** 2026-09-28
**Status:** phases 1-3 shipped; **phase 4 specified below and not yet built**
**Last revised:** 2026-10-06 — phase 4 taken from outline to an implementable
design, phasing updated to what is actually deployed, and §7.5 added from a
defect found in the shipped editor.
**Supersedes:** nothing. Extends the existing `ResumeEditor` / `SectionEditor` / `/api/compile-latex` surface.

---

## 1. What this is for

Today the product generates a tailored resume and hands back a PDF. The AI is
invisible: it runs, something appears, and the user has no way to see what it
judged, what it changed, or why. If the result is wrong, the only control is a
Regenerate button that rolls the dice again.

The Studio makes the reasoning visible and the output editable, in one place:

> paste a JD → see the score → **Generate** → land in an editor with the draft,
> the JD coverage beside it, and a live PDF

The same page serves a second entry point — opening any job that already has
artifacts — so tweaking a regenerated resume is the same surface, not a
different mode.

### Non-goals

- The user never edits LaTeX. Not as a toggle, not as an "advanced" pane.
- No WYSIWYG-on-PDF. A PDF is a rendered artefact, not a document model;
  click-to-edit on it requires synctex coordinate mapping and was explicitly
  rejected in favour of side-by-side.
- No full-resume AI instruction. Scoped per-section only — see §7.

---

## 2. The load-bearing decision: sections are the source of truth

Everything — typing, applied suggestions, per-section AI instructions — edits
one structured `sections` object. LaTeX is **derived** from it and spliced into
a preamble the user never sees.

```
sections  ──rebuild_tex_from_sections()──►  .tex  ──tectonic──►  PDF
   ▲
   └── typing · Apply suggestion · AI instruction
```

Why this and not "edit the LaTeX, hide it behind a nicer UI":

- **One escaping site.** A stray `&`, `%`, `_` or `#` in prose is escaped once,
  where sections become LaTeX. The alternative escapes in every edit path.
- **The AI never emits LaTeX.** This removes the entire class of failure the
  preamble-protection design exists to prevent — a model rewriting
  `\newcommand`, dropping a `\usepackage`, or calling a 3-argument macro with 2.
- **Prose and AI edits cannot conflict**, because they are the same operation
  on the same model.

This is not new machinery. `parse_sections.rebuild_tex_from_sections(sections,
base_tex)` already exists and is already used by `POST
/api/dashboard/jobs/{id}/sections`.

---

## 3. Layout

Two columns, matching the geometry of Overleaf, which is the reference the
owner named. The left column is split horizontally.

```
┌───────────────────────────────┬──────────────────────┐
│ AI PANEL                      │                      │
│   ATS 86–90 · HM 84 · TR 88   │   compiled PDF       │
│   Coverage  14/18 ✓           │   (real tectonic     │
│   Suggestions  [Apply]        │    output)           │
├───────────────────────────────┤                      │
│ SECTION EDITOR                │   [Recompile]        │
│   Summary            ▾        │   ⚠ 2 changes not    │
│   Technical Skills   ▾        │      yet compiled    │
│   Experience         ▾        │                      │
│     └ [Ask AI ▸]              │                      │
└───────────────────────────────┴──────────────────────┘
```

- The AI panel is **always visible**, not a tab. The product requirement was
  "AI is visible in the app, not just a model that generated the final output";
  a tab makes it a place you visit.
- Below 1100px the columns stack, PDF first — on a phone the document matters
  more than the panel.
- `JobWorkspace.jsx` is 1,401 lines at 0% coverage. The Studio is a **new
  route and new components**. Nothing is added to that file.

---

## 4. Compile pipeline

Compiling takes ~15s via tectonic in Lambda, so per-keystroke compilation is
not viable. Trigger is **section blur, plus a manual Recompile button** — one
compile per section edited, which is a natural unit of work.

```
blur → derive .tex → hash it
     → hash unchanged?  → do nothing
     → hash changed?    → enqueue compile keyed by that hash
                        → result arrives
                           → hash still current?  → show it
                           → hash stale?          → DISCARD
```

Two properties this buys:

- **Rapid edits collapse.** Three blurs in ten seconds enqueue at most one
  compile per distinct content state.
- **The PDF can never silently show an older version than the editor.** A
  result whose hash no longer matches current state is discarded, not
  displayed. Without this, out-of-order completions render stale output with no
  indication.

While a compile is outstanding or changes are uncompiled, the pane shows
`⚠ N changes not yet compiled`. A visibly stale PDF is fine; an invisibly
stale one is not.

`POST /api/dashboard/jobs/{id}/sections` is currently `202 + poll`. Blur
triggers fire often, so the poll must be cancellable and must not stack.

---

## 5. The AI panel

### 5.1 Coverage — free, and the highest value

Every JD requirement, matched or missing, quoting the JD's own words:

```
✓ Kubernetes           "operate multi-tenant Kubernetes infrastructure"
✓ Terraform            "own Terraform modules"
✗ Incident response    "running production incident response"
✗ Go                   "strong Python and Go"
```

**This requires no new AI work.** `key_matches`, `gaps` and `requirement_map`
are already computed on every scoring run and already stored on the `jobs`
row. They have never been displayed. Rendering them is the single largest
"AI is visible" win per unit of effort in this document.

### 5.2 Scores — and why they must not be live

> **2026-10-06:** what the three scores ARE — and what "trained" does and does
> not mean for them — is specified in
> [`2026-10-06-persona-scoring-design.md`](2026-10-06-persona-scoring-design.md).
> That document is the authority on the numbers; this section is the authority
> on how the Studio *displays* them. The banding decision below is unchanged by
> it and is in fact reinforced: a judgement rendered to one decimal place claims
> a resolution nothing supports.


The owner's requirement: *"I need the scoring to be consistent as we want the
AI to be reliable."*

Measured 2026-09-28, and this is the constraint the design must respect:

- The same job and resume scored **25 to 80** across 30 verified models.
- One model, one identical prompt, `temperature=0`, three consecutive calls:
  three different answers. Temperature controls sampling; it does not control
  mixture-of-experts routing or request batching, neither of which the caller
  can pin.

So a score recomputed on every blur will **jitter**. A user tightens a bullet,
watches ATS drop 88 → 84, and learns not to trust the panel. That is worse
than showing nothing.

**The existing remedy is disabled.** `score_single_job_deterministic(job,
resume_tex, num_calls=1)` takes the median of each perspective across
`num_calls` independent calls — and every one of its four call sites uses the
default of 1, so it takes the median of a single number. Its own docstring
notes the second half: individual calls hit the AI cache, so even at
`num_calls=3` the cache would return one response three times.

Both halves must be enabled for the Studio path:

| path | calls | cache | rationale |
|---|---|---|---|
| batch pipeline | 1 | on | ~58 jobs/run; the ranking only needs to be roughly right, and 3× would triple load on the Groq TPM ceiling that is already the bottleneck |
| **Studio** | **3, median per perspective** | **bypassed** | one job, the number is on screen, and it must not move when nothing changed |

**Display as a band, not a point.** `86–90`, not `88`. Reporting a single
integer implies a precision the measurement does not have. The band is the
min/max across the three calls; when they agree it collapses to a point on its
own, which is itself information.

**Recompute on the same trigger as the compile** (section blur), never per
keystroke. A stale strip greys the previous numbers rather than showing a
spinner — a greyed number is still information.

### 5.3 Suggestions

Per-section, each carrying the text it was generated against:

```
Experience · Clover IT Services
  "Developed React frontends for internal ops dashboards"
  → no quantified impact, and the JD asks for scale
  [Apply]  [Dismiss]
```

**`Apply` edits that one section. It does not regenerate the resume.** One
bullet changes, the diff is inspectable, and undo is a single edit. The
alternative — "AI improves your resume" firing a full regeneration — is what
the current Regenerate button does, and is precisely why the user cannot tell
what changed.

**Anchoring and staleness.** A suggestion points at
`experience[0].bullets[2]`. If that text has changed since the suggestion was
generated, applying it would clobber the user's edit. Each suggestion stores a
hash of the source text; when it no longer matches, the suggestion greys out
with *"you've edited this since — re-analyse?"* rather than silently applying
to content it was not written for.

---

## 6. Generation

Score → **Generate** → the editor opens with the draft.

An explicit button with a visible 40–90s progress state, not a streaming
editor that fills in. The wait is real and honest progress is better than a
half-populated editor that has to handle partial and failed states in place.

On failure the editor still opens, empty, with the error and a retry — the
Studio is the destination, and generation is one of the things that happens
in it.

---

## 7. Per-section AI instruction  *(phase 4 — specified, not built)*

Each section has `Ask AI ▸`, opening a single-line instruction box:

> *"quantify the impact"* · *"make this more DevOps-focused"* · *"cut this to
> three bullets"*

Scoped to **one section**, deliberately:

- A bad result damages one section, not the document.
- The diff is small enough to actually read before accepting.
- Result is shown as a proposed change with Accept / Reject, never applied
  directly.

Full-resume instructions are out of scope. They rewrite everywhere at once,
which makes reviewing the change impractical and a bad result unrecoverable
short of regenerating.

### 7.1 API

Mirrors the existing suggestions endpoint, which is already async and already
polled by `apiCall`:

```
POST /api/dashboard/jobs/{job_id}/instruct   -> 202 {task_id, poll_url}
  body: {section_key: str, section_text: str, instruction: str}
  task result: {section_key, proposed: str, unchanged: bool, refused: str|null}
```

`section_text` comes from the client for the same reason `/suggestions` takes
it: the Studio compiles on blur, so the editor is routinely ahead of the stored
`.tex`, and a proposal anchored to S3 would rewrite text the user has already
changed. The JD is read server-side from the job row — it is not the client's
to supply.

`unchanged: true` is a first-class outcome, not an error. A model that returns
the input is common for vague instructions, and the UI must say "no change
proposed" rather than render an empty diff that looks like a bug.

### 7.2 Validation — what a proposal may be

The proposal replaces **the text of one section**. It is validated before it
reaches the UI:

- **Shape.** A list section (`experience`, `projects`) must come back as a list
  of entry objects; a text section as a string. `SectionEditor` renders these
  differently and a type mismatch produces an editor with nothing in it —
  observed on 2026-10-05 while writing tests, where a string passed for
  `experience` rendered an empty editor that read as a data-loss bug.
- **Containment.** No `\section`, no preamble commands, no `\begin{document}`.
  The proposal cannot introduce or remove a section.
- **Quality, reported not blocking.** `check_weak_bullet_openers` and
  `check_banned_phrases` run on the proposal. A weak opener is shown as a
  warning beside Accept, because the user may still prefer it.
- **Fabrication, blocking.** `check_fabrication` against the base résumé. An
  instruction is user text, and "say I know Kubernetes" must not become a
  claim. A blocked proposal is refused with its reason in `refused`.

### 7.3 Prompt injection

The instruction is user text reaching a model that also sees the JD and the
résumé. Three properties contain it, and they are structural rather than
prompt-level:

- The output is validated as section content (§7.2), so it can only ever
  replace the text of the section it was invoked on.
- It cannot alter other sections, the preamble, or the compile.
- The JD is server-supplied, so an instruction cannot smuggle in a different
  job description.

Note what is deliberately NOT relied on: telling the model to ignore
instructions in the JD. The JD is also untrusted, `guardrails/input_guards`
already fences it, and a second prompt-level request would add no guarantee.

### 7.4 State and staleness

A proposal is anchored to the `section_text` it was computed from. If the user
edits that section while the task is in flight, the proposal is **stale** and
is discarded with a message, not silently applied to different text. Same rule
phase 3 established for suggestions; the implementation should share it rather
than restate it.

### 7.5 Lesson from the shipped editor — carry it into this phase

`ResumeEditor` shipped with two state defects, fixed 2026-10-05, and phase 4
can reintroduce both because it adds a second writer to the same state:

1. The section loader re-ran on `job.resume_s3_url` and overwrote the user's
   unsaved text. Every save changes that URL — the backend mints a fresh
   presigned URL for the same key — so the race fired on the common path, not
   a rare one.
2. The PDF preview seeded from the prop once and never re-synced.

The rule this gives phase 4: **accepting a proposal is an edit like any other.**
It sets the dirty flag, it does not bypass the save path, and it must not
trigger a reload that races itself. A test asserting the accepted text survives
a concurrent refetch belongs in this phase, not after it.

---

## 8. Phasing

Status as of 2026-10-06: phases 1-3 are deployed (`web/src/pages/ResumeStudio.jsx`,
`components/studio/CoveragePanel.jsx`, `components/studio/PdfPane.jsx`, and both
`/suggest` and `/suggestions` endpoints). Phase 4 has no endpoint and no UI.

**Phase 1 — the two-pane Studio.** SHIPPED. Route, layout, section editor, live PDF
with hash-keyed blur compilation, coverage panel, banded scores from a single
scoring call. Uses only data that already exists. This is the phase to land
first.

**Phase 2 — reliable scoring.** SHIPPED. `num_calls=3` with cache bypass on the Studio
path; band derived from the spread.

**Phase 3 — suggestions.** SHIPPED. Suggestion model, anchoring, staleness, Apply/undo.

**Phase 4 — per-section instructions.** Specified in §7. Not built.

Each phase is independently shippable and independently useful. Phase 1 alone
delivers the visible-AI product claim.

---

## 9. Testing

- **Compile ordering.** A stale result must be discarded, not rendered. Assert
  by resolving two compiles out of order and checking the older is dropped.
- **Score stability.** With three stubbed calls returning 84/88/86, the
  displayed band is 84–88 and the stored value is the median, 86.
- **Suggestion staleness.** Editing the anchored text must grey the suggestion
  rather than let Apply proceed.
- **LaTeX never reaches the user.** No editor surface exposes `tex_content`;
  asserted structurally, not by inspection.
- **Escaping.** A section containing `&`, `%`, `_`, `#`, `$` must round-trip
  through `rebuild_tex_from_sections` and compile.
- **E2E.** Extend the existing Playwright suite (135 tests, mocked + live
  projects). Every significant assertion proved to fail against the broken
  behaviour, per that suite's established practice.

---

## 10. Risks and open questions

**The 15s compile is the product's pace.** Blur-triggered compilation makes it
bearable, not invisible. If it proves too slow in use, the next lever is
caching compiled output by section-hash so reverting an edit is instant.

**Suggestion quality is unproven.** No suggestion model exists yet. Phase 3
should begin by generating suggestions for ten real jobs and reading them
before any UI is built — a panel full of generic advice is worse than no
panel.

**Scoring cost at `num_calls=3`.** Studio-only keeps it bounded, but a user
editing heavily will trigger many re-scores. If that bites, debounce to an
explicit Re-score button rather than dropping back to a single call —
consistency is the requirement.

**`parse_resume_sections` depends on AI.** With no client it returns only
`raw_text`. The Studio inherits the content floor added to the upload path
(`sections_have_content`), which rejects a parse that carried nothing across.
