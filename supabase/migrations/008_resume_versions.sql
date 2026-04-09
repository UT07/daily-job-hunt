CREATE TABLE IF NOT EXISTS public.resume_versions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  job_id TEXT NOT NULL,
  version_number INT NOT NULL DEFAULT 1,
  resume_s3_url TEXT,
  cover_letter_s3_url TEXT,
  tailoring_model TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_resume_versions_job ON public.resume_versions (job_id, version_number DESC);
ALTER TABLE public.resume_versions ENABLE ROW LEVEL SECURITY;
CREATE POLICY resume_versions_user_policy ON public.resume_versions FOR ALL USING (auth.uid() = user_id);
