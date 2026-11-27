import {
  Building2, Cpu, DollarSign, ListTree, RefreshCw, ScrollText, Shield, Users,
} from 'lucide-react';
import { useState } from 'react';
import { rescoreAll } from '../../api/dashboard';
import { toast } from '../../store/toastStore';
import { AdminAuditTab } from './AdminAuditTab';
import { AdminCoaTab } from './AdminCoaTab';
import { AdminCompaniesTab } from './AdminCompaniesTab';
import { AdminFxTab } from './AdminFxTab';
import { AdminModelsTab } from './AdminModelsTab';
import { AdminRolesTab } from './AdminRolesTab';
import { AdminUsersTab } from './AdminUsersTab';

type Tab = 'users' | 'roles' | 'companies' | 'coa' | 'audit' | 'fx' | 'models';

/** Admin console — wraps user/role/CoA/FX/audit management inside the slide-over
 * panel shell instead of a standalone full-page route. Each tab is its own
 * component that loads its data when shown. */
export function AdminConsolePanel() {
  const [tab, setTab] = useState<Tab>('users');
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [rescoring, setRescoring] = useState(false);
  const tabProps = { onError: setError, onMessage: setMessage };

  const handleRescoreAll = async () => {
    setRescoring(true);
    setError('');
    try {
      const res = await rescoreAll();
      const msg = res.message || 'Rescore complete';
      setMessage(msg);
      toast.success(msg);
    } catch (e: any) {
      setError(e.message || 'Rescore failed');
      toast.error(e.message || 'Rescore failed');
    } finally {
      setRescoring(false);
    }
  };

  const tabs: { id: Tab; label: string; icon: typeof Users }[] = [
    { id: 'users', label: 'Users', icon: Users },
    { id: 'roles', label: 'Roles', icon: Shield },
    { id: 'companies', label: 'Companies', icon: Building2 },
    { id: 'coa', label: 'Chart of Accounts', icon: ListTree },
    { id: 'fx', label: 'FX Rates', icon: DollarSign },
    { id: 'models', label: 'Models', icon: Cpu },
    { id: 'audit', label: 'Audit Log', icon: ScrollText },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex gap-1.5 flex-wrap flex-1">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => {
                setError('');
                setTab(t.id);
              }}
              className={`flex items-center gap-1.5 px-2.5 py-1.5 text-xs rounded-lg border transition-colors ${
                tab === t.id
                  ? 'bg-deloitte-green/12 border-deloitte-green/30 text-deloitte-green font-semibold'
                  : 'bg-surface-900 border-surface-700 text-surface-400'
              }`}
            >
              <t.icon className="w-3.5 h-3.5" /> {t.label}
            </button>
          ))}
        </div>
        <button
          type="button"
          onClick={handleRescoreAll}
          disabled={rescoring}
          className="flex items-center gap-1.5 px-2.5 py-1.5 text-xs bg-surface-800 border border-surface-700 text-surface-300 rounded-lg hover:text-white disabled:opacity-50 shrink-0"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${rescoring ? 'animate-spin' : ''}`} />
          Rescore all
        </button>
      </div>

      {error && <p className="text-red-400 text-sm">{error}</p>}
      {message && <p className="text-deloitte-green text-sm">{message}</p>}

      {tab === 'users' && <AdminUsersTab {...tabProps} />}
      {tab === 'roles' && <AdminRolesTab {...tabProps} />}
      {tab === 'companies' && <AdminCompaniesTab {...tabProps} />}
      {tab === 'coa' && <AdminCoaTab {...tabProps} />}
      {tab === 'fx' && <AdminFxTab {...tabProps} />}
      {tab === 'models' && <AdminModelsTab {...tabProps} />}
      {tab === 'audit' && <AdminAuditTab {...tabProps} />}
    </div>
  );
}
