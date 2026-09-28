import { useCallback, useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { apiCall, apiGet, pollPipeline } from '../api';
import CoveragePanel from '../components/studio/CoveragePanel';
import PdfPane from '../components/studio/PdfPane';
import ScoreStrip from '../components/studio/ScoreStrip';
import StudioSections from '../components/studio/StudioSections';
import { hashSections, useHashedCompile } from '../components/studio/useHashedCompile';

export default function ResumeStudio() {
  const { jobId } = useParams();
  const [job, setJob] = useState(null);
  const [sections, setSections] = useState(null);
  const [sectionsError, setSectionsError] = useState(null);
  const [renderedHashSeed, setRenderedHashSeed] = useState(null);
  const [loading, setLoading] = useState(true);

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
    const { poll_url: pollUrl } = await apiCall(
      `/api/dashboard/jobs/${jobId}/sections`, { sections: next },
    );
    const done = await pollPipeline(pollUrl, { intervalMs: 2000, maxWaitMs: 120000 });
    const url = done?.result?.pdf_url;
    if (!url) throw new Error(done?.error || 'Compile finished without a PDF');
    return url;
  }, [jobId]);

  const {
    pdfUrl, compiling, error: compileError, pendingChanges, requestCompile,
  } = useHashedCompile(sections || {}, compileSections, { renderedHashSeed });

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
        <div className="space-y-4 order-2 lg:order-1">
          <ScoreStrip
            ats={job?.ats_score}
            hiringManager={job?.hiring_manager_score}
            techRecruiter={job?.tech_recruiter_score}
            stale={pendingChanges > 0}
          />
          <CoveragePanel
            keyMatches={job?.key_matches || []}
            gaps={job?.gaps || []}
            requirementMap={job?.requirement_map || []}
          />
          <StudioSections
            sections={sections}
            onChange={setSections}
            onSectionBlur={requestCompile}
          />
        </div>
        <div className="order-1 lg:order-2">
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
