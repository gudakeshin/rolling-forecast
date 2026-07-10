/** Minimal i18n — English default; extend locales as needed. */

export type Locale = 'en';

const messages = {
  en: {
    'app.title': 'Rolling Forecast',
    'app.brand': 'Deloitte',
    'nav.executive': 'Executive',
    'nav.admin': 'Admin',
    'nav.skills': 'Skills',
    'nav.signOut': 'Sign out',
    'theme.toggle': 'Toggle light / dark theme',
    'theme.light': 'Light',
    'theme.dark': 'Dark',
    'chat.placeholder': 'Ask about forecasts, drivers, or overrides…',
    'sidebar.conversations': 'Conversations',
    'sidebar.expand': 'Expand sidebar',
    'sidebar.collapse': 'Collapse sidebar',
  },
} as const;

export type MessageKey = keyof typeof messages.en;

let currentLocale: Locale = 'en';

export function setLocale(locale: Locale) {
  currentLocale = locale;
}

export function t(key: MessageKey, fallback?: string): string {
  return messages[currentLocale][key] ?? fallback ?? key;
}

export function useT() {
  return t;
}
