import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { ArrowLeft, Users, Shield, ListTree, ScrollText, DollarSign } from 'lucide-react';
import { apiGet, apiPost, apiPut, uploadFile } from '../../api/client';
import type { User } from '../../types/auth';

type Tab = 'users' | 'roles' | 'coa' | 'audit' | 'fx';

interface FxRate {
  id: string;
  from_currency: string;
  to_currency: string;
  period: string;
  rate: number;
  rate_type: string;
}

export function AdminPage() {
  const [tab, setTab] = useState<Tab>('users');
  const [users, setUsers] = useState<User[]>([]);
  const [roles, setRoles] = useState<any[]>([]);
  const [coa, setCoa] = useState<any>(null);
  const [audit, setAudit] = useState<any>(null);
  const [fxRates, setFxRates] = useState<FxRate[]>([]);
  const [reportingCurrency, setReportingCurrency] = useState('USD');
  const [fxForm, setFxForm] = useState({
    from_currency: 'EUR',
    to_currency: 'USD',
    period: new Date().toISOString().slice(0, 7),
    rate: '1.0',
    rate_type: 'average',
  });
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');

  const load = async (t: Tab) => {
    setError('');
    try {
      if (t === 'users') setUsers(await apiGet('/admin/users'));
      if (t === 'roles') setRoles(await apiGet('/admin/roles'));
      if (t === 'coa') setCoa(await apiGet('/admin/coa'));
      if (t === 'audit') setAudit(await apiGet('/admin/audit?limit=50'));
      if (t === 'fx') {
        const [rates, currency] = await Promise.all([
          apiGet<FxRate[]>('/admin/fx/rates'),
          apiGet<{ reporting_currency: string }>('/admin/fx/settings/reporting_currency'),
        ]);
        setFxRates(rates);
        setReportingCurrency(currency.reporting_currency);
      }
    } catch (e: any) {
      setError(e.message || 'Failed to load');
    }
  };

  useEffect(() => {
    load(tab);
  }, [tab]);

  const wireCoa = async () => {
    try {
      const res = await apiPost<any>('/admin/coa/wire-standard');
      setMessage(`Wired ${res.dependencies_created} dependencies`);
      load('coa');
    } catch (e: any) {
      setError(e.message);
    }
  };

  const saveReportingCurrency = async () => {
    try {
      const res = await apiPut<{ reporting_currency: string }>(
        '/admin/fx/settings/reporting_currency',
        { currency: reportingCurrency },
      );
      setReportingCurrency(res.reporting_currency);
      setMessage(`Reporting currency set to ${res.reporting_currency}`);
    } catch (e: any) {
      setError(e.message);
    }
  };

  const createFxRate = async () => {
    try {
      await apiPost('/admin/fx/rates', {
        ...fxForm,
        from_currency: fxForm.from_currency.toUpperCase(),
        to_currency: fxForm.to_currency.toUpperCase(),
        rate: Number(fxForm.rate),
      });
      setMessage('FX rate created');
      load('fx');
    } catch (e: any) {
      setError(e.message);
    }
  };

  const uploadFxCsv = async (file: File) => {
    try {
      const res = await uploadFile('/admin/fx/rates/upload', file);
      setMessage(`Uploaded FX rates: ${res.created} created, ${res.updated} updated`);
      load('fx');
    } catch (e: any) {
      setError(e.message);
    }
  };

  const tabs: { id: Tab; label: string; icon: typeof Users }[] = [
    { id: 'users', label: 'Users', icon: Users },
    { id: 'roles', label: 'Roles', icon: Shield },
    { id: 'coa', label: 'Chart of Accounts', icon: ListTree },
    { id: 'fx', label: 'FX Rates', icon: DollarSign },
    { id: 'audit', label: 'Audit Log', icon: ScrollText },
  ];

  return (
    <div className="min-h-screen bg-black text-white">
      <header className="h-14 flex items-center gap-4 px-6 border-b border-surface-700/50">
        <Link to="/" className="text-surface-400 hover:text-white flex items-center gap-1 text-sm">
          <ArrowLeft className="w-4 h-4" /> Back
        </Link>
        <span className="font-semibold">Admin Console</span>
      </header>

      <div className="max-w-5xl mx-auto px-6 py-6">
        <div className="flex gap-2 mb-6 flex-wrap">
          {tabs.map((t) => (
            <button
              key={t.id}
              onClick={() => setTab(t.id)}
              className={`flex items-center gap-1.5 px-3 py-1.5 text-xs rounded-lg border ${
                tab === t.id
                  ? 'bg-deloitte-green/20 border-deloitte-green/40 text-deloitte-green'
                  : 'bg-surface-900 border-surface-700 text-surface-400'
              }`}
            >
              <t.icon className="w-3.5 h-3.5" /> {t.label}
            </button>
          ))}
        </div>

        {error && <p className="text-red-400 text-sm mb-4">{error}</p>}
        {message && <p className="text-deloitte-green text-sm mb-4">{message}</p>}

        {tab === 'users' && (
          <table className="w-full text-sm">
            <thead className="text-surface-500 text-left">
              <tr>
                <th className="py-2">Username</th>
                <th>Email</th>
                <th>Role</th>
                <th>BU</th>
                <th>Active</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="border-t border-surface-800">
                  <td className="py-2">{u.username}</td>
                  <td className="text-surface-400">{u.email}</td>
                  <td>{u.role_name}</td>
                  <td className="text-surface-400">{u.business_unit || '—'}</td>
                  <td>{u.is_active ? 'Yes' : 'No'}</td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {tab === 'roles' && (
          <div className="space-y-3">
            {roles.map((r) => (
              <div key={r.id} className="bg-surface-900 border border-surface-700/50 rounded-lg p-4">
                <div className="font-semibold mb-1">{r.name}</div>
                <p className="text-xs text-surface-500 mb-2">{r.description}</p>
                <div className="flex flex-wrap gap-2 text-[10px]">
                  {['can_input', 'can_generate', 'can_override', 'can_review', 'can_publish', 'can_admin'].map(
                    (k) =>
                      r[k] && (
                        <span key={k} className="px-2 py-0.5 bg-deloitte-green/15 text-deloitte-green rounded">
                          {k.replace('can_', '')}
                        </span>
                      )
                  )}
                </div>
              </div>
            ))}
          </div>
        )}

        {tab === 'coa' && coa && (
          <div className="space-y-4">
            <button
              onClick={wireCoa}
              className="px-3 py-1.5 text-xs bg-deloitte-green text-black font-semibold rounded-lg"
            >
              Wire standard P&L dependencies
            </button>
            <p className="text-xs text-surface-500">
              {coa.line_items?.length || 0} line items · {coa.dependencies?.length || 0} dependencies
            </p>
            <div className="max-h-96 overflow-auto border border-surface-700/40 rounded-xl">
              <table className="w-full text-xs">
                <thead className="bg-surface-900 text-surface-500 sticky top-0">
                  <tr>
                    <th className="px-3 py-2 text-left">Code</th>
                    <th className="px-3 py-2 text-left">Name</th>
                    <th className="px-3 py-2 text-left">Category</th>
                    <th className="px-3 py-2 text-left">Calculated</th>
                  </tr>
                </thead>
                <tbody>
                  {(coa.line_items || []).map((li: any) => (
                    <tr key={li.id} className="border-t border-surface-800">
                      <td className="px-3 py-1.5">{li.account_code}</td>
                      <td className="px-3 py-1.5">{li.name}</td>
                      <td className="px-3 py-1.5 text-surface-400">{li.category}</td>
                      <td className="px-3 py-1.5">{li.is_calculated ? 'Yes' : ''}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {tab === 'fx' && (
          <div className="space-y-6">
            <div className="bg-surface-900 border border-surface-700/50 rounded-xl p-4 space-y-3">
              <h3 className="text-sm font-semibold">Reporting currency</h3>
              <div className="flex gap-2 items-center">
                <input
                  value={reportingCurrency}
                  onChange={(e) => setReportingCurrency(e.target.value.toUpperCase())}
                  maxLength={3}
                  className="w-24 bg-surface-800 border border-surface-600 rounded-lg px-3 py-1.5 text-sm"
                />
                <button
                  type="button"
                  onClick={saveReportingCurrency}
                  className="px-3 py-1.5 text-xs bg-deloitte-green text-black font-semibold rounded-lg"
                >
                  Save
                </button>
              </div>
            </div>

            <div className="bg-surface-900 border border-surface-700/50 rounded-xl p-4 space-y-3">
              <h3 className="text-sm font-semibold">Add rate</h3>
              <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
                {(['from_currency', 'to_currency', 'period', 'rate', 'rate_type'] as const).map((k) => (
                  <input
                    key={k}
                    value={fxForm[k]}
                    onChange={(e) => setFxForm({ ...fxForm, [k]: e.target.value })}
                    placeholder={k}
                    className="bg-surface-800 border border-surface-600 rounded-lg px-2 py-1.5 text-xs"
                  />
                ))}
              </div>
              <button
                type="button"
                onClick={createFxRate}
                className="px-3 py-1.5 text-xs bg-deloitte-green text-black font-semibold rounded-lg"
              >
                Create rate
              </button>
            </div>

            <div className="bg-surface-900 border border-surface-700/50 rounded-xl p-4 space-y-2">
              <h3 className="text-sm font-semibold">Upload CSV</h3>
              <p className="text-[11px] text-surface-500">
                Columns: from_currency,to_currency,period,rate[,rate_type]
              </p>
              <input
                type="file"
                accept=".csv,text/csv"
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) uploadFxCsv(f);
                }}
                className="text-xs text-surface-400"
              />
            </div>

            <div className="max-h-96 overflow-auto border border-surface-700/40 rounded-xl">
              <table className="w-full text-xs">
                <thead className="bg-surface-900 text-surface-500 sticky top-0">
                  <tr>
                    <th className="px-3 py-2 text-left">From</th>
                    <th className="px-3 py-2 text-left">To</th>
                    <th className="px-3 py-2 text-left">Period</th>
                    <th className="px-3 py-2 text-left">Rate</th>
                    <th className="px-3 py-2 text-left">Type</th>
                  </tr>
                </thead>
                <tbody>
                  {fxRates.map((r) => (
                    <tr key={r.id} className="border-t border-surface-800">
                      <td className="px-3 py-1.5">{r.from_currency}</td>
                      <td className="px-3 py-1.5">{r.to_currency}</td>
                      <td className="px-3 py-1.5">{r.period}</td>
                      <td className="px-3 py-1.5">{r.rate}</td>
                      <td className="px-3 py-1.5 text-surface-400">{r.rate_type}</td>
                    </tr>
                  ))}
                  {!fxRates.length && (
                    <tr>
                      <td colSpan={5} className="px-3 py-4 text-surface-500 text-center">
                        No FX rates yet
                      </td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </div>
        )}

        {tab === 'audit' && audit && (
          <div className="space-y-2 max-h-[70vh] overflow-auto">
            {(audit.events || []).map((e: any) => (
              <div key={e.id} className="bg-surface-900 border border-surface-700/40 rounded-lg px-3 py-2 text-xs">
                <div className="flex justify-between gap-2">
                  <span className="text-deloitte-green font-mono">{e.action}</span>
                  <span className="text-surface-500">{e.timestamp}</span>
                </div>
                <div className="text-surface-400 mt-0.5">
                  {e.actor_username || 'system'} · {e.entity_type}/{e.entity_id || '—'}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  );
}
