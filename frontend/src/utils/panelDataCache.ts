/** Recently fetched panel payloads, keyed by panel type + request URL, so
 * switching back to a panel shows it instantly instead of refetching (and
 * losing its scroll position). Small and short-lived: entries expire after
 * TTL_MS and the oldest is evicted past MAX_ENTRIES. An explicit refresh
 * bypasses it. */
const TTL_MS = 120_000;
const MAX_ENTRIES = 16;

const entries = new Map<string, { data: unknown; at: number }>();

export function getCachedPanelData<T>(key: string, now = Date.now()): T | undefined {
  const hit = entries.get(key);
  if (!hit) return undefined;
  if (now - hit.at > TTL_MS) {
    entries.delete(key);
    return undefined;
  }
  // Refresh recency for LRU eviction.
  entries.delete(key);
  entries.set(key, hit);
  return hit.data as T;
}

export function setCachedPanelData(key: string, data: unknown, now = Date.now()): void {
  entries.delete(key);
  entries.set(key, { data, at: now });
  while (entries.size > MAX_ENTRIES) {
    const oldest = entries.keys().next().value as string;
    entries.delete(oldest);
  }
}

export function clearPanelDataCache(): void {
  entries.clear();
}
