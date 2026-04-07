CREATE TABLE IF NOT EXISTS public.application_timeline (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  user_id UUID NOT NULL,
  job_id UUID NOT NULL,
  status TEXT NOT NULL,
  notes TEXT,
  created_at TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX idx_timeline_job ON public.application_timeline (job_id, created_at DESC);
ALTER TABLE public.application_timeline ENABLE ROW LEVEL SECURITY;
CREATE POLICY timeline_user_policy ON public.application_timeline FOR ALL USING (auth.uid() = user_id);
