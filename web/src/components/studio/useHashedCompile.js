import { useCallback, useEffect, useRef, useState } from 'react';

/**
 * Stable content hash for a sections object.
 *
 * JSON.stringify is key-order dependent, and React state updates can produce
 * the same content with different key order, which would look like a change
 * and trigger a needless 15s compile. Sorting the keys makes the hash a
 * function of content alone.
 */
export function hashSections(sections) {
  const canonical = JSON.stringify(sections, (_key, value) =>
    value && typeof value === 'object' && !Array.isArray(value)
      ? Object.keys(value).sort().reduce((acc, k) => { acc[k] = value[k]; return acc; }, {})
      : value,
  );
  // djb2. Not cryptographic — this only needs to distinguish edits from
  // non-edits within one browser session.
  let h = 5381;
  for (let i = 0; i < canonical.length; i += 1) h = ((h << 5) + h + canonical.charCodeAt(i)) | 0;
  return String(h);
}

export function useHashedCompile(sections, compileFn) {
  const [pdfUrl, setPdfUrl] = useState(null);
  const [compiling, setCompiling] = useState(false);
  const [error, setError] = useState(null);
  const [renderedHash, setRenderedHash] = useState(null);

  const currentHash = hashSections(sections);

  // The completion handler needs the hash as it is WHEN THE RESULT ARRIVES,
  // not as it was when the closure was created. A ref is the only thing that
  // sees the newer value from inside an already-scheduled promise callback.
  const currentHashRef = useRef(currentHash);
  useEffect(() => { currentHashRef.current = currentHash; }, [currentHash]);

  const sectionsRef = useRef(sections);
  useEffect(() => { sectionsRef.current = sections; }, [sections]);

  const renderedHashRef = useRef(renderedHash);
  useEffect(() => { renderedHashRef.current = renderedHash; }, [renderedHash]);

  const inFlight = useRef(new Set());

  const requestCompile = useCallback(() => {
    const hash = currentHashRef.current;
    if (hash === renderedHashRef.current) return; // nothing changed since the displayed PDF
    if (inFlight.current.has(hash)) return;       // already queued for this exact content
    inFlight.current.add(hash);
    setCompiling(true);
    setError(null);

    Promise.resolve(compileFn(sectionsRef.current))
      .then((url) => {
        // THE RULE: a result for content that is no longer current is dropped,
        // never displayed. Without this, an out-of-order completion renders an
        // older document with nothing on screen saying so.
        if (hash !== currentHashRef.current) return;
        setPdfUrl(url);
        setRenderedHash(hash);
      })
      .catch((e) => {
        if (hash !== currentHashRef.current) return;
        // Keep the last good PDF on screen; a stale-but-labelled document is
        // more useful than an empty pane.
        setError(e.message || 'Compile failed');
      })
      .finally(() => {
        inFlight.current.delete(hash);
        if (inFlight.current.size === 0) setCompiling(false);
      });
  }, [compileFn]);

  return {
    pdfUrl,
    compiling,
    error,
    pendingChanges: renderedHash !== null && renderedHash !== currentHash ? 1 : 0,
    requestCompile,
  };
}
