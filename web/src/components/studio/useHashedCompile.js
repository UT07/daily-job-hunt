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

/**
 * Compile `sections` to a PDF — at most one at a time, latest content wins.
 *
 * @param sections   the structured content to compile
 * @param compileFn  (sections) => Promise<pdfUrl | {pdfUrl, ...extra}>
 * @param options.renderedHashSeed
 *   hashSections(...) of content the caller is ALREADY displaying a PDF for.
 *   The Studio has one: GET .../sections parses the same _tailored.tex the
 *   existing PDF was compiled from, so the two correspond by construction.
 *   Without it the rendered hash stays null, the first blur looks like a
 *   change, and 15s goes on recompiling a document nobody edited.
 *
 *   The CALLER computes it, deliberately. The hook cannot know whether a
 *   pre-existing PDF matches the sections it was handed, and a caller with no
 *   such PDF genuinely does need that first compile.
 *
 * SERIALIZED, not merely de-duplicated. An earlier version allowed concurrent
 * compiles and relied on a hash check to discard a stale RESULT. That kept the
 * on-screen PDF honest but not the stored one: every compile writes the same
 * S3 key (users/{uid}/resumes/{job_id}_tailored.pdf), so two in flight race
 * server-side and whichever finishes last wins on disk — which can be the
 * OLDER document. The client would then show the right PDF while the saved
 * resume was silently wrong. One at a time removes that race at its source.
 *
 * The stale-result check is kept as defence in depth: it costs nothing, and it
 * still fires when content changes while the single in-flight compile runs.
 */
export function useHashedCompile(sections, compileFn, { renderedHashSeed = null } = {}) {
  const [pdfUrl, setPdfUrl] = useState(null);
  const [result, setResult] = useState(null);
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

  // Nothing compiled yet in this session falls back to what the caller says is
  // already on screen.
  const effectiveRenderedHash = renderedHash ?? renderedHashSeed;

  const renderedHashRef = useRef(effectiveRenderedHash);
  useEffect(() => { renderedHashRef.current = effectiveRenderedHash; }, [effectiveRenderedHash]);

  const busy = useRef(false);
  const queued = useRef(false);

  const mounted = useRef(true);
  useEffect(() => () => { mounted.current = false; }, []);

  const compileFnRef = useRef(compileFn);
  useEffect(() => { compileFnRef.current = compileFn; }, [compileFn]);

  const run = useCallback(function run() {
    // Read the hash and the content together, at start time, so the staleness
    // check at the end compares like with like.
    const hash = currentHashRef.current;
    const payload = sectionsRef.current;

    if (hash === renderedHashRef.current) {
      busy.current = false;
      setCompiling(false);
      return;
    }

    busy.current = true;
    setCompiling(true);
    setError(null);

    Promise.resolve(compileFnRef.current(payload))
      .then((res) => {
        if (!mounted.current) return;
        // Content moved on while this ran, so this PDF is already out of date.
        // Showing it would be an invisibly stale pane.
        if (hash !== currentHashRef.current) return;
        // compileFn may resolve a bare url, or an object carrying the url plus
        // whatever else the call produced (the Studio's comes back with freshly
        // measured scores). Both shapes are supported so a simpler caller need
        // not build an envelope it has no use for.
        const url = typeof res === 'string' ? res : res && res.pdfUrl;
        if (!url) { setError('Compile finished without a PDF'); return; }
        setPdfUrl(url);
        setResult(typeof res === 'string' ? null : res);
        setRenderedHash(hash);
      })
      .catch((e) => {
        if (!mounted.current) return;
        // Keep the last good PDF on screen; a stale-but-labelled document is
        // more useful than an empty pane.
        setError(e.message || 'Compile failed');
      })
      .finally(() => {
        busy.current = false;
        if (!mounted.current) return;
        if (queued.current) {
          // Run the LATEST content, not whatever was current when the request
          // was queued — intermediate states were superseded before they ever
          // reached the server, and compiling them would waste 15s each.
          queued.current = false;
          run();
        } else {
          setCompiling(false);
        }
      });
  }, []);

  const requestCompile = useCallback(() => {
    if (currentHashRef.current === renderedHashRef.current) return;
    if (busy.current) { queued.current = true; return; }
    run();
  }, [run]);

  return {
    pdfUrl,
    result,
    compiling,
    error,
    pendingChanges:
      effectiveRenderedHash !== null && effectiveRenderedHash !== currentHash ? 1 : 0,
    requestCompile,
  };
}
