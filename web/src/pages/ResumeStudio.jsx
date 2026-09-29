import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { apiCall, apiGet } from '../api';
import CoveragePanel from '../components/studio/CoveragePanel';
import PdfPane from '../components/studio/PdfPane';
import ScoreStrip from '../components/studio/ScoreStrip';
import StudioSections from '../components/studio/StudioSections';
import SuggestionsPanel from '../components/studio/SuggestionsPanel';
import { hashSections, useHashedCompile } from '../components/studio/useHashedCompile';

export default function ResumeStudio() {
  const { jobId } = useParams();
  const [job, setJob] = useState(null);
  const [sections, setSections] = useState(null);
  const [sectionsError, setSectionsError] = useState(null);
  const [renderedHashSeed, setRenderedHashSeed] = useState(null);
  // Once the user edits anything, the stored scores describe a document that no
  // longer exists. Phase 1 does not re-score, so this never clears in-session.
  const [hasEdited, setHasEdited] = useState(false);
  const [loading, setLoading] = useState(true);
  // null = never analysed, [] = analysed and had nothing to say. The panel
  // renders those two differently, so they must not collapse into one value.
  const [suggestions, setSuggestions] = useState(null);
  const [suggesting, setSuggesting] = useState(false);
  const [suggestError, setSuggestError] = useState(null);

  useEffect(() => {
    let cancelled = false;
    // No setLoading(true) here: `loading` already starts true, and setting it
    // synchronously in an effect body triggers a cascading render for no gain.
    // The route remounts per jobId, so there is no in-place reload to cover.
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
        const loaded = secResult.sections || null;
        setSections(loaded);
        // The PDF already on the row was compiled from the very .tex that
        // GET .../sections just parsed, so the loaded sections are already
        // rendered. Seeding this stops the first blur recompiling an
        // unedited document for 15s.
        if (loaded && jobRow?.resume_s3_url) {
          setRenderedHashSeed(hashSections(loaded));
        }
      }
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [jobId]);

  // One compile per distinct content state. useHashedCompile keys results by a
  // hash of `sections` and discards any whose hash is no longer current, so two
  // outstanding compiles finishing out of order cannot render the older one.
  const compileSections = useCallback(async (next) => {
    // Refuse to rebuild from nothing. The server REPLACES the stored .tex with
    // one built from this object, so POSTing {} overwrites a real resume with
    // an empty one. sections is null whenever the GET failed — including the
    // transient 500 app.py:2121 returns for any non-NoSuchKey S3 error — and
    // the Recompile button is reachable in exactly that state. Same class of
    // loss that sections_have_content guards on the upload path.
    if (!next || Object.keys(next).length === 0) {
      throw new Error('No sections loaded — nothing to compile');
    }

    // apiCall ALREADY follows a 202 {task_id, poll_url} to completion and
    // returns task.result (api.js:85-87). This used to poll a second time with
    // pollPipeline, which (a) received `undefined` because poll_url is not in
    // what apiCall returns, (b) is the Step Functions poller and switches on
    // SUCCEEDED/FAILED, not the running/done/error this endpoint reports, and
    // (c) returns data.output, not a {status, result} envelope. Every compile
    // 404'd on `<API_BASE>undefined` AFTER the server had already replaced the
    // file, so the PDF never updated and the error was a lie about what
    // happened.
    const res = await apiCall(
      `/api/dashboard/jobs/${jobId}/sections`, { sections: next },
    );
    const url = res?.pdf_url;
    if (!url) throw new Error('Compile finished without a PDF');
    // The scores ride back with the PDF: they were measured against this exact
    // rebuilt .tex inside the same task, so they cannot drift from it.
    return { pdfUrl: url, scores: res?.scores || null };
  }, [jobId]);

  const {
    pdfUrl, result: compileResult, compiling, error: compileError,
    pendingChanges, requestCompile,
  } = useHashedCompile(sections || {}, compileSections, { renderedHashSeed });

  // Analyse the sections in the EDITOR, not the ones on disk. Compilation is
  // blur-triggered, so the editor routinely holds text the stored .tex does
  // not, and a suggestion anchored to the stored version would arrive already
  // stale. Only one analysis at a time — the button is disabled while it runs,
  // which is also what stops two responses racing to set the list.
  const requestSuggestions = useCallback(async () => {
    if (!sections || Object.keys(sections).length === 0) {
      setSuggestError('Nothing to analyse yet — generate a tailored resume first');
      return;
    }
    setSuggesting(true);
    setSuggestError(null);
    try {
      const res = await apiCall(`/api/dashboard/jobs/${jobId}/suggestions`, { sections });
      setSuggestions(res?.suggestions || []);
    } catch (e) {
      setSuggestError(e.message || 'Could not generate suggestions');
    } finally {
      setSuggesting(false);
    }
  }, [jobId, sections]);

  // Applying is an edit like any other: it marks the document changed, which
  // greys the score and raises "N changes not yet compiled". It deliberately
  // does NOT compile — §4 names blur and the Recompile button as the triggers,
  // and 15s per accepted suggestion would punish accepting several.
  const applySuggestedSections = useCallback((next) => {
    setHasEdited(true);
    setSections(next);
  }, []);

  // Prefer scores measured against the document currently on screen over the
  // stored row's, which describe the resume as it was before any edit.
  const liveScores = compileResult?.scores || null;
  const spread = liveScores?.score_spread || null;

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
        {/* Below the lg breakpoint the columns stack, PDF first: on a phone the
            document matters more than the panel. order-* does the reordering. */}
        <div className="space-y-4 order-2 lg:order-1 min-w-0">
          <ScoreStrip
            ats={liveScores?.ats_score ?? job?.ats_score}
            hiringManager={liveScores?.hiring_manager_score ?? job?.hiring_manager_score}
            techRecruiter={liveScores?.tech_recruiter_score ?? job?.tech_recruiter_score}
            band={spread?.match || spread?.ats || null}
            calls={spread?.n ?? null}
            // Stale means "these numbers describe a document that is not the
            // one on screen". A live score was measured against the current
            // rebuild, so it is never stale; the stored row's scores go stale
            // the moment anything is edited.
            stale={!liveScores && (hasEdited || pendingChanges > 0)}
          />
          <CoveragePanel
            keyMatches={job?.key_matches || []}
            gaps={job?.gaps || []}
            requirementMap={job?.requirement_map || []}
          />
          {/* The AI panel is always visible (§3) — but with no document there
              is nothing to suggest against, and a button that can only fail is
              worse than no button. CoveragePanel hides itself on the same
              grounds. */}
          {sections && <SuggestionsPanel
            sections={sections}
            suggestions={suggestions}
            loading={suggesting}
            error={suggestError}
            onRequest={requestSuggestions}
            onApplySections={applySuggestedSections}
          />}
          <StudioSections
            sections={sections}
            onChange={(next) => { setHasEdited(true); setSections(next); }}
            onSectionBlur={requestCompile}
          />
        </div>
        <div className="order-1 lg:order-2 min-w-0">
          <PdfPane
            pdfUrl={pdfUrl || job?.resume_s3_url || null}
            compiling={compiling}
            pendingChanges={pendingChanges}
            error={compileError}
            onRecompile={requestCompile}
          />
        </div>
      </div>
    </div>
  );
}
