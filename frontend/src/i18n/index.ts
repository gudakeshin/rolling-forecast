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
  // Drivers panel
  'drivers.refresh': 'Refresh',
  'drivers.upload': 'Upload CSV/XLSX',
  'drivers.count': '{count} drivers',
  'drivers.createTitle': 'Create driver',
  'drivers.keyPlaceholder': 'key (e.g. headcount_na)',
  'drivers.namePlaceholder': 'display name',
  'drivers.unitPlaceholder': 'unit',
  'drivers.buPlaceholder': 'business unit',
  'drivers.create': 'Create',
  'drivers.creating': 'Creating…',
  'drivers.empty': 'No causal drivers yet. Create one or upload a CSV with columns driver_key, period, value.',
  'drivers.tableTitle': 'Causal drivers',
  'drivers.col.key': 'Key',
  'drivers.col.name': 'Name',
  'drivers.col.type': 'Type',
  'drivers.col.bu': 'BU',
  'drivers.col.freshness': 'Freshness',
  'drivers.freshness.noData': 'no data',
  'drivers.freshness.stale': '(stale)',
  'drivers.linksTitle': 'Links — {name}',
  'drivers.lineItemId': 'line item id',
  'drivers.lag': 'lag',
  'drivers.coefficient': 'coefficient (optional)',
  'drivers.assertLink': 'Assert link',
  'drivers.noLinks': 'No links for this driver.',
  'drivers.promote': 'Promote',
  'drivers.error.load': 'Failed to load drivers',
  'drivers.error.links': 'Failed to load links',
  'drivers.error.keyName': 'Key and name are required',
  'drivers.error.create': 'Create failed',
  'drivers.error.upload': 'Upload failed',
  'drivers.error.lineItem': 'Enter a valid line item id',
  'drivers.error.link': 'Link create failed',
  'drivers.error.promote': 'Promote failed',
  'drivers.success.created': 'Created driver {key}',
  'drivers.success.uploaded': 'Drivers uploaded',
  'drivers.success.link': 'Link created',
  'drivers.success.promote': 'Link promoted to active',
  // Explainability panel
  'explain.selectVersion': 'Select a forecast version to explain variance.',
  'explain.error.version': 'Select a forecast version first',
  'explain.error.bridge': 'Failed to load bridge',
  'explain.error.drill': 'Drilldown failed',
  'explain.convention.volume': 'Convention: volume first',
  'explain.convention.price': 'Convention: price first',
  'explain.periodFrom': 'period from',
  'explain.periodTo': 'period to',
  'explain.whatIf': 'What-if builder',
  'explain.refresh': 'Refresh',
  'explain.whyMoved': 'Why did {line} move?',
  'explain.thisLine': 'this line',
  'explain.method': 'Method',
  'explain.explained': 'Explained',
  'explain.derivedPrice':
    'Price is derived as L÷Q — it blends rate, mix, and discount effects; not measured ASP.',
  'explain.bridgeTitle': 'Budget bridge (attributed)',
  'explain.col.line': 'Line',
  'explain.col.prior': 'Δ Prior',
  'explain.col.budget': 'Δ Budget',
  'explain.col.method': 'Method',
  'explain.bucket.volume': 'Volume',
  'explain.bucket.price': 'Price',
  'explain.bucket.mix': 'Mix',
  'explain.bucket.fx': 'FX',
  'explain.bucket.constant_currency': 'Constant currency',
  'explain.bucket.reconciliation': 'Reconciliation',
  'explain.bucket.unattributed': 'Unattributed',
  // What-if panel
  'whatIf.title': 'What-if scenario',
  'whatIf.subtitle':
    'Shock causal drivers and clone the active forecast into a scenario version. Linked line items are perturbed via coefficients; P10/P90 stay on the base.',
  'whatIf.label': 'Scenario label',
  'whatIf.labelPlaceholder': 'e.g. headcount_down_10',
  'whatIf.shocks': 'Driver shocks',
  'whatIf.addShock': 'Add shock',
  'whatIf.selectDriver': 'Select driver…',
  'whatIf.mode.pct': '% change',
  'whatIf.mode.absolute': 'absolute Δ',
  'whatIf.mode.replace': 'replace',
  'whatIf.removeShock': 'Remove shock',
  'whatIf.run': 'Run what-if',
  'whatIf.created': 'Scenario created',
  'whatIf.version': 'Version',
  'whatIf.linePeriods': 'Line-periods perturbed: {count}',
  'whatIf.lineItems': 'Line items affected: {count}',
  'whatIf.openTable': 'Open scenario forecast table',
  'whatIf.emptyDrivers':
    'No drivers available. Create or upload drivers first, then link them to line items.',
  'whatIf.error.version': 'Select a base forecast version first',
  'whatIf.error.driver': 'Each shock needs a driver',
  'whatIf.error.value': 'Each shock needs a numeric value',
  'whatIf.error.label': 'Scenario label is required',
  'whatIf.error.run': 'What-if failed',
  'whatIf.success.created': 'Created scenario {label}',
  'panel.drivers': 'Driver Series',
  'panel.explainability': 'Explain Variance',
  'panel.whatIf': 'What-if Scenario',
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
  'drivers.refresh': 'Actualizar',
  'drivers.upload': 'Subir CSV/XLSX',
  'drivers.count': '{count} drivers',
  'drivers.createTitle': 'Crear driver',
  'drivers.keyPlaceholder': 'clave (p. ej. headcount_na)',
  'drivers.namePlaceholder': 'nombre visible',
  'drivers.unitPlaceholder': 'unidad',
  'drivers.buPlaceholder': 'unidad de negocio',
  'drivers.create': 'Crear',
  'drivers.creating': 'Creando…',
  'drivers.empty':
    'Aún no hay drivers causales. Cree uno o suba un CSV con columnas driver_key, period, value.',
  'drivers.tableTitle': 'Drivers causales',
  'drivers.col.key': 'Clave',
  'drivers.col.name': 'Nombre',
  'drivers.col.type': 'Tipo',
  'drivers.col.bu': 'BU',
  'drivers.col.freshness': 'Actualidad',
  'drivers.freshness.noData': 'sin datos',
  'drivers.freshness.stale': '(desactualizado)',
  'drivers.linksTitle': 'Vínculos — {name}',
  'drivers.lineItemId': 'id de partida',
  'drivers.lag': 'rezago',
  'drivers.coefficient': 'coeficiente (opcional)',
  'drivers.assertLink': 'Crear vínculo',
  'drivers.noLinks': 'No hay vínculos para este driver.',
  'drivers.promote': 'Promover',
  'drivers.error.load': 'No se pudieron cargar los drivers',
  'drivers.error.links': 'No se pudieron cargar los vínculos',
  'drivers.error.keyName': 'Clave y nombre son obligatorios',
  'drivers.error.create': 'Error al crear',
  'drivers.error.upload': 'Error al subir',
  'drivers.error.lineItem': 'Ingrese un id de partida válido',
  'drivers.error.link': 'Error al crear el vínculo',
  'drivers.error.promote': 'Error al promover',
  'drivers.success.created': 'Driver {key} creado',
  'drivers.success.uploaded': 'Drivers cargados',
  'drivers.success.link': 'Vínculo creado',
  'drivers.success.promote': 'Vínculo promovido a activo',
  'explain.selectVersion': 'Seleccione una versión de pronóstico para explicar la variación.',
  'explain.error.version': 'Seleccione primero una versión de pronóstico',
  'explain.error.bridge': 'No se pudo cargar el puente',
  'explain.error.drill': 'Error en el desglose',
  'explain.convention.volume': 'Convención: volumen primero',
  'explain.convention.price': 'Convención: precio primero',
  'explain.periodFrom': 'periodo desde',
  'explain.periodTo': 'periodo hasta',
  'explain.whatIf': 'Constructor what-if',
  'explain.refresh': 'Actualizar',
  'explain.whyMoved': '¿Por qué se movió {line}?',
  'explain.thisLine': 'esta partida',
  'explain.method': 'Método',
  'explain.explained': 'Explicado',
  'explain.derivedPrice':
    'El precio se deriva como L÷Q — mezcla tasa, mix y descuentos; no es ASP medido.',
  'explain.bridgeTitle': 'Puente presupuestario (atribuido)',
  'explain.col.line': 'Partida',
  'explain.col.prior': 'Δ Anterior',
  'explain.col.budget': 'Δ Presupuesto',
  'explain.col.method': 'Método',
  'explain.bucket.volume': 'Volumen',
  'explain.bucket.price': 'Precio',
  'explain.bucket.mix': 'Mix',
  'explain.bucket.fx': 'FX',
  'explain.bucket.constant_currency': 'Moneda constante',
  'explain.bucket.reconciliation': 'Reconciliación',
  'explain.bucket.unattributed': 'Sin atribuir',
  'whatIf.title': 'Escenario what-if',
  'whatIf.subtitle':
    'Aplique shocks a drivers causales y clone el pronóstico activo en una versión de escenario. Las partidas vinculadas se perturban vía coeficientes; P10/P90 permanecen en la base.',
  'whatIf.label': 'Etiqueta del escenario',
  'whatIf.labelPlaceholder': 'p. ej. headcount_down_10',
  'whatIf.shocks': 'Shocks de drivers',
  'whatIf.addShock': 'Agregar shock',
  'whatIf.selectDriver': 'Seleccionar driver…',
  'whatIf.mode.pct': '% cambio',
  'whatIf.mode.absolute': 'Δ absoluto',
  'whatIf.mode.replace': 'reemplazar',
  'whatIf.removeShock': 'Quitar shock',
  'whatIf.run': 'Ejecutar what-if',
  'whatIf.created': 'Escenario creado',
  'whatIf.version': 'Versión',
  'whatIf.linePeriods': 'Períodos-partida perturbados: {count}',
  'whatIf.lineItems': 'Partidas afectadas: {count}',
  'whatIf.openTable': 'Abrir tabla de pronóstico del escenario',
  'whatIf.emptyDrivers':
    'No hay drivers disponibles. Cree o suba drivers primero y vincúlelos a partidas.',
  'whatIf.error.version': 'Seleccione primero una versión base de pronóstico',
  'whatIf.error.driver': 'Cada shock necesita un driver',
  'whatIf.error.value': 'Cada shock necesita un valor numérico',
  'whatIf.error.label': 'La etiqueta del escenario es obligatoria',
  'whatIf.error.run': 'Error en what-if',
  'whatIf.success.created': 'Escenario {label} creado',
  'panel.drivers': 'Series de drivers',
  'panel.explainability': 'Explicar variación',
  'panel.whatIf': 'Escenario what-if',
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
