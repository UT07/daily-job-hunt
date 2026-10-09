import { useState, useEffect, useRef } from 'react';
import { Download } from 'lucide-react';
import Button from './ui/Button';
import SectionEditor from './SectionEditor';
import { apiGet, apiCall } from '../api';

const SECTION_LABELS = {
  summary: 'Summary',
  skills: 'Skills',
  experience: 'Experience',
  projects: 'Projects',
  education: 'Education',
  certifications: 'Certifications',
};

// Display order for sections in the editor
const SECTION_ORDER = ['summary', 'skills', 'experience', 'projects', 'education', 'certifications'];

export default function ResumeEditor({ job, onGenerateResume, generating }) {
  const jobId = job.job_id;

  const [sections, setSections] = useState(null);
  const [jdAnalysis, setJdAnalysis] = useState(null);
  const [loadError, setLoadError] = useState(null);
  const [loadingData, setLoadingData] = useState(true);

  const [saving, setSaving] = useState(false);
  const [saveError, setSaveError] = useState(null);
  const [saveSuccess, setSaveSuccess] = useState(false);

  const [pdfUrl, setPdfUrl] = useState(job.resume_s3_url || null);
  const [pdfKey, setPdfKey] = useState(0); // increment to force iframe refresh

  // No "Upload PDF" here. It posted to /api/resumes/upload, which replaces the
  // user's BASE résumé (resume_key "default") and overwrites profile fields
  // from it -- and no endpoint replaces a job's tailored PDF. Base-résumé
  // upload lives in Settings and Onboarding.

  // Why a ref and not state: the loader below re-runs whenever
  // `job.resume_s3_url` changes, and EVERY save changes it -- the backend mints
  // a fresh presigned URL for the same key, so the value differs even when the
  // document does not. Without this flag the loader overwrites `sections` with
  // the server's copy and silently discards whatever has been typed since.
  // Same defect class as the three hydration races in Settings.jsx (#171,
  // #176): a refetch racing the user's own edits.
  const sectionsDirty = useRef(false);

  // Self-contained reset, during render -- React's documented pattern for
  // "adjust state when a prop changes". JobWorkspace also passes key={jobId},
  // which remounts and makes this unreachable there; it stays because a
  // component should be correct for every caller, not only for the one that
  // remembered the key. In render rather than in an effect because what decides
  // the outcome is the flag's value when the in-flight fetch RESOLVES, not when
  // an effect fired, and only a render-pass reset closes that window.
  const [renderedJobId, setRenderedJobId] = useState(jobId);
  if (jobId !== renderedJobId) {
    setRenderedJobId(jobId);
    sectionsDirty.current = false;
    setSections(null);
  }

  useEffect(() => {
    let cancelled = false;
    async function load() {
      // No tailored resume yet — GET .../sections predictably 404s ("No
      // tailored .tex found for job {id}. Run tailoring first.", app.py).
      // Skip the round-trip and show the same friendly empty state as the
      // Resume tab, instead of a raw red error banner for what is currently
      // an everyday state, not an edge case (audit P1-4).
      if (!job.resume_s3_url) {
        setSections(null);
        setLoadError(null);
        setLoadingData(false);
        return;
      }
      setLoadingData(true);
      setLoadError(null);
      try {
        const data = await apiGet(`/api/dashboard/jobs/${jobId}/sections`);
        if (!cancelled) {
          // Unsaved edits win. The JD analysis is server-derived and carries no
          // user input, so it always refreshes.
          if (!sectionsDirty.current) setSections(data.sections || {});
          setJdAnalysis(data.jd_analysis || null);
        }
      } catch (err) {
        if (!cancelled) setLoadError(err.message);
      } finally {
        if (!cancelled) setLoadingData(false);
      }
    }
    load();
    return () => { cancelled = true; };
  }, [jobId, job.resume_s3_url]);


  // `pdfUrl` seeds from the prop once via useState, so it never saw a later
  // change to it: a regenerate or re-tailor elsewhere in the page updated
  // `job.resume_s3_url` and the preview kept rendering the previous document.
  // Not folded into the loader above, because that one returns early when
  // there is no resume yet and would skip this.
  useEffect(() => {
    if (job.resume_s3_url) {
      setPdfUrl(job.resume_s3_url);
      setPdfKey((k) => k + 1);
    }
  }, [job.resume_s3_url]);

  function handleSectionChange(key, value) {
    sectionsDirty.current = true;
    setSections((prev) => ({ ...prev, [key]: value }));
    setSaveSuccess(false);
    setSaveError(null);
  }

  async function handleSaveAndCompile() {
    setSaving(true);
    setSaveError(null);
    setSaveSuccess(false);
    try {
      const result = await apiCall(`/api/dashboard/jobs/${jobId}/sections`, { sections });
      // Saved: the server's copy and the editor's now agree, so a later reload
      // is no longer a race and is allowed to refresh from it.
      sectionsDirty.current = false;
      setSaveSuccess(true);
      // If backend returns updated PDF URL, use it
      const newUrl = result?.resume_s3_url || result?.pdf_url || null;
      if (newUrl) {
        setPdfUrl(newUrl);
        setPdfKey((k) => k + 1);
      }
    } catch (err) {
      setSaveError(err.message);
    } finally {
      setSaving(false);
    }
  }

  const sectionAnalysis = (key) =>
    jdAnalysis?.sections?.[key] || null;

  if (loadingData) {
    return (
      <div className="flex items-center gap-3 py-12 justify-center">
        <span className="spinner" />
        <span className="text-sm text-stone-400 font-mono">Loading sections...</span>
      </div>
    );
  }

  if (!job.resume_s3_url) {
    return (
      <div className="text-center py-16">
        <svg className="w-16 h-16 mx-auto mb-4 text-stone-300" fill="none" viewBox="0 0 24 24" stroke="currentColor" strokeWidth={1}>
          <path strokeLinecap="round" strokeLinejoin="round" d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z" />
        </svg>
        <p className="text-stone-400 font-heading font-bold">No resume generated yet</p>
        <p className="text-xs text-stone-400 mt-1 mb-4">Generate a tailored resume before editing its sections.</p>
        {onGenerateResume && (
          <Button variant="accent" size="sm" loading={!!generating} disabled={!!generating} onClick={onGenerateResume}>
            {generating ? 'Generating...' : 'Generate Resume'}
          </Button>
        )}
      </div>
    );
  }

  if (loadError) {
    return (
      <div className="border-2 border-error bg-error-light p-4">
        <p className="text-sm font-bold text-error font-mono">Failed to load sections: {loadError}</p>
      </div>
    );
  }

  return (
    <div className="grid grid-cols-[60fr_40fr] gap-0 min-h-[700px] border-2 border-black -m-6">
      {/* ---- Left pane: section editors ---- */}
      <div className="border-r-2 border-black overflow-y-auto" style={{ maxHeight: '80vh' }}>
        {/* Pane header */}
        <div className="sticky top-0 z-10 bg-black text-cream px-4 py-3 flex items-center justify-between border-b-2 border-black">
          <span className="text-xs font-bold uppercase tracking-wider font-heading">Edit Sections</span>
          {jdAnalysis?.jd_keywords?.length > 0 && (
            <span className="text-[10px] font-mono text-stone-400">
              {jdAnalysis.jd_keywords.length} JD keywords tracked
            </span>
          )}
        </div>

        <div className="p-4">
          {SECTION_ORDER.map((key) => {
            if (!(key in (sections || {}))) return null;
            return (
              <SectionEditor
                key={key}
                jobId={jobId}
                sectionKey={key}
                label={SECTION_LABELS[key] || key}
                value={sections[key]}
                analysis={sectionAnalysis(key)}
                onChange={(val) => handleSectionChange(key, val)}
              />
            );
          })}

          {/* Save status */}
          {saveSuccess && (
            <div className="mb-3 p-3 border-2 border-success bg-success-light">
              <p className="text-xs font-bold text-success font-mono">Saved and compiled successfully.</p>
            </div>
          )}
          {saveError && (
            <div className="mb-3 p-3 border-2 border-error bg-error-light">
              <p className="text-xs font-bold text-error font-mono">Error: {saveError}</p>
            </div>
          )}

          {/* Action buttons */}
          <div className="flex items-center gap-3 pt-2 border-t-2 border-black">
            <Button
              variant="accent"
              size="md"
              loading={saving}
              disabled={saving}
              onClick={handleSaveAndCompile}
            >
              {saving ? 'Compiling...' : 'Save & Compile'}
            </Button>

            {pdfUrl && (
              <a href={pdfUrl} target="_blank" rel="noopener noreferrer" className="ml-auto">
                <Button variant="ghost" size="sm">
                  <Download size={14} />
                  Download
                </Button>
              </a>
            )}
          </div>

        </div>
      </div>

      {/* ---- Right pane: PDF preview ---- */}
      <div className="flex flex-col">
        <div className="bg-stone-100 px-4 py-3 border-b-2 border-black flex items-center justify-between">
          <span className="text-xs font-bold uppercase tracking-wider text-stone-500 font-heading">PDF Preview</span>
          {saving && (
            <div className="flex items-center gap-2">
              <span className="spinner" style={{ width: 14, height: 14, borderWidth: 2 }} />
              <span className="text-[10px] font-mono text-stone-400">Compiling PDF...</span>
            </div>
          )}
        </div>
        <div className="flex-1 bg-stone-200">
          {pdfUrl ? (
            <iframe
              key={pdfKey}
              src={pdfUrl}
              title="Resume PDF Preview"
              className="w-full h-full bg-white"
              style={{ minHeight: '650px', border: 'none' }}
            />
          ) : (
            <div className="flex flex-col items-center justify-center h-full py-20 text-center px-6">
              <svg
                className="w-14 h-14 mb-4 text-stone-300"
                fill="none"
                viewBox="0 0 24 24"
                stroke="currentColor"
                strokeWidth={1}
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  d="M9 12h6m-6 4h6m2 5H7a2 2 0 01-2-2V5a2 2 0 012-2h5.586a1 1 0 01.707.293l5.414 5.414a1 1 0 01.293.707V19a2 2 0 01-2 2z"
                />
              </svg>
              <p className="text-stone-400 font-heading font-bold text-sm">No PDF yet</p>
              <p className="text-xs text-stone-400 mt-1 font-mono">
                Edit sections and click "Save &amp; Compile" to generate your resume.
              </p>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
