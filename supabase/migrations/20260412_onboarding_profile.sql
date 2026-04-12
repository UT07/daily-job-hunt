-- Add onboarding + profile fields for auto-apply prerequisites
ALTER TABLE "public"."users"
  ADD COLUMN IF NOT EXISTS "onboarding_completed_at" timestamp with time zone,
  ADD COLUMN IF NOT EXISTS "salary_expectation_notes" text,
  ADD COLUMN IF NOT EXISTS "notice_period_text" text;
