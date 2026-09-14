/**
 * Bookkeeping fields that make sense inside the panel store but would
 * corrupt a shareable link or saved view (a fresh cache-busting nonce, a
 * paging-in-progress flag, or a scroll position nobody wants to resume
 * into) — a shared/saved view should open at a clean, top-of-page state.
 */
const NON_SHAREABLE_KEYS = new Set(['_refresh', '_append', 'offset']);

/** Strip internal bookkeeping so a link/saved view only carries real filter state. */
export function shareablePanelParams(params: Record<string, any>): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [k, v] of Object.entries(params || {})) {
    if (NON_SHAREABLE_KEYS.has(k) || v === undefined) continue;
    out[k] = v;
  }
  return out;
}
