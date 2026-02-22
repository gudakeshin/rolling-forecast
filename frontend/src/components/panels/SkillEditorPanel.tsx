import { useState, useEffect, useCallback } from 'react';

interface SkillInfo {
  name: string;
  description: string;
  has_definition: boolean;
  tags: string[];
  version: string;
  required_role: string | null;
  param_count: number;
}

interface SkillDetail {
  name: string;
  description: string;
  parameters: any[];
  required_role: string | null;
  tags: string[];
  version: string;
  when_to_use: string;
  examples: string[];
  md_content: string | null;
  md_file: string;
}

export function SkillEditorPanel() {
  const [skills, setSkills] = useState<SkillInfo[]>([]);
  const [selectedSkill, setSelectedSkill] = useState<string | null>(null);
  const [detail, setDetail] = useState<SkillDetail | null>(null);
  const [editContent, setEditContent] = useState<string>('');
  const [isEditing, setIsEditing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [loading, setLoading] = useState(true);

  const apiBase = 'http://localhost:8000';

  const fetchSkills = useCallback(async () => {
    try {
      const res = await fetch(`${apiBase}/api/skills/`);
      const data = await res.json();
      setSkills(data.skills || []);
    } catch (e) {
      console.error('Failed to fetch skills:', e);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    fetchSkills();
  }, [fetchSkills]);

  const fetchDetail = async (name: string) => {
    try {
      const res = await fetch(`${apiBase}/api/skills/${name}`);
      const data = await res.json();
      setDetail(data);
      setEditContent(data.md_content || '');
      setSelectedSkill(name);
      setIsEditing(false);
    } catch (e) {
      console.error('Failed to fetch skill detail:', e);
    }
  };

  const handleSave = async () => {
    if (!selectedSkill) return;
    setSaving(true);
    setMessage(null);

    try {
      const res = await fetch(`${apiBase}/api/skills/${selectedSkill}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content: editContent }),
      });

      if (res.ok) {
        setMessage({ type: 'success', text: 'Skill definition saved successfully!' });
        setIsEditing(false);
        fetchDetail(selectedSkill);
        fetchSkills();
      } else {
        const err = await res.json();
        setMessage({ type: 'error', text: err.detail || 'Failed to save' });
      }
    } catch (e) {
      setMessage({ type: 'error', text: 'Network error while saving' });
    } finally {
      setSaving(false);
    }
  };

  const handleReload = async () => {
    try {
      await fetch(`${apiBase}/api/skills/reload`, { method: 'POST' });
      fetchSkills();
      setMessage({ type: 'success', text: 'All skill definitions reloaded from disk' });
    } catch (e) {
      setMessage({ type: 'error', text: 'Failed to reload' });
    }
  };

  if (loading) {
    return (
      <div className="flex items-center justify-center h-64 text-surface-400">
        Loading skills...
      </div>
    );
  }

  return (
    <div className="h-full flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between p-4 border-b border-surface-700/50">
        <div>
          <h2 className="text-lg font-semibold text-white">Skill Editor</h2>
          <p className="text-xs text-surface-400 mt-0.5">
            {skills.length} skills registered &middot; Edit .md files to customize behavior
          </p>
        </div>
        <button
          onClick={handleReload}
          className="px-3 py-1.5 text-xs bg-surface-700 hover:bg-surface-600 text-surface-300 rounded transition-colors"
        >
          Reload All
        </button>
      </div>

      {/* Message */}
      {message && (
        <div
          className={`mx-4 mt-3 px-3 py-2 rounded text-xs ${
            message.type === 'success'
              ? 'bg-deloitte-green/10 text-deloitte-green border border-deloitte-green/20'
              : 'bg-red-500/10 text-red-400 border border-red-500/20'
          }`}
        >
          {message.text}
        </div>
      )}

      <div className="flex-1 flex overflow-hidden">
        {/* Skill List */}
        <div className="w-64 border-r border-surface-700/50 overflow-y-auto">
          {skills.map((skill) => (
            <button
              key={skill.name}
              onClick={() => fetchDetail(skill.name)}
              className={`w-full text-left px-4 py-3 border-b border-surface-800 transition-colors ${
                selectedSkill === skill.name
                  ? 'bg-deloitte-green/10 border-l-2 border-l-deloitte-green'
                  : 'hover:bg-surface-800/50 border-l-2 border-l-transparent'
              }`}
            >
              <div className="flex items-center gap-2">
                <span
                  className={`text-sm font-medium ${
                    selectedSkill === skill.name ? 'text-deloitte-green' : 'text-surface-200'
                  }`}
                >
                  {skill.name}
                </span>
                {skill.has_definition && (
                  <span className="w-1.5 h-1.5 rounded-full bg-deloitte-green flex-shrink-0" />
                )}
              </div>
              <div className="text-xs text-surface-400 mt-0.5 truncate">
                {skill.description}
              </div>
              <div className="flex gap-1 mt-1 flex-wrap">
                {skill.tags.slice(0, 3).map((tag) => (
                  <span
                    key={tag}
                    className="px-1.5 py-0.5 text-[10px] bg-surface-700 text-surface-400 rounded"
                  >
                    {tag}
                  </span>
                ))}
              </div>
            </button>
          ))}
        </div>

        {/* Detail / Editor */}
        <div className="flex-1 overflow-y-auto">
          {!detail ? (
            <div className="flex items-center justify-center h-full text-surface-400 text-sm">
              Select a skill to view or edit its definition
            </div>
          ) : isEditing ? (
            /* Edit Mode */
            <div className="flex flex-col h-full">
              <div className="flex items-center justify-between p-3 bg-surface-800 border-b border-surface-700">
                <span className="text-sm text-surface-300">
                  Editing: <span className="text-deloitte-green font-mono">{detail.name}.md</span>
                </span>
                <div className="flex gap-2">
                  <button
                    onClick={() => setIsEditing(false)}
                    className="px-3 py-1 text-xs bg-surface-700 hover:bg-surface-600 text-surface-300 rounded"
                  >
                    Cancel
                  </button>
                  <button
                    onClick={handleSave}
                    disabled={saving}
                    className="px-3 py-1 text-xs bg-deloitte-green text-black font-semibold rounded hover:bg-deloitte-green/90 disabled:opacity-50"
                  >
                    {saving ? 'Saving...' : 'Save'}
                  </button>
                </div>
              </div>
              <textarea
                value={editContent}
                onChange={(e) => setEditContent(e.target.value)}
                className="flex-1 w-full p-4 bg-black text-surface-200 font-mono text-xs leading-relaxed resize-none focus:outline-none"
                spellCheck={false}
              />
            </div>
          ) : (
            /* View Mode */
            <div className="p-4 space-y-4">
              <div className="flex items-start justify-between">
                <div>
                  <h3 className="text-lg font-semibold text-white">{detail.name}</h3>
                  <p className="text-sm text-surface-300 mt-1">{detail.description}</p>
                </div>
                {detail.md_content && (
                  <button
                    onClick={() => setIsEditing(true)}
                    className="px-3 py-1.5 text-xs bg-deloitte-green/10 border border-deloitte-green/25 text-deloitte-green rounded hover:bg-deloitte-green/20 transition-colors flex-shrink-0"
                  >
                    Edit .md
                  </button>
                )}
              </div>

              {/* Metadata */}
              <div className="grid grid-cols-3 gap-3">
                <MetaCard label="Version" value={detail.version} />
                <MetaCard label="Role Required" value={detail.required_role || 'Any'} />
                <MetaCard label="Parameters" value={String(detail.parameters.length)} />
              </div>

              {/* Tags */}
              {detail.tags.length > 0 && (
                <div>
                  <h4 className="text-xs font-semibold text-surface-400 uppercase tracking-wider mb-1.5">
                    Tags
                  </h4>
                  <div className="flex gap-1.5 flex-wrap">
                    {detail.tags.map((tag) => (
                      <span
                        key={tag}
                        className="px-2 py-0.5 text-xs bg-accent-500/10 text-accent-500 rounded-full"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* When to Use */}
              {detail.when_to_use && (
                <div>
                  <h4 className="text-xs font-semibold text-surface-400 uppercase tracking-wider mb-1.5">
                    When to Use
                  </h4>
                  <p className="text-sm text-surface-300 leading-relaxed whitespace-pre-wrap">
                    {detail.when_to_use}
                  </p>
                </div>
              )}

              {/* Examples */}
              {detail.examples.length > 0 && (
                <div>
                  <h4 className="text-xs font-semibold text-surface-400 uppercase tracking-wider mb-1.5">
                    Examples
                  </h4>
                  <ul className="space-y-1">
                    {detail.examples.map((ex, i) => (
                      <li key={i} className="flex items-start gap-2 text-sm">
                        <span className="text-deloitte-green mt-0.5 text-xs">&bull;</span>
                        <span className="text-surface-300">&ldquo;{ex}&rdquo;</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}

              {/* Parameters */}
              {detail.parameters.length > 0 && (
                <div>
                  <h4 className="text-xs font-semibold text-surface-400 uppercase tracking-wider mb-2">
                    Parameters
                  </h4>
                  <div className="space-y-2">
                    {detail.parameters.map((p: any) => (
                      <div
                        key={p.name}
                        className="px-3 py-2 bg-surface-800 rounded border border-surface-700"
                      >
                        <div className="flex items-center gap-2">
                          <code className="text-xs font-mono text-deloitte-green">{p.name}</code>
                          <span className="text-[10px] px-1.5 py-0.5 bg-surface-700 text-surface-400 rounded">
                            {p.type}
                          </span>
                          {p.required && (
                            <span className="text-[10px] px-1.5 py-0.5 bg-red-500/10 text-red-400 rounded">
                              required
                            </span>
                          )}
                        </div>
                        <p className="text-xs text-surface-400 mt-1">{p.description}</p>
                        {p.enum && (
                          <div className="flex gap-1 mt-1 flex-wrap">
                            {p.enum.map((v: string) => (
                              <span
                                key={v}
                                className="text-[10px] px-1 py-0.5 bg-surface-700 text-surface-300 rounded font-mono"
                              >
                                {v}
                              </span>
                            ))}
                          </div>
                        )}
                      </div>
                    ))}
                  </div>
                </div>
              )}

              {/* Raw .md file path */}
              <div className="pt-2 border-t border-surface-700/50">
                <p className="text-xs text-surface-500 font-mono">{detail.md_file}</p>
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function MetaCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="px-3 py-2 bg-surface-800 rounded border border-surface-700">
      <p className="text-[10px] text-surface-400 uppercase tracking-wider">{label}</p>
      <p className="text-sm font-semibold text-white mt-0.5">{value}</p>
    </div>
  );
}
