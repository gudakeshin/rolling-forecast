import { LogOut, Languages, Menu, X } from 'lucide-react';
import { useAuthStore } from '../../store/authStore';
import { useVersionStore } from '../../store/versionStore';
import { useChatStore } from '../../store/chatStore';
import { useI18n } from '../../i18n/useI18n';
import type { Locale } from '../../i18n';

export function Header() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const versions = useVersionStore((s) => s.versions);
  const activeVersionId = useVersionStore((s) => s.activeVersionId);
  const activeScenario = useVersionStore((s) => s.activeScenario);
  const setActiveVersionId = useVersionStore((s) => s.setActiveVersionId);
  const setActiveScenario = useVersionStore((s) => s.setActiveScenario);
  const sidebarExpanded = useChatStore((s) => s.sidebarExpanded);
  const toggleSidebar = useChatStore((s) => s.toggleSidebar);
  const { t, locale, setLocale } = useI18n();

  const scenarios = Array.from(new Set(versions.map((v) => v.scenario || 'base'))).sort();
  const versionsInScenario = versions.filter((v) => (v.scenario || 'base') === activeScenario);

  const cycleLocale = () => {
    const next: Locale = locale === 'en' ? 'es' : 'en';
    setLocale(next);
  };

  return (
    <header className="h-14 shrink-0 flex items-center justify-between px-3.5 border-b border-surface-700/60 bg-surface-800">
      <div className="flex items-center gap-3 min-w-0">
        <button
          type="button"
          className="lg:hidden p-2 -ml-1 rounded-lg text-surface-400 hover:text-deloitte-green hover:bg-surface-700/40 min-h-[40px] min-w-[40px] flex items-center justify-center"
          aria-label={sidebarExpanded ? t('nav.closeMenu') : t('nav.menu')}
          aria-expanded={sidebarExpanded}
          onClick={toggleSidebar}
        >
          {sidebarExpanded ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>

        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-7 h-7 rounded-lg shrink-0 bg-gradient-to-br from-deloitte-green to-deloitte-green-dark flex items-center justify-center">
            <svg width="14" height="14" viewBox="0 0 16 16" fill="none">
              <path d="M2 12L5 7L8 9L14 2" stroke="white" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
              <path d="M10 2H14V6" stroke="white" strokeWidth="1.6" strokeLinecap="round" strokeLinejoin="round" />
            </svg>
          </div>
          <div className="leading-tight min-w-0">
            <div className="text-[13px] font-bold text-white truncate">{t('app.title')}</div>
            <div className="text-[9.5px] font-semibold text-deloitte-green tracking-widest uppercase truncate">
              {t('app.brand')}
            </div>
          </div>
        </div>

        {versions.length > 0 && (
          <div className="hidden md:flex items-center gap-1.5 pl-1">
            <div className="w-px h-5 bg-surface-700 mx-1" />
            {scenarios.length > 1 && (
              <select
                value={activeScenario}
                onChange={(e) => setActiveScenario(e.target.value)}
                className="max-w-[7rem] text-[11.5px] font-medium bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-300 focus:outline-none focus:border-deloitte-green/50"
                aria-label="Scenario"
                title="Scenario"
              >
                {scenarios.map((s) => (
                  <option key={s} value={s}>{s}</option>
                ))}
              </select>
            )}
            <select
              value={activeVersionId || ''}
              onChange={(e) => setActiveVersionId(e.target.value || null)}
              className="max-w-[12rem] text-[11.5px] font-medium bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-300 focus:outline-none focus:border-deloitte-green/50"
              aria-label={t('nav.version')}
              title={t('nav.version')}
            >
              {(versionsInScenario.length ? versionsInScenario : versions).map((v) => (
                <option key={v.id} value={v.id}>{v.label || v.name}</option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="flex items-center gap-1 sm:gap-2.5">
        <button
          type="button"
          onClick={cycleLocale}
          className="p-2 rounded-lg text-surface-400 hover:text-deloitte-green hover:bg-surface-700/40 transition-colors"
          title={`${t('locale.label')}: ${t(locale === 'en' ? 'locale.en' : 'locale.es')}`}
          aria-label={t('locale.label')}
        >
          <Languages className="w-4 h-4" />
        </button>

        {user && (
          <div className="hidden sm:flex items-center gap-2 pl-1 pr-0.5">
            <div className="w-6 h-6 rounded-full bg-deloitte-green-dark text-white text-[10.5px] font-bold flex items-center justify-center shrink-0">
              {(user.full_name || user.username || '?').slice(0, 2).toUpperCase()}
            </div>
            <div className="leading-tight hidden md:block">
              <div className="text-[11.5px] font-semibold text-surface-200 max-w-[8rem] truncate">
                {user.full_name || user.username}
              </div>
              <div className="text-[9.5px] text-surface-500 truncate">{user.role_name}</div>
            </div>
          </div>
        )}
        <button
          type="button"
          onClick={logout}
          className="p-2 rounded-lg text-surface-400 hover:text-red-400 hover:bg-surface-700/40 transition-colors"
          title={t('nav.signOut')}
          aria-label={t('nav.signOut')}
        >
          <LogOut className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
}
