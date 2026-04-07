import { useState } from 'react';
import { ChevronDown, ChevronRight, Sparkles, Check, X, Pencil } from 'lucide-react';
import Button from './ui/Button';
import { apiCall } from '../api';

// ---- JD Analysis Bar ----
function JdAnalysisBar({ analysis }) {
  if (!analysis) return null;

  const { keywords_matched = [], keywords_missing = [], coverage_score } = analysis;
  const hasData = keywords_matched.length > 0 || keywords_missing.length > 0 || coverage_score != null;
  if (!hasData) return null;

  return (
    <div className="mb-3 p-3 border-2 border-stone-200 bg-stone-50">
      <div className="flex items-center justify-between mb-2">
        <span className="text-[10px] font-bold text-stone-400 uppercase tracking-wider">JD Coverage</span>
        {coverage_score != null && (
          <span
            className={`font-mono text-xs font-bold px-2 py-0.5 border-2 ${
              coverage_score >= 70
                ? 'border-success bg-success-light text-success'
                : coverage_score >= 40
                  ? 'border-yellow-dark bg-yellow-light text-yellow-dark'
                  : 'border-error bg-error-light text-error'
            }`}
          >
            {coverage_score}%
          </span>
        )}
      </div>
      {keywords_matched.length > 0 && (
        <div className="flex flex-wrap gap-1 mb-1">
          {keywords_matched.map((kw) => (
            <span
              key={kw}
              className="text-[10px] font-mono font-bold px-1.5 py-0.5 border border-success bg-success-light text-success"
            >
              {kw}
            </span>
          ))}
        </div>
      )}
      {keywords_missing.length > 0 && (
        <div className="flex flex-wrap gap-1">
          {keywords_missing.map((kw) => (
            <span
              key={kw}
              className="text-[10px] font-mono font-bold px-1.5 py-0.5 border border-error bg-error-light text-error"
            >
              {kw}
            </span>
          ))}
        </div>
      )}
    </div>
  );
}

