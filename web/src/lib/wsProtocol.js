/**
 * WebSocket message protocol for Smart Apply Plan 3c.full.
 *
 * Inbound (from Fargate browser via API Gateway Management API):
 *   - JSON text frames: {action: 'status'|'fields'|'field_filled', ...}
 *   - Binary frames: raw JPEG bytes, ~5-10 KB, 5-10 fps
 *
 * Outbound (to Fargate via $default API Gateway route):
 *   - JSON text frames only. {action: 'fill_all'|'click'|'type'|'submit'|...}
 */

export const ACTIONS_OUT = Object.freeze({
  FILL_ALL: 'fill_all',
  FILL_FIELD: 'fill_field',
  CLICK: 'click',
  TYPE: 'type',
  KEY: 'key',
  NAVIGATE: 'navigate',
  SCROLL: 'scroll',
  SUBMIT: 'submit',
  END_SESSION: 'end_session',
})

export const ACTIONS_IN = Object.freeze({
  STATUS: 'status',
  FIELDS: 'fields',
  FIELD_FILLED: 'field_filled',
})

/**
 * Parse a text frame. Returns null on malformed JSON or missing `action`.
 * Caller is responsible for switching on `parsed.action`.
 */
export function parseTextFrame(text) {
  let parsed
  try {
    parsed = JSON.parse(text)
  } catch {
    return null
  }
  if (!parsed || typeof parsed.action !== 'string') return null
  return parsed
}

/**
 * Convert an ArrayBuffer of JPEG bytes into a data: URL suitable for <img src>.
 * Browsers handle ~50 KB data URLs cleanly; Fargate caps frames at <128 KB
 * (API Gateway Management API limit) so this is always safe.
 */
export function binaryFrameToDataUrl(arrayBuffer) {
  const bytes = new Uint8Array(arrayBuffer)
  let binary = ''
  // String.fromCharCode(...bytes) blows the call stack on >65k items;
  // chunk the conversion to stay safe.
  const CHUNK = 0x8000
  for (let i = 0; i < bytes.length; i += CHUNK) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + CHUNK))
  }
  return `data:image/jpeg;base64,${btoa(binary)}`
}
