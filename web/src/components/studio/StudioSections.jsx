const FIELD = 'w-full text-sm p-2 border-2 border-stone-200 focus:border-stone-400 outline-none';
const LABEL = 'block text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-1';

/**
 * The structured section editor. Sections are the source of truth.
 *
 * LaTeX is derived from this object server-side and spliced into a preamble
 * the user never sees. Two things fall out of that, and both are the reason
 * the Studio edits sections rather than markup:
 *
 *   - One escaping site. A stray &, %, _ or # in prose is escaped once, where
 *     sections become LaTeX, instead of in every edit path.
 *   - The AI never emits LaTeX, which removes the whole class of failure the
 *     preamble-protection code exists to prevent — a model rewriting a
 *     \newcommand, dropping a \usepackage, or calling a 3-argument macro
 *     with 2.
 */
export default function StudioSections({ sections, onChange, onSectionBlur }) {
  if (!sections) {
    return (
      <div role="status" className="p-3 border-2 border-stone-200 text-sm text-stone-500">
        Nothing to edit yet — generate a tailored resume first.
      </div>
    );
  }

  // Every edit produces a whole new sections object. Structural sharing is not
  // worth the bug surface here: the object is small, and a single owner of the
  // full shape is what makes the hash in useHashedCompile meaningful.
  const set = (patch) => onChange({ ...sections, ...patch });

  // parse_resume_sections returns [] for a section it could not find, and a
  // resume with no projects is ordinary — so never assume presence.
  const skills = sections.skills || [];
  const experience = sections.experience || [];

  const setExperienceBullet = (ei, bi, value) => {
    set({
      experience: experience.map((entry, i) =>
        i !== ei ? entry : { ...entry, bullets: entry.bullets.map((b, j) => (j === bi ? value : b)) },
      ),
    });
  };

  const setSkill = (i, value) => {
    set({ skills: skills.map((s, j) => (j === i ? { ...s, items: value } : s)) });
  };

  return (
    <div className="space-y-4">
      <div>
        <label htmlFor="studio-summary" className={LABEL}>Summary</label>
        <textarea
          id="studio-summary"
          className={FIELD}
          rows={3}
          value={sections.summary || ''}
          onChange={(e) => set({ summary: e.target.value })}
          onBlur={onSectionBlur}
        />
      </div>

      {skills.length > 0 && (
        <div>
          <span className={LABEL}>Skills</span>
          {skills.map((s, i) => (
            <div key={s.category || i} className="mb-2">
              <label htmlFor={`skill-${i}`} className="text-xs text-stone-500">{s.category}</label>
              <input
                id={`skill-${i}`}
                className={FIELD}
                value={s.items || ''}
                onChange={(e) => setSkill(i, e.target.value)}
                onBlur={onSectionBlur}
              />
            </div>
          ))}
        </div>
      )}

      {experience.map((entry, ei) => (
        <div key={`${entry.company}-${ei}`}>
          <span className={LABEL}>{entry.company} — {entry.title}</span>
          {(entry.bullets || []).map((b, bi) => (
            // Keyed by index deliberately. Bullets are a plain string array
            // with no id, so position IS the identity. Keying on the text
            // would remount the input on every keystroke and lose focus.
            <input
              key={bi}
              aria-label={`${entry.company} bullet ${bi + 1}`}
              className={`${FIELD} mb-1`}
              value={b}
              onChange={(e) => setExperienceBullet(ei, bi, e.target.value)}
              onBlur={onSectionBlur}
            />
          ))}
        </div>
      ))}
    </div>
  );
}
