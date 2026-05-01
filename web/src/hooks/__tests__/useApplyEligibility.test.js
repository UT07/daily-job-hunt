import { describe, it, expect } from 'vitest'
import { computeEligibility } from '../useApplyEligibility'
import enumFile from '../../../../shared/eligibility_reasons.json'

const completeProfile = { profile_complete: true }
const incompleteProfile = { profile_complete: false }
const validJob = {
  apply_url: 'https://boards.greenhouse.io/acme/jobs/123',
  resume_s3_key: 's3://bucket/key.pdf',
  apply_platform: 'greenhouse',
  application_status: 'scored',
}

describe('computeEligibility — order matches app.py:apply_eligibility', () => {
  it('eligible when all gates pass', () => {
    expect(computeEligibility(validJob, completeProfile)).toEqual({
      eligible: true,
      platform: 'greenhouse',
    })
  })

  it('no_apply_url wins over already_applied (job-side gates first)', () => {
    const r = computeEligibility(
      { ...validJob, application_status: 'applied', apply_url: null },
      incompleteProfile,
    )
    expect(r).toEqual({ eligible: false, reason: 'no_apply_url' })
  })

  it('already_applied wins over profile_incomplete (when job is fully apply-ready)', () => {
    const r = computeEligibility(
      { ...validJob, application_status: 'applied' },
      incompleteProfile,
    )
    expect(r).toEqual({ eligible: false, reason: 'already_applied' })
  })

  it('no_apply_url when apply_url missing', () => {
    const r = computeEligibility({ ...validJob, apply_url: null }, completeProfile)
    expect(r).toEqual({ eligible: false, reason: 'no_apply_url' })
  })

  it('no_apply_url when apply_url empty string', () => {
    const r = computeEligibility({ ...validJob, apply_url: '' }, completeProfile)
    expect(r).toEqual({ eligible: false, reason: 'no_apply_url' })
  })

  it('no_resume when resume_s3_key missing', () => {
    const r = computeEligibility({ ...validJob, resume_s3_key: null }, completeProfile)
    expect(r).toEqual({ eligible: false, reason: 'no_resume' })
  })

  it('profile_incomplete when profile.profile_complete=false', () => {
    expect(computeEligibility(validJob, incompleteProfile)).toEqual({
      eligible: false,
      reason: 'profile_incomplete',
    })
  })

  it('eligible:true with platform=null when apply_platform unknown (HN Hiring)', () => {
    const r = computeEligibility({ ...validJob, apply_platform: null }, completeProfile)
    expect(r).toEqual({ eligible: true, platform: null })
  })

  it('eligible:true for any apply_platform (no client-side platform gate)', () => {
    const r = computeEligibility({ ...validJob, apply_platform: 'workday' }, completeProfile)
    expect(r).toEqual({ eligible: true, platform: 'workday' })
  })

  it('returns null-safe defaults when profile is null/undefined', () => {
    const r = computeEligibility(validJob, null)
    expect(r).toEqual({ eligible: false, reason: 'profile_incomplete' })
  })

  it('every returned reason is in shared/eligibility_reasons.json', () => {
    const reasons = enumFile.ineligibility_reasons
    const all = [
      computeEligibility({ ...validJob, application_status: 'applied' }, completeProfile),
      computeEligibility({ ...validJob, apply_url: null }, completeProfile),
      computeEligibility({ ...validJob, resume_s3_key: null }, completeProfile),
      computeEligibility(validJob, incompleteProfile),
    ]
    for (const r of all) {
      expect(reasons).toContain(r.reason)
    }
  })
})
