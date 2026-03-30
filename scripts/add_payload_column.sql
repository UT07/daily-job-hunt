-- Add payload column to pipeline_tasks for SQS worker pattern
-- The payload stores the full task input (JD text, resume type, etc.)
-- so the SQS message only needs to carry the task_id
ALTER TABLE pipeline_tasks ADD COLUMN IF NOT EXISTS payload JSONB;
