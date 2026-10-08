/**
 * Browser storage that belongs to a signed-in user.
 *
 * GDPR consent used to live under one global localStorage key,
 * `gdpr_consent`, that sign-out never cleared. On a shared machine the next
 * person to sign in inherited the previous user's "consented" and never saw
 * the banner -- and the key was written even when the server never recorded
 * consent. Consent is now keyed by user id and only written after the server
 * accepted it; sign-out clears every user-scoped key.
 *
 * The Add Job draft (sessionStorage) is included: a pasted job description is
 * the previous user's data too.
 */
const CONSENT_PREFIX = 'gdpr_consent:';
const LEGACY_CONSENT_KEY = 'gdpr_consent';
export const ADDJOB_DRAFT_KEY = 'naukribaba_addjob_draft';

export function consentKey(userId) {
  return `${CONSENT_PREFIX}${userId}`;
}

export function hasLocalConsent(userId) {
  if (!userId) return false;
  try {
    return localStorage.getItem(consentKey(userId)) === 'true';
  } catch {
    return false;
  }
}

export function setLocalConsent(userId) {
  if (!userId) return;
  try {
    localStorage.setItem(consentKey(userId), 'true');
  } catch { /* storage disabled: the server record is the source of truth */ }
}

export function clearLocalConsent(userId) {
  try {
    if (userId) localStorage.removeItem(consentKey(userId));
    localStorage.removeItem(LEGACY_CONSENT_KEY);
  } catch { /* storage disabled */ }
}

/** Remove everything user-scoped from this browser. Called on sign-out. */
export function clearUserScopedStorage() {
  try {
    const doomed = [];
    for (let i = 0; i < localStorage.length; i++) {
      const k = localStorage.key(i);
      if (k && (k.startsWith(CONSENT_PREFIX) || k === LEGACY_CONSENT_KEY)) doomed.push(k);
    }
    doomed.forEach((k) => localStorage.removeItem(k));
  } catch { /* storage disabled */ }
  try {
    sessionStorage.removeItem(ADDJOB_DRAFT_KEY);
  } catch { /* storage disabled */ }
}
