// Order matches app.py:apply_eligibility exactly so frontend and backend never
// disagree on which reason fires when multiple apply.
export function computeEligibility(job, profile) {
  if (!job.apply_url)                        return { eligible: false, reason: 'no_apply_url' }
  if (!job.resume_s3_key)                    return { eligible: false, reason: 'no_resume' }
  // Backend writes "Applied" (Title Case) in app.py:apply_record(). Lower-case here so we
  // never miss a status due to casing drift — tests below pin both shapes.
  if ((job.application_status || '').toLowerCase() === 'applied') return { eligible: false, reason: 'already_applied' }
  if (!profile || !profile.profile_complete) return { eligible: false, reason: 'profile_incomplete' }
  return { eligible: true, platform: job.apply_platform || null }
}
