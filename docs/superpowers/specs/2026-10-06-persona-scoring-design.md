# Persona scoring — design

**Status:** proposed. Supersedes nothing; it is the first written account of
what the three scores are and what they are not.

**Prompted by** a question on 2026-10-05: *"how are the personas trained on
hiring manager and technical recruiter since we don't have any data of what
good looks like"*. The answer is that they are not trained, and the rest of
this document is what to do about it.

---

## 1. What exists today

One model call, one system prompt, three numbers
(`lambdas/pipeline/score_batch.py:401`). The "personas" are three paragraphs
telling the model where to look:

| Score | Prompt instruction, in full |
|---|---|
| ATS | exact keyword matches, title alignment, certifications, section structure, parser compatibility |
| Hiring Manager | impact with metrics, project relevance, trajectory, leadership signals, communication |
| Technical Recruiter | required vs preferred stack, depth, seniority alignment, red flags |

Plus a verbal calibration table (`90-100: exceptional … 50-59: weak`) and three
anti-inflation rules.

### 1.1 What that means, stated plainly

**They are not independent.** One call, one context, one model. Three numbers
from one opinion — correlated by construction. Presenting them as three expert
reviewers is the single most misleading thing the product does.

**They are not trained.** There is no training step, no fitting, no labels.

**They are not validated.** `evals/baseline.json` reports
`tier_accuracy: 0.85`, and `evals/harness.py` says where the labels come from:

> *"golden set's expected tiers came from production `match_score`s computed
> against this same resume"*

The labels are the model's own earlier outputs. So that number measures
**agreement with its past self** — drift, not correctness. It is a genuinely
useful regression signal and a worthless quality signal, and the two are easy
to confuse because they share a word.

**The anti-inflation rules are not enforced.** All three are prompt text:

```
- If the resume lacks a REQUIRED skill, ATS score cannot exceed 75.
- If the resume has no metrics, HM score cannot exceed 70.
- If fewer than 3 of the top 5 required technologies are present, TR ≤ 75.
```

The only cap applied in code is `apply_geo_score_cap` (work authorisation).
By CLAUDE.md rule 4 the other three are requests — and all three are
mechanically decidable, which is what makes this a defect rather than a
limitation.

---

## 2. The decision: split by what is decidable

Not every lens needs a model. Two of the three are lookups wearing a costume.

| Lens | Becomes | Why |
|---|---|---|
| **ATS** | a computation | keyword presence, section presence and parse fidelity are all decidable from the document |
| **Technical Recruiter** | a taxonomy lookup | "does the résumé cover the JD's required skills" is set coverage once both sides are mapped to a skills vocabulary |
| **Hiring Manager** | stays a judgement | impact, trajectory and narrative are genuinely subjective; nothing public decides them |

This is the whole design. The rest is how.

---

## 3. ATS becomes a measurement

Three parts, all countable, none needing a model:

1. **Keyword coverage.** JD terms present in the résumé text. Already
   approximated by the Studio's coverage panel; promote it to the score.
2. **Structural compatibility.** Required sections present and findable.
3. **Parse fidelity — the part nobody checks today.** Real ATS (Workday,
   Greenhouse, Taleo) read PDFs with extractors close to PyMuPDF/pdfminer.
   So extract *our own* PDF the way they would and assert: every section header
   appears in the extracted text, reading order is not interleaved, dates parse
   as dates, and no content is trapped in a graphic.

That third part is new and belongs beside `shared/page_check.py`, which
already establishes the pattern of measuring the compiled artefact rather than
the source. Proposed: `shared/ats_extract_check.py`, same contract — returns a
list of violations, empty means measured and compliant.

**Open:** the weighting between the three. Proposed start is equal thirds,
revisited once §5 gives outcomes to calibrate against.

---

## 4. Technical Recruiter becomes a taxonomy lookup

Two free, authoritative vocabularies:

