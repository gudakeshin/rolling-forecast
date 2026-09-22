import { useState, useRef, useCallback, useEffect, useMemo } from 'react';
import { Send, Paperclip, Loader2, CheckCircle, Square, Terminal, AtSign } from 'lucide-react';
import { uploadFile, apiPost } from '../../api/client';
import { useI18n } from '../../i18n/useI18n';
import { useComposerStore } from '../../store/composerStore';
import { searchLineItems, type LineItemSearchResult } from '../../api/dashboard';
import type { MessageKey } from '../../i18n';

const ACTUALS_EXTENSIONS = new Set(['.csv', '.xlsx', '.xls']);
const CONTEXT_EXTENSIONS = new Set(['.pdf', '.docx', '.doc', '.pptx', '.txt', '.md', '.html', '.htm']);
const MENTION_SEARCH_DEBOUNCE_MS = 200;

function getFileExtension(name: string): string {
  return (name.lastIndexOf('.') >= 0 ? name.slice(name.lastIndexOf('.')) : '').toLowerCase();
}

interface SlashCommand {
  id: string;
  labelKey: MessageKey;
  descKey: MessageKey;
  promptKey: MessageKey;
}

const SLASH_COMMANDS: SlashCommand[] = [
  { id: 'generate', labelKey: 'chat.slash.generate.label', descKey: 'chat.slash.generate.desc', promptKey: 'chat.suggestion.generate' },
  { id: 'review', labelKey: 'chat.slash.review.label', descKey: 'chat.slash.review.desc', promptKey: 'chat.suggestion.review' },
  { id: 'drivers', labelKey: 'chat.slash.drivers.label', descKey: 'chat.slash.drivers.desc', promptKey: 'chat.suggestion.drivers' },
  { id: 'compare', labelKey: 'chat.slash.compare.label', descKey: 'chat.slash.compare.desc', promptKey: 'chat.suggestion.compare' },
  { id: 'help', labelKey: 'chat.slash.help.label', descKey: 'chat.slash.help.desc', promptKey: 'chat.slash.help.desc' },
];

/** Finds the "@query" token touching the caret, if any (start of line or after whitespace). */
function findMentionToken(value: string, caret: number): { start: number; end: number; query: string } | null {
  const uptoCaret = value.slice(0, caret);
  const at = uptoCaret.lastIndexOf('@');
  if (at === -1) return null;
  if (at > 0 && !/\s/.test(uptoCaret[at - 1])) return null;
  const query = uptoCaret.slice(at + 1);
  if (/\s/.test(query) || query.length > 40) return null;
  return { start: at, end: caret, query };
}

interface Props {
  onSend: (content: string) => void;
  onStop?: () => void;
  isStreaming: boolean;
}