// ---- AI Suggestion Panel ----
function AiSuggestionPanel({ jobId, sectionKey, currentContent, onAccept }) {
  const [open, setOpen] = useState(false);
  const [loading, setLoading] = useState(false);
  const [suggestion, setSuggestion] = useState(null);
  const [error, setError] = useState(null);
  const [editMode, setEditMode] = useState(false);
  const [editedSuggestion, setEditedSuggestion] = useState('');

  async function handleGetSuggestion() {
    setLoading(true);
    setError(null);
    setSuggestion(null);
    setEditMode(false);
    try {
      const data = await apiCall(`/api/dashboard/jobs/${jobId}/suggest`, {
        section: sectionKey,
        current_content: currentContent,
      });
      setSuggestion(data.suggestion || data.content || '');
      setEditedSuggestion(data.suggestion || data.content || '');
      setOpen(true);
    } catch (err) {
      setError(err.message);
      setOpen(true);
    } finally {
      setLoading(false);
    }
  }

  function handleAccept() {
    onAccept(editMode ? editedSuggestion : suggestion);
    setSuggestion(null);
    setOpen(false);
    setEditMode(false);
  }

  function handleReject() {
    setSuggestion(null);
    setOpen(false);
    setEditMode(false);
    setError(null);
  }

  return (
    <div className="mt-2">
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={open ? () => setOpen(false) : suggestion ? () => setOpen(true) : handleGetSuggestion}
          disabled={loading}
          className="inline-flex items-center gap-1.5 text-xs font-bold text-stone-500 border-2 border-stone-300
            px-2.5 py-1 hover:border-black hover:text-black transition-colors cursor-pointer disabled:opacity-50"
        >
          {loading ? (
            <>
              <span className="spinner" style={{ width: 12, height: 12, borderWidth: 2 }} />
              Getting suggestion...
            </>
          ) : (
            <>
              <Sparkles size={12} />
              AI Suggest
            </>
          )}
        </button>
        {suggestion && !open && (
          <span className="text-[10px] text-stone-400 font-mono">Suggestion ready</span>
        )}
      </div>

      {open && (
        <div className="mt-2 border-2 border-yellow bg-yellow-light">
          <div className="flex items-center justify-between px-3 pt-2 pb-1 border-b border-yellow-dark">
            <span className="text-[10px] font-bold text-yellow-dark uppercase tracking-wider">AI Suggestion</span>
            <button
              type="button"
              onClick={() => setOpen(false)}
              className="text-stone-500 hover:text-black cursor-pointer"
            >
              <X size={12} />
            </button>
          </div>
          <div className="p-3">
            {error && (
              <p className="text-xs text-error font-mono mb-2">{error}</p>
            )}
            {suggestion && (
              editMode ? (
                <textarea
                  value={editedSuggestion}
                  onChange={(e) => setEditedSuggestion(e.target.value)}
                  rows={6}
                  className="w-full border-2 border-black bg-white text-xs font-mono px-2 py-2
                    resize-none focus:outline-none focus:border-yellow-dark"
                />
              ) : (
                <p className="text-xs font-mono text-stone-700 leading-relaxed whitespace-pre-wrap">
                  {suggestion}
                </p>
              )
            )}
            {suggestion && (
              <div className="flex items-center gap-2 mt-2">
                <button
                  type="button"
                  onClick={handleAccept}
                  className="inline-flex items-center gap-1 text-xs font-bold text-cream bg-black border-2 border-black
                    px-2.5 py-1 hover:bg-stone-700 transition-colors cursor-pointer"
                >
                  <Check size={11} />
                  Accept
                </button>
                <button
                  type="button"
                  onClick={() => { setEditMode(true); setEditedSuggestion(suggestion); }}
                  className="inline-flex items-center gap-1 text-xs font-bold text-stone-600 border-2 border-stone-400
                    px-2.5 py-1 hover:border-black hover:text-black transition-colors cursor-pointer"
                >
                  <Pencil size={11} />
                  Edit
                </button>
                <button
                  type="button"
                  onClick={handleReject}
                  className="inline-flex items-center gap-1 text-xs font-bold text-error border-2 border-error
                    px-2.5 py-1 hover:bg-error-light transition-colors cursor-pointer"
                >
                  <X size={11} />
                  Reject
                </button>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

// ---- Experience / Project sub-entry editor ----
function EntryEditor({ entry, index, type, onChange }) {
  const [open, setOpen] = useState(true);

  const title = type === 'experience'
    ? `${entry.title || 'Role'} @ ${entry.company || 'Company'}`
    : entry.name || `Project ${index + 1}`;

  function updateBullet(bulletIdx, value) {
    const newBullets = [...(entry.bullets || [])];
    newBullets[bulletIdx] = value;
    onChange({ ...entry, bullets: newBullets });
  }

  function addBullet() {
    onChange({ ...entry, bullets: [...(entry.bullets || []), ''] });
  }

  function removeBullet(bulletIdx) {
    const newBullets = (entry.bullets || []).filter((_, i) => i !== bulletIdx);
    onChange({ ...entry, bullets: newBullets });
  }

  return (
    <div className="border-2 border-stone-300 bg-white mb-2">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-3 py-2 hover:bg-stone-50 transition-colors cursor-pointer"
      >
        <span className="text-xs font-bold text-stone-700">{title}</span>
        <span className="text-stone-400">
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
        </span>
      </button>
      {open && (
        <div className="px-3 pb-3 border-t border-stone-200">
          {type === 'experience' && (
            <div className="grid grid-cols-2 gap-2 mt-2 mb-2">
              <div>
                <label className="block text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-1">Title</label>
                <input
                  type="text"
                  value={entry.title || ''}
                  onChange={(e) => onChange({ ...entry, title: e.target.value })}
                  className="w-full border-2 border-black bg-white text-xs px-2 py-1.5 focus:outline-none focus:border-yellow-dark"
                />
              </div>
              <div>
                <label className="block text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-1">Company</label>
                <input
                  type="text"
                  value={entry.company || ''}
                  onChange={(e) => onChange({ ...entry, company: e.target.value })}
                  className="w-full border-2 border-black bg-white text-xs px-2 py-1.5 focus:outline-none focus:border-yellow-dark"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-1">Dates</label>
                <input
                  type="text"
                  value={entry.dates || ''}
                  onChange={(e) => onChange({ ...entry, dates: e.target.value })}
                  className="w-full border-2 border-black bg-white text-xs px-2 py-1.5 focus:outline-none focus:border-yellow-dark"
                />
              </div>
            </div>
          )}
          {type === 'projects' && (
            <div className="grid grid-cols-2 gap-2 mt-2 mb-2">
              <div>
                <label className="block text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-1">Name</label>
                <input
                  type="text"
                  value={entry.name || ''}
                  onChange={(e) => onChange({ ...entry, name: e.target.value })}
                  className="w-full border-2 border-black bg-white text-xs px-2 py-1.5 focus:outline-none focus:border-yellow-dark"
                />
              </div>
              <div>
                <label className="block text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-1">Dates</label>
                <input
                  type="text"
                  value={entry.dates || ''}
                  onChange={(e) => onChange({ ...entry, dates: e.target.value })}
                  className="w-full border-2 border-black bg-white text-xs px-2 py-1.5 focus:outline-none focus:border-yellow-dark"
                />
              </div>
              <div className="col-span-2">
                <label className="block text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-1">Technologies</label>
                <input
                  type="text"
                  value={entry.tech || ''}
                  onChange={(e) => onChange({ ...entry, tech: e.target.value })}
                  className="w-full border-2 border-black bg-white text-xs px-2 py-1.5 focus:outline-none focus:border-yellow-dark"
                />
              </div>
            </div>
          )}
          <div>
            <label className="block text-[10px] font-bold text-stone-400 uppercase tracking-wider mb-1">Bullets</label>
            <div className="space-y-1.5">
              {(entry.bullets || []).map((bullet, bi) => (
                <div key={bi} className="flex items-start gap-1.5">
                  <span className="mt-2 text-stone-400 shrink-0 text-xs">•</span>
                  <textarea
                    value={bullet}
                    onChange={(e) => updateBullet(bi, e.target.value)}
                    rows={2}
                    className="flex-1 border-2 border-stone-300 bg-white text-xs font-mono px-2 py-1.5
                      resize-none focus:outline-none focus:border-black"
                  />
                  <button
                    type="button"
                    onClick={() => removeBullet(bi)}
                    className="mt-1 text-stone-400 hover:text-error transition-colors cursor-pointer shrink-0"
                  >
                    <X size={12} />
                  </button>
                </div>
              ))}
            </div>
            <button
              type="button"
              onClick={addBullet}
              className="mt-2 text-[10px] font-bold text-stone-500 border-2 border-dashed border-stone-300
                px-2.5 py-1 hover:border-black hover:text-black transition-colors cursor-pointer w-full"
            >
              + Add Bullet
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ---- Main SectionEditor ----
export default function SectionEditor({ jobId, sectionKey, label, value, analysis, onChange }) {
  const [open, setOpen] = useState(true);
  const isListSection = sectionKey === 'experience' || sectionKey === 'projects';
  const isSkills = sectionKey === 'skills';

  function handleTextChange(e) {
    onChange(e.target.value);
  }

  function handleEntryChange(idx, updated) {
    const newList = [...value];
    newList[idx] = updated;
    onChange(newList);
  }

  function handleSkillCategoryChange(idx, field, val) {
    const newList = [...value];
    newList[idx] = { ...newList[idx], [field]: val };
    onChange(newList);
  }

  function getTextValue() {
    if (typeof value === 'string') return value;
    return '';
  }

  return (
    <div className="border-2 border-black mb-3 bg-white">
      {/* Section header */}
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="w-full flex items-center justify-between px-4 py-3 bg-stone-100 hover:bg-stone-200
          border-b-2 border-black transition-colors cursor-pointer"
      >
        <span className="text-sm font-heading font-bold text-black uppercase tracking-wide">{label}</span>
        <span className="text-stone-500">
          {open ? <ChevronDown size={16} /> : <ChevronRight size={16} />}
        </span>
      </button>

      {open && (
        <div className="p-4">
          {/* JD analysis */}
          <JdAnalysisBar analysis={analysis} />

          {/* Editable content */}
          {isListSection ? (
            <div>
              {Array.isArray(value) && value.map((entry, idx) => (
                <EntryEditor
                  key={idx}
                  entry={entry}
                  index={idx}
                  type={sectionKey}
                  onChange={(updated) => handleEntryChange(idx, updated)}
                />
              ))}
              {(!Array.isArray(value) || value.length === 0) && (
                <p className="text-xs text-stone-400 font-mono">No entries yet.</p>
              )}
            </div>
          ) : isSkills ? (
            <div className="space-y-2">
              {Array.isArray(value) && value.map((skill, idx) => (
                <div key={idx} className="grid grid-cols-3 gap-2 items-start">
                  <input
                    type="text"
                    value={skill.category || ''}
                    onChange={(e) => handleSkillCategoryChange(idx, 'category', e.target.value)}
                    placeholder="Category"
                    className="border-2 border-black bg-white text-xs font-bold px-2 py-1.5
                      focus:outline-none focus:border-yellow-dark"
                  />
                  <input
                    type="text"
                    value={skill.items || ''}
                    onChange={(e) => handleSkillCategoryChange(idx, 'items', e.target.value)}
                    placeholder="Python, TypeScript, ..."
                    className="col-span-2 border-2 border-stone-300 bg-white text-xs font-mono px-2 py-1.5
                      focus:outline-none focus:border-black"
                  />
                </div>
              ))}
            </div>
          ) : (
            <textarea
              value={getTextValue()}
              onChange={handleTextChange}
              rows={sectionKey === 'summary' ? 5 : 3}
              className="w-full border-2 border-black bg-white text-sm font-mono px-3 py-2.5
                resize-none focus:outline-none focus:border-yellow-dark leading-relaxed"
            />
          )}

          {/* AI suggestion — only for text sections */}
          {!isListSection && !isSkills && (
            <AiSuggestionPanel
              jobId={jobId}
              sectionKey={sectionKey}
              currentContent={getTextValue()}
              onAccept={(text) => onChange(text)}
            />
          )}
        </div>
      )}
    </div>
  );
}
