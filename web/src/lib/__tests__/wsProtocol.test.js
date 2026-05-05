import { describe, it, expect } from 'vitest'
import {
  ACTIONS_OUT,
  ACTIONS_IN,
  parseTextFrame,
  binaryFrameToDataUrl,
} from '../wsProtocol'

describe('wsProtocol', () => {
  it('exports outbound action constants', () => {
    expect(ACTIONS_OUT.FILL_ALL).toBe('fill_all')
    expect(ACTIONS_OUT.CLICK).toBe('click')
    expect(ACTIONS_OUT.TYPE).toBe('type')
    expect(ACTIONS_OUT.SUBMIT).toBe('submit')
    expect(ACTIONS_OUT.END_SESSION).toBe('end_session')
    // Frozen so mistypes blow up loudly
    expect(Object.isFrozen(ACTIONS_OUT)).toBe(true)
  })

  it('exports inbound action constants', () => {
    expect(ACTIONS_IN.STATUS).toBe('status')
    expect(ACTIONS_IN.FIELDS).toBe('fields')
    expect(ACTIONS_IN.FIELD_FILLED).toBe('field_filled')
    expect(Object.isFrozen(ACTIONS_IN)).toBe(true)
  })

  it('parseTextFrame returns parsed JSON for valid frames', () => {
    expect(parseTextFrame('{"action":"status","status":"ready"}')).toEqual({
      action: 'status',
      status: 'ready',
    })
  })

  it('parseTextFrame returns null for malformed JSON', () => {
    expect(parseTextFrame('not json')).toBeNull()
  })

  it('parseTextFrame returns null for frames without action field', () => {
    expect(parseTextFrame('{"status":"ready"}')).toBeNull()
  })

  it('binaryFrameToDataUrl converts ArrayBuffer to image/jpeg data URL', () => {
    // Tiny 3-byte buffer (simulates JPEG SOI marker FF D8 FF)
    const buf = new Uint8Array([0xff, 0xd8, 0xff]).buffer
    const url = binaryFrameToDataUrl(buf)
    expect(url).toMatch(/^data:image\/jpeg;base64,/)
    // base64('\xff\xd8\xff') === '/9j/'
    expect(url).toContain('/9j/')
  })
})
