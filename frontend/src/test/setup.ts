import '@testing-library/jest-dom/vitest';
import * as matchers from 'vitest-axe/matchers';
import { expect } from 'vitest';

expect.extend(matchers);

/**
 * Node 22+ exposes a built-in `localStorage` global that is `undefined` unless
 * the process is started with --localstorage-file, and it shadows the one jsdom
 * installs. Zustand's `persist` middleware then throws on first setState, which
 * fails every test that touches a persisted store. Install a real in-memory
 * implementation when the global is missing or unusable.
 */
function installMemoryStorage(key: 'localStorage' | 'sessionStorage') {
  const existing = (globalThis as Record<string, unknown>)[key];
  if (existing && typeof (existing as Storage).setItem === 'function') return;

  const store = new Map<string, string>();
  const storage: Storage = {
    get length() {
      return store.size;
    },
    clear: () => store.clear(),
    getItem: (k: string) => (store.has(k) ? store.get(k)! : null),
    key: (i: number) => Array.from(store.keys())[i] ?? null,
    removeItem: (k: string) => void store.delete(k),
    setItem: (k: string, v: string) => void store.set(k, String(v)),
  };

  Object.defineProperty(globalThis, key, {
    value: storage,
    writable: true,
    configurable: true,
  });
  if (typeof window !== 'undefined') {
    Object.defineProperty(window, key, { value: storage, writable: true, configurable: true });
  }
}

installMemoryStorage('localStorage');
installMemoryStorage('sessionStorage');
