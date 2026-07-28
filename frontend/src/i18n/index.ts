/** i18n — English + Spanish; extend locales as needed. */

export type Locale = 'en' | 'es';

const en = {
  'app.title': 'Rolling Forecast',
  'app.brand': 'Deloitte',
  'app.tagline': 'AI-Powered FP&A',
  'app.empty.subtitle':
    'Generate statistical forecasts, analyze variances, manage overrides, and answer questions about your financial data through natural conversation.',
  'nav.executive': 'Executive',
  'nav.forecast': 'Forecast',
  'nav.review': 'Review',
  'nav.overrides': 'Overrides',
  'nav.approvals': 'Approvals',
  'nav.accuracy': 'Accuracy',
  'nav.drivers': 'Drivers',
  'nav.causalDrivers': 'Driver Series',
  'nav.explain': 'Explain',
  'nav.whatIf': 'What-if',
  'nav.anomalies': 'Anomalies',
  'nav.admin': 'Admin',
  'nav.skills': 'Skills',
  'nav.version': 'Active forecast version',
  'nav.signOut': 'Sign out',
  'nav.menu': 'Open navigation menu',
  'nav.closeMenu': 'Close navigation menu',
  'chat.stop': 'Stop generating',
  'theme.toggle': 'Toggle light / dark theme',
  'theme.light': 'Light',
  'theme.dark': 'Dark',
  'locale.label': 'Language',
  'locale.en': 'English',
  'locale.es': 'Español',
  'chat.placeholder': 'Ask about forecasts, drivers, or overrides…',
  'chat.composer': 'Message composer',
  'chat.send': 'Send message',
  'chat.upload': 'Upload file',
  'chat.disclaimer': 'AI-generated forecasts require human review before publication',
  'chat.suggestion.generate': 'Generate a baseline forecast',
  'chat.suggestion.review': 'Review forecast confidence & risks',
  'chat.suggestion.drivers': 'Collect driver inputs from BUs',
  'chat.suggestion.compare': 'Compare current vs prior forecast',
  'chat.regenerate': 'Regenerate',
  'sidebar.conversations': 'Conversations',
  'sidebar.chats': 'Chats',
  'sidebar.expand': 'Expand sidebar',
  'sidebar.collapse': 'Collapse sidebar',
  'sidebar.new': 'New conversation',
  'sidebar.empty': 'No conversations yet',
  'sidebar.loading': 'Loading…',
  'sidebar.loadError': 'Could not load conversations',
  'sidebar.deleteError': 'Could not delete conversation',
  'sidebar.deleteConfirm': 'Delete “{title}”? This cannot be undone.',
  'sidebar.untitled': 'Untitled',
  'sidebar.messages': '{count} messages',
  'panel.close': 'Close panel',
  'panel.widen': 'Widen panel',
  'panel.narrow': 'Narrow panel',
  'table.exportCsv': 'Export CSV',
  'table.exportXlsx': 'Export Excel',
  'table.title': 'Table',
} as const;

type MessageDict = { [K in keyof typeof en]: string };

const es: MessageDict = {
  'app.title': 'Pronóstico Continuo',
  'app.brand': 'Deloitte',
  'app.tagline': 'FP&A impulsado por IA',
  'app.empty.subtitle':
    'Genere pronósticos estadísticos, analice variaciones, gestione ajustes y consulte sus datos financieros en lenguaje natural.',
  'nav.executive': 'Ejecutivo',
  'nav.forecast': 'Pronóstico',
  'nav.review': 'Revisión',
  'nav.overrides': 'Ajustes',
  'nav.approvals': 'Aprobaciones',
  'nav.accuracy': 'Precisión',
  'nav.drivers': 'Drivers',
  'nav.causalDrivers': 'Series de drivers',
  'nav.explain': 'Explicar',
  'nav.whatIf': 'What-if',
  'nav.anomalies': 'Anomalías',
  'nav.admin': 'Admin',
  'nav.skills': 'Skills',
  'nav.version': 'Versión de pronóstico activa',
  'nav.signOut': 'Cerrar sesión',
  'nav.menu': 'Abrir menú de navegación',
  'nav.closeMenu': 'Cerrar menú de navegación',
  'chat.stop': 'Detener generación',
  'theme.toggle': 'Cambiar tema claro / oscuro',
  'theme.light': 'Claro',
  'theme.dark': 'Oscuro',
  'locale.label': 'Idioma',
  'locale.en': 'English',
  'locale.es': 'Español',
  'chat.placeholder': 'Pregunte sobre pronósticos, drivers o ajustes…',
  'chat.composer': 'Compositor de mensajes',
  'chat.send': 'Enviar mensaje',
  'chat.upload': 'Subir archivo',
  'chat.disclaimer': 'Los pronósticos generados por IA requieren revisión humana antes de publicarse',
  'chat.suggestion.generate': 'Generar un pronóstico base',
  'chat.suggestion.review': 'Revisar confianza y riesgos del pronóstico',
  'chat.suggestion.drivers': 'Recopilar inputs de drivers por BU',
  'chat.suggestion.compare': 'Comparar pronóstico actual vs anterior',
  'chat.regenerate': 'Regenerar',
  'sidebar.conversations': 'Conversaciones',
  'sidebar.chats': 'Chats',
  'sidebar.expand': 'Expandir barra lateral',
  'sidebar.collapse': 'Contraer barra lateral',
  'sidebar.new': 'Nueva conversación',
  'sidebar.empty': 'Aún no hay conversaciones',
  'sidebar.loading': 'Cargando…',
  'sidebar.loadError': 'No se pudieron cargar las conversaciones',
  'sidebar.deleteError': 'No se pudo eliminar la conversación',
  'sidebar.deleteConfirm': '¿Eliminar “{title}”? Esta acción no se puede deshacer.',
  'sidebar.untitled': 'Sin título',
  'sidebar.messages': '{count} mensajes',
  'panel.close': 'Cerrar panel',
  'panel.widen': 'Ampliar panel',
  'panel.narrow': 'Estrechar panel',
  'table.exportCsv': 'Exportar CSV',
  'table.exportXlsx': 'Exportar Excel',
  'table.title': 'Tabla',
};

const messages: Record<Locale, MessageDict> = { en, es };

export type MessageKey = keyof typeof en;

const STORAGE_KEY = 'rf_locale';

function readLocale(): Locale {
  if (typeof localStorage === 'undefined') return 'en';
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored === 'en' || stored === 'es') return stored;
  return 'en';
}

let currentLocale: Locale = readLocale();

type Listener = (locale: Locale) => void;
const listeners = new Set<Listener>();

function notify() {
  listeners.forEach((l) => l(currentLocale));
}

export function getLocale(): Locale {
  return currentLocale;
}

export function setLocale(locale: Locale) {
  currentLocale = locale;
  if (typeof localStorage !== 'undefined') {
    localStorage.setItem(STORAGE_KEY, locale);
  }
  if (typeof document !== 'undefined') {
    document.documentElement.lang = locale;
  }
  notify();
}

export function subscribeLocale(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function t(key: MessageKey, vars?: Record<string, string | number>, fallback?: string): string {
  let text = messages[currentLocale][key] ?? messages.en[key] ?? fallback ?? key;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      text = text.replace(`{${k}}`, String(v));
    }
  }
  return text;
}

/** Hook-friendly accessor that re-renders when locale changes (via subscribe). */
export function useT(): typeof t {
  return t;
}
