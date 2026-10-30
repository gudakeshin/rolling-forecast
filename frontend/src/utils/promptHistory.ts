/** Recently sent chat prompts for ↑/↓ recall in the composer (per browser;
 * a convenience, so storage failures are ignored). */
const KEY = 'rf_prompt_history';
const MAX = 50;

export function loadPromptHistory(): string[] {
  try {
    const raw = JSON.parse(localStorage.getItem(KEY) || '[]');
    return Array.isArray(raw) ? raw.filter((p): p is string => typeof p === 'string').slice(-MAX) : [];
  } catch {
    return [];
  }
}

/** Append a sent prompt (collapsing an immediate repeat) and persist. */
export function rememberPrompt(history: string[], prompt: string): string[] {
  const next = history[history.length - 1] === prompt ? history : [...history, prompt].slice(-MAX);
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // Storage unavailable: history still works for this session.
  }
  return next;
}

/** Where ↑ (-1) or ↓ (+1) moves from `index` (null = not browsing); null when
 * ↓ walks past the newest entry, meaning "back to an empty composer". */
export function stepHistory(length: number, index: number | null, direction: -1 | 1): number | null {
  if (length === 0) return null;
  if (index === null) return direction === -1 ? length - 1 : null;
  const next = index + direction;
  if (next < 0) return 0;
  if (next >= length) return null;
  return next;
}