- **ESCO** — the EU classification: ~13,900 skills mapped to ~3,000
  occupations, multilingual, downloadable (CSV/RDF) with an API. EU-official,
  which is the right jurisdiction for this user.
- **O*NET** — US Dept of Labor: ~1,000 occupations with skills, tasks and work
  activities, each carrying *importance* and *level* ratings. Useful precisely
  where ESCO is flat — it says which skills matter most for an occupation.

Pipeline: extract requirement phrases from the JD → map to ESCO concepts →
map résumé evidence to the same concepts → coverage is a set operation, and
the gaps are *named concepts* rather than an opinion.

Two properties this buys that no prompt can:

- **Explainability.** "78" becomes "covers 11 of 14 required competences;
  missing: Kubernetes administration, Terraform, incident management."
- **Stability.** The same résumé and JD give the same answer every time, which
  is the fix for the long-standing *Score inconsistency* backlog item.

**Open:** synonym mapping is the hard part (ESCO's `altLabels` help; embedding
similarity against ESCO concept labels is the fallback). An LLM may still be
used to *propose* the mapping, but the mapping is then cached and auditable
rather than re-judged per run — the difference between using a model to build a
lookup table and using it as the lookup table.

---

## 5. Hiring Manager stays a judgement — and gets the only real labels

No public dataset of *(résumé, job, hired?)* exists; privacy and legal
constraints mean one will not appear. So this lens cannot be trained from
online data by anyone, and claiming otherwise would be the same error as
`tier_accuracy`.

The only ground truth available is **the user's own outcomes**, and the column
already exists: `jobs.application_status`. Applied → screened → interviewed →
offer is a real label, generated by using the product.

The question that matters, once a few dozen labels exist, is **do high scores
predict callbacks?** That is answerable with a rank correlation, and it is the
first honest measurement this system would have.

Until then the HM score is presented as a **band, not a number** — the Studio
already established banded scores for exactly this reason (§5.2 of the Studio
design) — and the UI says it is a model's opinion.

---

## 6. What to stop claiming

- Not "three independent perspectives". One call. Either say so, or make them
  three calls from distinct model families, which the council already knows how
  to do.
- Not `tier_accuracy` as quality. Rename it `tier_consistency` in
  `evals/baseline.json` so the next reader cannot misread it. It keeps its job
  as a drift gate.
- Not decimal precision on a judgement. `83.3` claims a resolution nothing
  supports.

---

## 7. Phasing

Ordered by evidence produced per unit of work, not by appeal:

1. **Enforce the anti-inflation rules that already exist.** They are written,
   decidable and ignored. Cheapest possible honesty gain.
2. **`ats_extract_check`.** Self-contained, no external data, and it answers a
   question the product currently guesses at: does our own PDF survive an ATS?
3. **Rename `tier_accuracy` → `tier_consistency`.** One line, removes a
   standing misreading.
4. **Outcome capture.** Make `application_status` transitions easy to record in
   the UI. Labels accumulate from here, so it must start early even though it
   pays late.
5. **ESCO ingestion + TR coverage score.** The largest piece. Deterministic TR.
6. **Calibrate against outcomes.** Only possible once 4 has run for a while.

1–3 are independently shippable within a day each. 5 is a project.

---

## 8. Risks

**ESCO's vocabulary is occupational, not technological.** It is strong on
competences and weaker on fast-moving tool names. Mitigation: ESCO for
competences, a maintained tech-synonym list for tools, and never silently
score a tool the taxonomy does not know — report it as unmapped.

**Deterministic scores will look worse before they look better.** A real
coverage number will be lower and flatter than a model asked to use the full
0–100 range. That is the measurement working, and it must not be "fixed" by
re-inflating it. (CLAUDE.md rule 7: if a check needs a low bar to pass, the
population is usually wrong — here the bar was never real.)

**Outcome labels are sparse and biased.** The user only applies to jobs the
system already scored well, so the data is censored on the predictor. Worth
stating before anyone fits anything to it.
