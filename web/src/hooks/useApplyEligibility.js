// Order matches app.py:2683-2734 exactly so frontend and backend never disagree
// on which reason fires when multiple apply.
export function computeEligibility(job, profile) {
  if (job.application_status === 'applied') {
    return { eligible: false, reason: 'already_applied' }
  }
  if (!job.apply_url) {
    return { eligible: false, reason: 'no_apply_url' }
  }
  if (!job.resume_s3_key) {
    return { eligible: false, reason: 'no_resume' }
  }
  if (!profile || !profile.profile_complete) {
    return { eligible: false, reason: 'profile_incomplete' }
  }
  return { eligible: true, platform: job.apply_platform || null }
}