export function InputBar({ onSend, onStop, isStreaming }: Props) {
  const { t } = useI18n();
  const [input, setInput] = useState('');
  const [isUploading, setIsUploading] = useState(false);
  const [uploadStatus, setUploadStatus] = useState<string | null>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);
  const textareaRef = useRef<HTMLTextAreaElement>(null);
  const focusRequestId = useComposerStore((s) => s.focusRequestId);

  const [menuClosed, setMenuClosed] = useState(false);
  const [slashHighlight, setSlashHighlight] = useState(0);
  const [mentionToken, setMentionToken] = useState<{ start: number; end: number; query: string } | null>(null);
  const [mentionHighlight, setMentionHighlight] = useState(0);
  const [mentionResults, setMentionResults] = useState<LineItemSearchResult[]>([]);
  const [mentionLoading, setMentionLoading] = useState(false);

  useEffect(() => {
    if (focusRequestId > 0) textareaRef.current?.focus();
  }, [focusRequestId]);

  const slashMatch = !menuClosed && /^\/([a-z]*)$/i.exec(input);
  const slashQuery = slashMatch ? slashMatch[1].toLowerCase() : null;
  const filteredCommands = useMemo(() => {
    if (slashQuery == null) return [];
    return SLASH_COMMANDS.filter(
      (c) => c.id.startsWith(slashQuery) || t(c.labelKey).toLowerCase().includes(slashQuery),
    );
  }, [slashQuery, t]);
  const slashOpen = slashQuery != null;
  const mentionOpen = !menuClosed && mentionToken != null;

  useEffect(() => {
    setSlashHighlight(0);
  }, [slashQuery]);

  useEffect(() => {
    if (!mentionToken || mentionToken.query.length === 0) {
      setMentionResults([]);
      setMentionLoading(false);
      return;
    }
    setMentionLoading(true);
    let cancelled = false;
    const timer = window.setTimeout(() => {
      searchLineItems(mentionToken.query, 5)
        .then((res) => {
          if (!cancelled) setMentionResults(res.items || []);
        })
        .catch(() => {
          if (!cancelled) setMentionResults([]);
        })
        .finally(() => {
          if (!cancelled) setMentionLoading(false);
        });
    }, MENTION_SEARCH_DEBOUNCE_MS);
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
    // Only the query text should re-trigger the search.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mentionToken?.query]);

  useEffect(() => {
    setMentionHighlight(0);
  }, [mentionResults.length]);

  const handleSend = useCallback(() => {
    const trimmed = input.trim();
    if (!trimmed || isStreaming) return;
    onSend(trimmed);
    setInput('');
    setMentionToken(null);
    if (textareaRef.current) textareaRef.current.style.height = 'auto';
  }, [input, isStreaming, onSend]);

  const runSlashCommand = (cmd: SlashCommand) => {
    onSend(t(cmd.promptKey));
    setInput('');
    setMenuClosed(false);
  };

  const insertMention = (item: LineItemSearchResult) => {
    if (!mentionToken) return;
    const before = input.slice(0, mentionToken.start);
    const after = input.slice(mentionToken.end);
    const inserted = `${item.name} `;
    const next = `${before}${inserted}${after}`;
    setInput(next);
    setMentionToken(null);
    const caret = before.length + inserted.length;
    requestAnimationFrame(() => {
      const el = textareaRef.current;
      if (el) {
        el.focus();
        el.setSelectionRange(caret, caret);
      }
    });
  };

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (slashOpen) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setSlashHighlight((h) => Math.min(h + 1, Math.max(filteredCommands.length - 1, 0)));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setSlashHighlight((h) => Math.max(h - 1, 0));
        return;
      }
      if (e.key === 'Enter') {
        e.preventDefault();
        const cmd = filteredCommands[slashHighlight];
        if (cmd) runSlashCommand(cmd);
        return;
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setMenuClosed(true);
        return;
      }
      if (e.key === ' ') {
        // Falls through to normal typing — regex no longer matches once a
        // space lands, closing the menu naturally on the next render.
      }
    } else if (mentionOpen) {
      if (e.key === 'ArrowDown') {
        e.preventDefault();
        setMentionHighlight((h) => Math.min(h + 1, Math.max(mentionResults.length - 1, 0)));
        return;
      }
      if (e.key === 'ArrowUp') {
        e.preventDefault();
        setMentionHighlight((h) => Math.max(h - 1, 0));
        return;
      }
      if (e.key === 'Enter') {
        const item = mentionResults[mentionHighlight];
        if (item) {
          e.preventDefault();
          insertMention(item);
          return;
        }
      }
      if (e.key === 'Escape') {
        e.preventDefault();
        setMenuClosed(true);
        return;
      }
    }

    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  };

  const handleFileUpload = async (e: React.ChangeEvent<HTMLInputElement>) => {
    const file = e.target.files?.[0];
    if (!file) return;
    setIsUploading(true);
    setUploadStatus(null);

    const ext = getFileExtension(file.name);

    try {
      if (ACTUALS_EXTENSIONS.has(ext)) {
        // /upload/actuals ingests immediately by default (ingest=true) — no
        // chat round-trip needed for the LLM to notice and call ingest_actuals.
        const result = await uploadFile('/upload/actuals', file);
        setUploadStatus(result.message || `${file.name} ingested`);
        onSend(`I've uploaded and ingested "${result.filename}" as actuals data. ${result.message || ''}`);
        setTimeout(() => setUploadStatus(null), 5000);
      } else if (CONTEXT_EXTENSIONS.has(ext) || !ACTUALS_EXTENSIONS.has(ext)) {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('scope', 'user');
        const result = await apiPost<any>('/context/upload', formData);
        if (result.status === 'ready') {
          setUploadStatus(`${file.name} indexed (${result.chunk_count} chunks)`);
          onSend(`I've uploaded "${file.name}" to the document library. It has been indexed with ${result.chunk_count} chunks. You can now search it for relevant context.`);
        } else if (result.status === 'failed') {
          onSend(`Upload processing failed for ${file.name}: ${result.error_message || 'Unknown error'}`);
        } else {
          setUploadStatus(`${file.name} is being processed...`);
          onSend(`I've uploaded "${file.name}" to the document library. It's being processed for indexing.`);
        }
        setTimeout(() => setUploadStatus(null), 5000);
      }
    } catch (error: any) {
      onSend(`Upload failed: ${error.message}`);
    } finally {
      setIsUploading(false);
      if (fileInputRef.current) fileInputRef.current.value = '';
    }
  };

  const handleInput = (e: React.ChangeEvent<HTMLTextAreaElement>) => {
    const value = e.target.value;
    setInput(value);
    setMenuClosed(false);
    setMentionToken(findMentionToken(value, e.target.selectionStart ?? value.length));
    const textarea = e.target;
    textarea.style.height = 'auto';
    textarea.style.height = `${Math.min(textarea.scrollHeight, 200)}px`;
  };

  return (
    <div className="relative">
      {uploadStatus && (
        <div className="absolute -top-8 left-0 right-0 flex items-center justify-center gap-1.5 text-xs text-deloitte-green animate-fade-in">
          <CheckCircle className="w-3 h-3" /> {uploadStatus}
        </div>
      )}

      {slashOpen && (
        <div
          role="listbox"
          aria-label={t('chat.slash.hint')}
          className="absolute bottom-full left-0 right-0 mb-2 bg-surface-800 border border-surface-700 rounded-xl shadow-xl shadow-black/40 overflow-hidden"
        >
          <div className="px-3.5 pt-2 pb-1 text-[10px] font-bold text-surface-500 uppercase tracking-wider flex items-center gap-1.5">
            <Terminal className="w-3 h-3" aria-hidden="true" /> {t('chat.slash.hint')}
          </div>
          {filteredCommands.length === 0 ? (
            <p className="px-3.5 py-3 text-sm text-surface-500">{t('chat.slash.empty')}</p>
          ) : (
            <div className="max-h-60 overflow-y-auto pb-1.5">
              {filteredCommands.map((cmd, i) => (
                <button
                  key={cmd.id}
                  type="button"
                  role="option"
                  aria-selected={i === slashHighlight}
                  onMouseEnter={() => setSlashHighlight(i)}
                  onClick={() => runSlashCommand(cmd)}
                  className={`w-full flex items-center justify-between gap-2 px-3.5 py-2 text-left text-sm transition-colors ${
                    i === slashHighlight ? 'bg-deloitte-green/12 text-deloitte-green' : 'text-surface-200'
                  }`}
                >
                  <span className="font-mono text-xs">{t(cmd.labelKey)}</span>
                  <span className="text-xs text-surface-500 truncate">{t(cmd.descKey)}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      {mentionOpen && (
        <div
          role="listbox"
          aria-label={t('chat.mention.hint')}
          className="absolute bottom-full left-0 right-0 mb-2 bg-surface-800 border border-surface-700 rounded-xl shadow-xl shadow-black/40 overflow-hidden"
        >
          <div className="px-3.5 pt-2 pb-1 text-[10px] font-bold text-surface-500 uppercase tracking-wider flex items-center gap-1.5">
            <AtSign className="w-3 h-3" aria-hidden="true" /> {t('chat.mention.hint')}
          </div>
          {mentionLoading ? (
            <p className="px-3.5 py-3 text-sm text-surface-500">{t('chat.mention.searching')}</p>
          ) : mentionResults.length === 0 ? (
            <p className="px-3.5 py-3 text-sm text-surface-500">{t('chat.mention.empty')}</p>
          ) : (
            <div className="max-h-60 overflow-y-auto pb-1.5">
              {mentionResults.map((item, i) => (
                <button
                  key={item.id}
                  type="button"
                  role="option"
                  aria-selected={i === mentionHighlight}
                  onMouseEnter={() => setMentionHighlight(i)}
                  onClick={() => insertMention(item)}
                  className={`w-full flex items-center justify-between gap-2 px-3.5 py-2 text-left text-sm transition-colors ${
                    i === mentionHighlight ? 'bg-deloitte-green/12 text-deloitte-green' : 'text-surface-200'
                  }`}
                >
                  <span className="truncate">{item.name}</span>
                  <span className="text-xs text-surface-500 shrink-0">{item.account_code}</span>
                </button>
              ))}
            </div>
          )}
        </div>
      )}

      <div className="flex items-end gap-2 bg-surface-800 border border-surface-700 rounded-2xl px-4 py-3 focus-within:border-deloitte-green/40 focus-within:glow-green transition-all">
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={isUploading}
          aria-label={t('chat.upload')}
          className="flex-shrink-0 p-1.5 hover:bg-surface-700 rounded-lg transition-colors text-surface-400 hover:text-deloitte-green disabled:opacity-50 min-h-[44px] min-w-[44px] flex items-center justify-center"
          title={t('chat.upload')}
        >
          {isUploading ? (
            <Loader2 className="w-5 h-5 animate-spin" />
          ) : (
            <Paperclip className="w-5 h-5" />
          )}
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept=".csv,.xlsx,.xls,.pdf,.docx,.doc,.pptx,.txt,.md,.html,.htm"
          onChange={handleFileUpload}
          className="hidden"
        />

        <textarea
          ref={textareaRef}
          value={input}
          onChange={handleInput}
          onKeyDown={handleKeyDown}
          placeholder={t('chat.placeholder')}
          aria-label={t('chat.composer')}
          rows={1}
          className="flex-1 bg-transparent text-white placeholder-surface-500 resize-none focus:outline-none text-sm leading-6 max-h-[200px]"
          disabled={isStreaming}
        />

        {isStreaming ? (
          <button
            type="button"
            onClick={onStop}
            aria-label={t('chat.stop')}
            title={t('chat.stop')}
            className="flex-shrink-0 p-1.5 bg-red-500/20 hover:bg-red-500/30 text-red-400 border border-red-500/30 rounded-lg transition-colors min-h-[44px] min-w-[44px] flex items-center justify-center"
          >
            <Square className="w-4 h-4 fill-current" />
          </button>
        ) : (
          <button
            type="button"
            onClick={handleSend}
            disabled={!input.trim()}
            aria-label={t('chat.send')}
            className="flex-shrink-0 p-1.5 bg-deloitte-green hover:bg-deloitte-green/90 disabled:bg-surface-700 disabled:text-surface-600 text-white rounded-lg transition-colors min-h-[44px] min-w-[44px] flex items-center justify-center"
          >
            <Send className="w-5 h-5" />
          </button>
        )}
      </div>

      <p className="text-center text-xs text-surface-500 mt-2">
        {t('chat.disclaimer')}
      </p>
    </div>
  );
}
