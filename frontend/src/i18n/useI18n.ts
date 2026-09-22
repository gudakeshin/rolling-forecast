import { useSyncExternalStore } from 'react';
import { getLocale, setLocale, subscribeLocale, t, type Locale, type MessageKey } from '../i18n';

/** Re-render on locale change. */
export function useI18n() {
  const locale = useSyncExternalStore(subscribeLocale, getLocale, () => 'en' as Locale);
  return {
    locale,
    setLocale,
    t: (key: MessageKey, vars?: Record<string, string | number>, fallback?: string) =>
      t(key, vars, fallback),
  };
}
