import type { StateStorage } from 'zustand/middleware';

/**
 * The access token carries the session's company (claim `cid`), so it must
 * belong to one tab, not the whole browser: switching company in one tab
 * must not move the others. The token is kept in sessionStorage (one per
 * tab, survives reloads); localStorage keeps a copy only so a brand-new tab
 * has something to start from.
 */
const TOKEN_KEY_SUFFIX = ':access-token';

interface PersistedEnvelope {
  state?: Record<string, unknown> & { token?: string | null };
  version?: number;
}

function safe<T>(fn: () => T, fallback: T): T {
  try {
    return fn();
  } catch {
    return fallback;
  }
}

export const tabScopedTokenStorage: StateStorage = {
  getItem: (name) => {
    const shared = safe(() => window.localStorage.getItem(name), null);
    if (!shared) return null;
    const tabToken = safe(() => window.sessionStorage.getItem(name + TOKEN_KEY_SUFFIX), null);
    if (!tabToken) return shared;
    const parsed = safe<PersistedEnvelope | null>(() => JSON.parse(shared), null);
    if (!parsed?.state) return shared;
    parsed.state.token = tabToken;
    return JSON.stringify(parsed);
  },
  setItem: (name, value) => {
    safe(() => window.localStorage.setItem(name, value), undefined);
    const parsed = safe<PersistedEnvelope | null>(() => JSON.parse(value), null);
    const token = parsed?.state?.token;
    safe(() => {
      if (token) window.sessionStorage.setItem(name + TOKEN_KEY_SUFFIX, token);
      else window.sessionStorage.removeItem(name + TOKEN_KEY_SUFFIX);
    }, undefined);
  },
  removeItem: (name) => {
    safe(() => window.localStorage.removeItem(name), undefined);
    safe(() => window.sessionStorage.removeItem(name + TOKEN_KEY_SUFFIX), undefined);
  },
};

/** The company id pinned in an access token, or null. Reads the payload
 * only; the server is what verifies the token. */
export function companyIdFromToken(token: string | null | undefined): string | null {
  if (!token) return null;
  const part = token.split('.')[1];
  if (!part) return null;
  return safe(() => {
    const b64 = part.replace(/-/g, '+').replace(/_/g, '/');
    const padded = b64 + '='.repeat((4 - (b64.length % 4)) % 4);
    const payload = JSON.parse(atob(padded)) as { cid?: unknown };
    return typeof payload.cid === 'string' ? payload.cid : null;
  }, null);
}
