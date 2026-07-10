import { Link } from 'react-router-dom';
import { LogOut, User, Settings2, LayoutDashboard, Shield } from 'lucide-react';
import { useAuthStore } from '../../store/authStore';
import { usePanelStore } from '../../store/panelStore';

export function Header() {
  const user = useAuthStore((s) => s.user);
  const logout = useAuthStore((s) => s.logout);
  const openPanel = usePanelStore((s) => s.openPanel);

  return (
    <header className="h-14 flex items-center justify-between px-6 border-b border-surface-700/50 bg-black/60 backdrop-blur-md">
      <div className="flex items-center gap-3">
        <div className="flex items-center gap-2.5">
          <div className="w-1 h-7 bg-deloitte-green rounded-full" />
          <div className="flex flex-col">
            <span className="text-sm font-bold text-white tracking-wide leading-none">
              Rolling Forecast
            </span>
            <span className="text-xs text-deloitte-green font-semibold tracking-widest uppercase leading-tight">
              Deloitte
            </span>
          </div>
        </div>
      </div>

      <div className="flex items-center gap-3">
        <Link
          to="/executive"
          className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-surface-800/60 hover:bg-deloitte-green/10 border border-surface-700/50 hover:border-deloitte-green/25 text-surface-400 hover:text-deloitte-green rounded-lg transition-all"
          title="Executive View"
        >
          <LayoutDashboard className="w-3.5 h-3.5" />
          <span>Executive</span>
        </Link>
        {user?.role_name === 'admin' && (
          <Link
            to="/admin"
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-surface-800/60 hover:bg-deloitte-green/10 border border-surface-700/50 hover:border-deloitte-green/25 text-surface-400 hover:text-deloitte-green rounded-lg transition-all"
            title="Admin Console"
          >
            <Shield className="w-3.5 h-3.5" />
            <span>Admin</span>
          </Link>
        )}
        {user?.role_name === 'admin' && (
          <button
            onClick={() => openPanel('skill_editor', {})}
            className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-surface-800/60 hover:bg-deloitte-green/10 border border-surface-700/50 hover:border-deloitte-green/25 text-surface-400 hover:text-deloitte-green rounded-lg transition-all"
            title="Skill Editor"
          >
            <Settings2 className="w-3.5 h-3.5" />
            <span>Skills</span>
          </button>
        )}
        {user && (
          <div className="flex items-center gap-2 text-sm text-surface-400">
            <User className="w-4 h-4" />
            <span className="text-surface-300">{user.full_name || user.username}</span>
            <span className="px-2 py-0.5 bg-deloitte-green/15 text-deloitte-green rounded-full text-xs font-semibold">
              {user.role_name}
            </span>
          </div>
        )}
        <button
          onClick={logout}
          className="p-2 hover:bg-surface-800 rounded-lg transition-colors text-surface-400 hover:text-white"
          title="Sign out"
        >
          <LogOut className="w-4 h-4" />
        </button>
      </div>
    </header>
  );
}
