/**
 * Anchoring, staleness and undo for Studio suggestions (spec §5.3).
 *
 * A suggestion is generated against one version of the document and applied
 * against another; in between, the user is typing. The anchor is two things:
 *
 *   path         where it was  — ["experience", 0, "bullets", 2]
 *   anchor_text  what was there — the verbatim source string
 *
 * Neither alone is enough. `path` still resolves after the bullet above it is
 * deleted, but to a different sentence — applying there overwrites text the
 * suggestion never saw. `anchor_text` alone says what but not where, and the
 * same words can occur twice.
 *
 * The spec calls for "a hash of the source text". This stores the source text
 * itself instead. It is a few hundred bytes, the panel has to display it
 * anyway ("here is what you wrote"), and one string comparison beats two hash
 * implementations — one in Python, one in JavaScript — that must agree with
 * each other forever or suggestions silently stop resolving.
 *
 * Resolution has three outcomes and only the last disables Apply:
 *
 *   exact  the text is still at the path
 *   moved  the text is intact but its index shifted — apply THERE
 *   stale  the text is gone or rewritten — grey out, offer re-analyse
 *
 * The re-anchor search varies ONLY the last indexed step of the path: the same
 * bullet list, or the same skills list. It deliberately does not search the
 * document, or even the whole section. A bullet found under a different
 * employer is not the same bullet — the suggestion's label would name the
 * wrong company, and the advice was written about that employer's work.
 * Insertion, deletion and reordering within one list are the common edits;
 * everything else is worth a re-analyse.
 *
 * Undo is the same operation with the two texts swapped. That is why it needs
 * no round trip to the model, and why it refuses under exactly the same
 * condition Apply does: if you edited the applied text afterwards, reverting
 * would throw away your words.
 */

/** The string at `path`, or undefined. Non-strings (a list, an object) are not text. */
export function getAtPath(sections, path) {
  let node = sections;
  for (const step of path || []) {
    if (node === null || node === undefined) return undefined;
    if (typeof step === 'number') {
      if (!Array.isArray(node) || step < 0 || step >= node.length) return undefined;
    } else if (typeof node !== 'object' || Array.isArray(node)) {
      return undefined;
    }
    node = node[step];
  }
  return typeof node === 'string' ? node : undefined;
}

/** Like getAtPath but returns containers too — used to find a list's siblings. */
function nodeAtPath(sections, path) {
  let node = sections;
  for (const step of path) {
    if (node === null || typeof node !== 'object') return undefined;
    node = node[step];
  }
  return node;
}

/** A copy of `sections` with `value` written at `path`. The input is untouched. */
function setAtPath(node, path, value) {
  const [head, ...rest] = path;
  const child = rest.length === 0 ? value : setAtPath(node[head], rest, value);
  if (Array.isArray(node)) {
    const copy = node.slice();
    copy[head] = child;
    return copy;
  }
  return { ...node, [head]: child };
}

/**
 * The same path with the last numeric step varied across its enclosing list.
 *
 * ["experience", 0, "bullets", 2] -> every bullet of THAT employer.
 * ["skills", 1, "items"]          -> the items line of every skills entry.
 * ["summary"]                     -> nothing; there is no list to move within.
 */
function siblingPaths(sections, path) {
  let pivot = -1;
  for (let i = 0; i < path.length; i += 1) if (typeof path[i] === 'number') pivot = i;
  if (pivot < 0) return [];

  const container = nodeAtPath(sections, path.slice(0, pivot));
  if (!Array.isArray(container)) return [];

  const head = path.slice(0, pivot);
  const tail = path.slice(pivot + 1);
  return container.map((_, i) => [...head, i, ...tail]);
}

/**
 * Where this suggestion applies in the CURRENT document.
 *
 * @returns {{status: 'exact'|'moved'|'stale', path: Array|null}}
 */
export function resolveAnchor(sections, suggestion) {
  const path = suggestion?.path;
  const anchor = suggestion?.anchor_text;
  const STALE = { status: 'stale', path: null };
  if (!Array.isArray(path) || path.length === 0 || typeof anchor !== 'string') return STALE;

  if (getAtPath(sections, path) === anchor) return { status: 'exact', path };

  // First match in list order. Two identical bullets in one list are already
  // a duplicate the user will want to fix; rewriting either is the same edit.
  for (const candidate of siblingPaths(sections, path)) {
    if (getAtPath(sections, candidate) === anchor) return { status: 'moved', path: candidate };
  }
  return STALE;
}

/**
 * Apply a suggestion, or return null when its anchor no longer matches.
 *
 * @returns {{sections: object, undo: object}|null} `undo` is itself an
 *   appliable suggestion — pass it back through this function to revert.
 */
export function applySuggestion(sections, suggestion) {
  if (typeof suggestion?.replacement !== 'string') return null;
  const { status, path } = resolveAnchor(sections, suggestion);
  if (status === 'stale') return null;

  return {
    sections: setAtPath(sections, path, suggestion.replacement),
    undo: {
      id: suggestion.id,
      label: suggestion.label,
      path,
      anchor_text: suggestion.replacement,
      replacement: suggestion.anchor_text,
    },
  };
}
