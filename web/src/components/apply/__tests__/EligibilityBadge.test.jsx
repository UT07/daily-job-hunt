import { describe, it, expect } from 'vitest'
import { render } from '@testing-library/react'
import { EligibilityBadge } from '../EligibilityBadge'

describe('EligibilityBadge', () => {
  it('renders green with "Smart Apply available" tooltip when eligible', () => {
    const { container } = render(<EligibilityBadge eligible reason={null} platform="greenhouse" />)
    const badge = container.querySelector('[data-testid="eligibility-badge"]')
    expect(badge).toHaveAttribute('data-state', 'eligible')
    expect(badge).toHaveAttribute('title', expect.stringMatching(/Smart Apply available/i))
  })

  it('renders amber with "Profile incomplete" tooltip', () => {
    const { container } = render(<EligibilityBadge eligible={false} reason="profile_incomplete" />)
    const badge = container.querySelector('[data-testid="eligibility-badge"]')
    expect(badge).toHaveAttribute('data-state', 'recoverable')
    expect(badge).toHaveAttribute('title', expect.stringMatching(/Profile incomplete/i))
  })

  it('renders amber with "No tailored resume yet" tooltip', () => {
    const { container } = render(<EligibilityBadge eligible={false} reason="no_resume" />)
    expect(container.querySelector('[data-testid="eligibility-badge"]'))
      .toHaveAttribute('title', expect.stringMatching(/No tailored resume/i))
  })

  it('renders amber with "No apply URL" tooltip', () => {
    const { container } = render(<EligibilityBadge eligible={false} reason="no_apply_url" />)
    expect(container.querySelector('[data-testid="eligibility-badge"]'))
      .toHaveAttribute('title', expect.stringMatching(/No apply URL/i))
  })

  it('renders grey with "Already applied" tooltip', () => {
    const { container } = render(<EligibilityBadge eligible={false} reason="already_applied" />)
    const badge = container.querySelector('[data-testid="eligibility-badge"]')
    expect(badge).toHaveAttribute('data-state', 'terminal')
    expect(badge).toHaveAttribute('title', expect.stringMatching(/Already applied/i))
  })
})
