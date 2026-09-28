import { useEffect, useState } from 'react';
import { useParams } from 'react-router-dom';
import { apiGet } from '../api';

export default function ResumeStudio() {
  const { jobId } = useParams();
  const [job, setJob] = useState(null);
  const [sections, setSections] = useState(null);
  const [sectionsError, setSectionsError] = useState(null);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    setLoading(true);
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
        setSections(secResult.sections || null);
      }
      setLoading(false);
    });
    return () => { cancelled = true; };
  }, [jobId]);

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
        <div data-testid="studio-left">{sections ? null : null}</div>
        <div data-testid="studio-right" />
      </div>
    </div>
  );
}
