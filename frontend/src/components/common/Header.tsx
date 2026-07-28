import { useState } from 'react';
import { Link } from 'react-router-dom';
import {
  LogOut,
  User,
  Settings2,
  LayoutDashboard,
  Shield,
  Sun,
  Moon,
  Menu,
  X,
  Languages,
  Table,
  Edit,
  CheckSquare,
  Target,
  FileInput,
  AlertTriangle,
  Activity,
  Sparkles,
  GitBranch,
  Brain,
} from 'lucide-react';
import { useAuthStore, useCan } from '../../store/authStore';
import { usePanelStore } from '../../store/panelStore';
import { useVersionStore } from '../../store/versionStore';
import { useThemeStore } from '../../store/themeStore';
import { useI18n } from '../../i18n/useI18n';
import type { Locale, MessageKey } from '../../i18n';

type NavItem =
  | { kind: 'link'; to: string; labelKey: MessageKey; icon: typeof LayoutDashboard; show: boolean }
  | {
      kind: 'panel';
      panel: string;
      labelKey: MessageKey;
      icon: typeof Table;
      show: boolean;
    };

export function Header() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const openPanel = usePanelStore((s) => s.openPanel);
  const versions = useVersionStore((s) => s.versions);
  const activeVersionId = useVersionStore((s) => s.activeVersionId);
  const activeScenario = useVersionStore((s) => s.activeScenario);
  const setActiveVersionId = useVersionStore((s) => s.setActiveVersionId);
  const setActiveScenario = useVersionStore((s) => s.setActiveScenario);
  const scenarios = Array.from(
    new Set(versions.map((v) => v.scenario || 'base')),
  ).sort();
  const versionsInScenario = versions.filter(
    (v) => (v.scenario || 'base') === activeScenario,
  );
  const theme = useThemeStore((s) => s.theme);
  const toggleTheme = useThemeStore((s) => s.toggleTheme);
  const { t, locale, setLocale } = useI18n();
  const [menuOpen, setMenuOpen] = useState(false);

  const canGenerate = useCan('can_generate');
  const canOverride = useCan('can_override');
  const canReview = useCan('can_review');
  const canInput = useCan('can_input');
  const canPublish = useCan('can_publish');
  const canAdmin = useCan('can_admin');
  const canManageDrivers = useCan('can_manage_drivers');

  const cycleLocale = () => {
    const next: Locale = locale === 'en' ? 'es' : 'en';
    setLocale(next);
  };

  const items: NavItem[] = [
    {
      kind: 'link',
      to: '/executive',
      labelKey: 'nav.executive',
      icon: LayoutDashboard,
      show: true,
    },
    {
      kind: 'panel',
      panel: 'forecast_table',
      labelKey: 'nav.forecast',
      icon: Table,
      show: canGenerate || canReview,
    },
    {
      kind: 'panel',
      panel: 'review_dashboard',
      labelKey: 'nav.review',
      icon: Shield,
      show: canReview,
    },
    {
      kind: 'panel',
      panel: 'overrides',
      labelKey: 'nav.overrides',
      icon: Edit,
      show: canOverride,
    },
    {
      kind: 'panel',
      panel: 'approvals',
      labelKey: 'nav.approvals',
      icon: CheckSquare,
      show: canReview || canPublish,
    },
    {
      kind: 'panel',
      panel: 'accuracy_tracking',
      labelKey: 'nav.accuracy',
      icon: Target,
      show: canReview || canGenerate,
    },
    {
      kind: 'panel',
      panel: 'driver_inputs',
      labelKey: 'nav.drivers',
      icon: FileInput,
      show: canInput,
    },
    {
      kind: 'panel',
      panel: 'drivers',
      labelKey: 'nav.causalDrivers',
      icon: Activity,
      show: canManageDrivers || canAdmin || user?.role_name === 'admin',
    },
    {
      kind: 'panel',
      panel: 'explainability',
      labelKey: 'nav.explain',
      icon: Sparkles,
      show: canReview || canGenerate,
    },
    {
      kind: 'panel',
      panel: 'what_if',
      labelKey: 'nav.whatIf',
      icon: GitBranch,
      show: canGenerate,
    },
    {
      kind: 'panel',
      panel: 'anomaly_dashboard',
      labelKey: 'nav.anomalies',
      icon: AlertTriangle,
      show: canReview,
    },
    {
      kind: 'panel',
      panel: 'heuristics',
      labelKey: 'nav.heuristics',
      icon: Brain,
      show: canReview || canAdmin,
    },
    {
      kind: 'link',
      to: '/admin',
      labelKey: 'nav.admin',
      icon: Shield,
      show: canAdmin || user?.role_name === 'admin',
    },
    {
      kind: 'panel',
      panel: 'skill_editor',
      labelKey: 'nav.skills',
      icon: Settings2,
      show: canAdmin || user?.role_name === 'admin',
    },
  ];

  const navClass =
    'flex items-center gap-1.5 px-3 py-2 text-xs bg-surface-800/60 hover:bg-deloitte-green/10 border border-surface-700/50 hover:border-deloitte-green/25 text-surface-400 hover:text-deloitte-green rounded-lg transition-all whitespace-nowrap';

  const navLinks = (
    <>
      {items
        .filter((i) => i.show)
        .map((item) =>
          item.kind === 'link' ? (
            <Link
              key={item.to + item.labelKey}
              to={item.to}
              onClick={() => setMenuOpen(false)}
              className={navClass}
              title={t(item.labelKey)}
            >
              <item.icon className="w-3.5 h-3.5" />
              <span>{t(item.labelKey)}</span>
            </Link>
          ) : (
            <button
              key={item.panel}
              type="button"
              onClick={() => {
                openPanel(item.panel, {});
                setMenuOpen(false);
              }}
              className={navClass}
              title={t(item.labelKey)}
            >
              <item.icon className="w-3.5 h-3.5" />
              <span>{t(item.labelKey)}</span>
            </button>
          ),
        )}
    </>
  );

  return (
    <header className="h-14 flex items-center justify-between px-3 sm:px-6 border-b border-surface-700/50 bg-black/60 backdrop-blur-md rf-elevated relative z-40">
      <div className="flex items-center gap-3 min-w-0">
        <Link to="/" className="flex items-center gap-2.5 min-w-0" onClick={() => setMenuOpen(false)}>
          <div className="w-1 h-7 bg-deloitte-green rounded-full shrink-0" />
          <div className="flex flex-col min-w-0">
            <span className="text-sm font-bold text-white tracking-wide leading-none truncate">
              {t('app.title')}
            </span>
            <span className="text-xs text-deloitte-green font-semibold tracking-widest uppercase leading-tight">
              {t('app.brand')}
            </span>
          </div>
        </Link>
        {versions.length > 0 && (
          <div className="hidden lg:flex items-center gap-1.5">
            {scenarios.length > 1 && (
              <select
                value={activeScenario}
                onChange={(e) => setActiveScenario(e.target.value)}
                className="max-w-[7rem] text-xs bg-surface-800 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-300 focus:outline-none focus:border-deloitte-green/40"
                aria-label="Scenario"
                title="Scenario"
              >
                {scenarios.map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
            )}
            <select
              value={activeVersionId || ''}
              onChange={(e) => setActiveVersionId(e.target.value || null)}
              className="max-w-[12rem] text-xs bg-surface-800 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-300 focus:outline-none focus:border-deloitte-green/40"
              aria-label={t('nav.version')}
              title={t('nav.version')}
            >
              {(versionsInScenario.length ? versionsInScenario : versions).map((v) => (
                <option key={v.id} value={v.id}>
                  {v.label || v.name}
                </option>
              ))}
            </select>
          </div>
        )}
      </div>

      <div className="flex items-center gap-1.5 sm:gap-3">
        <button
          type="button"
          onClick={cycleLocale}
          className="p-2 hover:bg-surface-800 rounded-lg transition-colors text-surface-400 hover:text-white"
          title={`${t('locale.label')}: ${t(locale === 'en' ? 'locale.en' : 'locale.es')}`}
          aria-label={t('locale.label')}
        >
          <Languages className="w-4 h-4" />
        </button>
        <button
          type="button"
          onClick={toggleTheme}
          className="p-2 hover:bg-surface-800 rounded-lg transition-colors text-surface-400 hover:text-white"
          title={t('theme.toggle')}
          aria-label={t('theme.toggle')}
        >
          {theme === 'dark' ? <Sun className="w-4 h-4" /> : <Moon className="w-4 h-4" />}
        </button>

        <div className="hidden sm:flex items-center gap-1.5 overflow-x-auto max-w-[50vw]">
          {navLinks}
        </div>

        {user && (
          <div className="hidden md:flex items-center gap-2 text-sm text-surface-400">
            <User className="w-4 h-4" />
            <span className="text-surface-300 max-w-[8rem] truncate">{user.full_name || user.username}</span>
            <span className="px-2 py-0.5 bg-deloitte-green/15 text-deloitte-green rounded-full text-xs font-semibold">
              {user.role_name}
            </span>
          </div>
        )}
        <button
          type="button"
          onClick={logout}
          className="p-2 hover:bg-surface-800 rounded-lg transition-colors text-surface-400 hover:text-white"
          title={t('nav.signOut')}
          aria-label={t('nav.signOut')}
        >
          <LogOut className="w-4 h-4" />
        </button>
        <button
          type="button"
          className="sm:hidden p-2 hover:bg-surface-800 rounded-lg text-surface-400 hover:text-white min-h-[44px] min-w-[44px] flex items-center justify-center"
          aria-label={menuOpen ? t('nav.closeMenu') : t('nav.menu')}
          aria-expanded={menuOpen}
          onClick={() => setMenuOpen((o) => !o)}
        >
          {menuOpen ? <X className="w-5 h-5" /> : <Menu className="w-5 h-5" />}
        </button>
      </div>

      {menuOpen && (
        <div className="sm:hidden absolute top-14 inset-x-0 border-b border-surface-700/50 bg-surface-900/95 backdrop-blur-md p-3 flex flex-col gap-2 shadow-xl max-h-[70vh] overflow-y-auto">
          {versions.length > 0 && (
            <select
              value={activeVersionId || ''}
              onChange={(e) => setActiveVersionId(e.target.value || null)}
              className="w-full text-xs bg-surface-800 border border-surface-700 rounded-lg px-2 py-2 text-surface-300"
              aria-label={t('nav.version')}
            >
              {versions.map((v) => (
                <option key={v.id} value={v.id}>
                  {v.label || v.name}
                </option>
              ))}
            </select>
          )}
          {navLinks}
          {user && (
            <div className="flex items-center gap-2 text-sm text-surface-400 px-2 py-1">
              <User className="w-4 h-4" />
              <span className="text-surface-300 truncate">{user.full_name || user.username}</span>
              <span className="px-2 py-0.5 bg-deloitte-green/15 text-deloitte-green rounded-full text-xs font-semibold">
                {user.role_name}
              </span>
            </div>
          )}
        </div>
      )}
    </header>
  );
}
