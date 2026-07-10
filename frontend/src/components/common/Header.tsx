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
} from 'lucide-react';
import { useAuthStore } from '../../store/authStore';
import { usePanelStore } from '../../store/panelStore';
import { useThemeStore } from '../../store/themeStore';
import { useI18n } from '../../i18n/useI18n';
import type { Locale } from '../../i18n';

export function Header() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const openPanel = usePanelStore((s) => s.openPanel);
  const theme = useThemeStore((s) => s.theme);
  const toggleTheme = useThemeStore((s) => s.toggleTheme);
  const { t, locale, setLocale } = useI18n();
  const [menuOpen, setMenuOpen] = useState(false);

  const cycleLocale = () => {
    const next: Locale = locale === 'en' ? 'es' : 'en';
    setLocale(next);
  };

  const navLinks = (
    <>
      <Link
        to="/executive"
        onClick={() => setMenuOpen(false)}
        className="flex items-center gap-1.5 px-3 py-2 text-xs bg-surface-800/60 hover:bg-deloitte-green/10 border border-surface-700/50 hover:border-deloitte-green/25 text-surface-400 hover:text-deloitte-green rounded-lg transition-all"
        title={t('nav.executive')}
      >
        <LayoutDashboard className="w-3.5 h-3.5" />
        <span>{t('nav.executive')}</span>
      </Link>
      {user?.role_name === 'admin' && (
        <Link
          to="/admin"
          onClick={() => setMenuOpen(false)}
          className="flex items-center gap-1.5 px-3 py-2 text-xs bg-surface-800/60 hover:bg-deloitte-green/10 border border-surface-700/50 hover:border-deloitte-green/25 text-surface-400 hover:text-deloitte-green rounded-lg transition-all"
          title={t('nav.admin')}
        >
          <Shield className="w-3.5 h-3.5" />
          <span>{t('nav.admin')}</span>
        </Link>
      )}
      {user?.role_name === 'admin' && (
        <button
          type="button"
          onClick={() => {
            openPanel('skill_editor', {});
            setMenuOpen(false);
          }}
          className="flex items-center gap-1.5 px-3 py-2 text-xs bg-surface-800/60 hover:bg-deloitte-green/10 border border-surface-700/50 hover:border-deloitte-green/25 text-surface-400 hover:text-deloitte-green rounded-lg transition-all"
          title={t('nav.skills')}
        >
          <Settings2 className="w-3.5 h-3.5" />
          <span>{t('nav.skills')}</span>
        </button>
      )}
    </>
  );

  return (
    <header className="h-14 flex items-center justify-between px-3 sm:px-6 border-b border-surface-700/50 bg-black/60 backdrop-blur-md rf-elevated relative z-40">
      <div className="flex items-center gap-3 min-w-0">
        <div className="flex items-center gap-2.5 min-w-0">
          <div className="w-1 h-7 bg-deloitte-green rounded-full shrink-0" />
          <div className="flex flex-col min-w-0">
            <span className="text-sm font-bold text-white tracking-wide leading-none truncate">
              {t('app.title')}
            </span>
            <span className="text-xs text-deloitte-green font-semibold tracking-widest uppercase leading-tight">
              {t('app.brand')}
            </span>
          </div>
        </div>
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

        <div className="hidden sm:flex items-center gap-1.5">{navLinks}</div>

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
        <div className="sm:hidden absolute top-14 inset-x-0 border-b border-surface-700/50 bg-surface-900/95 backdrop-blur-md p-3 flex flex-col gap-2 shadow-xl">
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
