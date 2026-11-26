import { beforeEach, describe, expect, it } from 'vitest';
import { companyIdFromToken, tabScopedTokenStorage } from './sessionToken';

function jwt(payload: Record<string, unknown>): string {
  const b64 = btoa(JSON.stringify(payload)).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
  return `e30.${b64}.sig`;
}

describe('companyIdFromToken', () => {
  it('reads the cid claim', () => {
    expect(companyIdFromToken(jwt({ sub: 'u', cid: 'co-b' }))).toBe('co-b');
  });
  it('is null for tokens without a company or garbage', () => {
    expect(companyIdFromToken(jwt({ sub: 'u' }))).toBeNull();
    expect(companyIdFromToken('not-a-jwt')).toBeNull();
    expect(companyIdFromToken(null)).toBeNull();
  });
});

describe('tabScopedTokenStorage', () => {
  beforeEach(() => {
    window.localStorage.clear();
    window.sessionStorage.clear();
  });

  const envelope = (token: string | null) => JSON.stringify({ state: { token, user: { id: 'u' } }, version: 0 });

  it("prefers this tab's token over the one another tab wrote last", () => {
    tabScopedTokenStorage.setItem('auth', envelope('tab-a-token'));
    // Another tab switches company: it rewrites localStorage, not our sessionStorage.
    window.localStorage.setItem('auth', envelope('tab-b-token'));
    const read = JSON.parse(tabScopedTokenStorage.getItem('auth') as string);
    expect(read.state.token).toBe('tab-a-token');
    expect(read.state.user.id).toBe('u');
  });

  it('a new tab starts from the shared copy', () => {
    window.localStorage.setItem('auth', envelope('shared-token'));
    const read = JSON.parse(tabScopedTokenStorage.getItem('auth') as string);
    expect(read.state.token).toBe('shared-token');
  });

  it('logout clears both copies', () => {
    tabScopedTokenStorage.setItem('auth', envelope('t'));
    tabScopedTokenStorage.removeItem('auth');
    expect(tabScopedTokenStorage.getItem('auth')).toBeNull();
  });
});
