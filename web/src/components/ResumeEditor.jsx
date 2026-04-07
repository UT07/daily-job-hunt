import { useState, useEffect, useRef } from 'react';
import { Upload, Download } from 'lucide-react';
import Button from './ui/Button';
import SectionEditor from './SectionEditor';
import { apiGet, apiCall, apiUpload } from '../api';

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

export default function ResumeEditor({ job }) {
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

  const [uploading, setUploading] = useState(false);
  const [uploadError, setUploadError] = useState(null);
  const fileInputRef = useRef(null);

  useEffect(() => {
    let cancelled = false;
    async function load() {
      setLoadingData(true);
      setLoadError(null);
      try {
        const data = await apiGet(`/api/dashboard/jobs/${jobId}/sections`);
        if (!cancelled) {
          setSections(data.sections || {});
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
  }, [jobId]);

  function handleSectionChange(key, value) {
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

  async function handleUploadPdf(e) {
    const file = e.target.files?.[0];
    if (!file) return;
    setUploading(true);
    setUploadError(null);
    try {
      const result = await apiUpload('/api/resume/upload-pdf', file);
      const newUrl = result?.pdf_url || result?.resume_s3_url || null;
      if (newUrl) {
        setPdfUrl(newUrl);
        setPdfKey((k) => k + 1);
      }
    } catch (err) {
      setUploadError(err.message);
    } finally {
      setUploading(false);
      // Clear input so same file can be re-uploaded if needed
      if (fileInputRef.current) fileInputRef.current.value = '';
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
              disabled={saving || uploading}
              onClick={handleSaveAndCompile}
            >
              {saving ? 'Compiling...' : 'Save & Compile'}
            </Button>

            <div>
              <input
                type="file"
                accept="application/pdf"
                ref={fileInputRef}
                onChange={handleUploadPdf}
                className="hidden"
                id="pdf-upload-input"
              />
              <Button
                variant="secondary"
                size="md"
                loading={uploading}
                disabled={saving || uploading}
                onClick={() => fileInputRef.current?.click()}
              >
                <Upload size={14} />
                {uploading ? 'Uploading...' : 'Upload PDF'}
              </Button>
            </div>

            {pdfUrl && (
              <a href={pdfUrl} target="_blank" rel="noopener noreferrer" className="ml-auto">
                <Button variant="ghost" size="sm">
                  <Download size={14} />
                  Download
                </Button>
              </a>
            )}
          </div>

          {uploadError && (
            <p className="mt-2 text-xs text-error font-mono">{uploadError}</p>
          )}
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
