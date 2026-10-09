import { useState } from 'react'
import Input from './Input'

// Common notice periods for IE/UK/EU job market.
// Order: shortest → longest, with "Custom..." last.
export const NOTICE_PERIOD_PRESETS = [
  'Available immediately',
  '1 week',
  '2 weeks',
  '1 month',
  '2 months',
  '3 months',
]

const CUSTOM = '__custom__'

/**
 * Notice-period input with a dropdown of common values and a "Custom..."
 * option that reveals a free-text field. Persists to a single string field
 * (`notice_period_text`) so backend `check_profile_completeness` keeps working.
 *
 * Props:
 *   value:     current string ('' | one of NOTICE_PERIOD_PRESETS | custom string)
 *   onChange:  (newValue: string) => void
 *   label:     optional label override (default: "Notice Period")
 */
export function NoticePeriodPicker({ value, onChange, label = 'Notice Period' }) {
  // The select's value is DERIVED from `value` on every render, not captured
  // once. Settings and Onboarding mount this with '' and fill it in when
  // GET /api/profile resolves; a mode computed in useState's initialiser
  // never saw that, so a stored "1 month" displayed as "Select…" (live E2E,
  // 2026-10-09). The only thing kept locally is the one fact `value` cannot
  // express: the user picked "Custom…" and the text is empty, or happens to
  // equal a preset while they type.
  const [customChosen, setCustomChosen] = useState(false)
  const isCustomValue = !!value && !NOTICE_PERIOD_PRESETS.includes(value)
  const mode = customChosen || isCustomValue ? CUSTOM : (value || '')

  function handleSelect(e) {
    const next = e.target.value
    setCustomChosen(next === CUSTOM)
    if (next === CUSTOM) {
      // Switching to custom: clear so user types fresh; preserve current
      // value in case they cancel by re-selecting a preset.
      onChange('')
    } else {
      onChange(next)
    }
  }

  return (
    <div>
      <label className="block text-sm font-bold mb-2">{label}</label>
      <select
        value={mode}
        onChange={handleSelect}
        data-testid="notice-period-select"
        className="w-full border-2 border-black px-3 py-2 font-mono text-sm bg-white"
      >
        <option value="">Select…</option>
        {NOTICE_PERIOD_PRESETS.map((p) => (
          <option key={p} value={p}>{p}</option>
        ))}
        <option value={CUSTOM}>Custom…</option>
      </select>

      {mode === CUSTOM && (
        <div className="mt-2">
          <Input
            value={value || ''}
            onChange={(e) => onChange(e.target.value)}
            placeholder="e.g. 4 weeks, flexible, end of quarter…"
            data-testid="notice-period-custom-input"
          />
        </div>
      )}
    </div>
  )
}
