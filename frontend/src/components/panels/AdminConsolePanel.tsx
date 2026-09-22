import { useEffect, useState } from 'react';
import { Users, Shield, ListTree, ScrollText, DollarSign, RefreshCw, Cpu, Pencil, Trash2, Building2, Plus } from 'lucide-react';
import { apiGet, apiPatch, apiPost, apiPut, uploadFile } from '../../api/client';
import { rescoreAll } from '../../api/dashboard';
import {
  listModelPresets,
  getAvailableModels,
  createModelPreset,
  updateModelPreset,
  deactivateModelPreset,
  type ModelPreset,
  type AvailableModel,
} from '../../api/modelPresets';
import type { User } from '../../types/auth';
import { toast } from '../../store/toastStore';
import { confirmDialog } from '../../store/confirmStore';
import { useWorkspaceStore } from '../../store/workspaceStore';

type Tab = 'users' | 'roles' | 'companies' | 'coa' | 'audit' | 'fx' | 'models';

interface BusinessUnit {
  id: string;
  name: string;
}

const ROLE_PERMISSION_KEYS: { key: string; label: string }[] = [
  { key: 'can_input', label: 'Input drivers' },
  { key: 'can_generate', label: 'Generate forecasts' },
  { key: 'can_override', label: 'Override values' },
  { key: 'can_review', label: 'Review/approve' },
  { key: 'can_publish', label: 'Publish' },
  { key: 'can_admin', label: 'Admin' },
  { key: 'can_view_all_bus', label: 'View all companies' },
  { key: 'can_manage_drivers', label: 'Manage drivers' },
];

const emptyPresetForm = {
  name: '',
  description: '',
  model_type: 'auto',
  candidate_models: [] as string[],
  default_horizon_months: '',
};

interface FxRate {
  id: string;
  from_currency: string;
  to_currency: string;
  period: string;
  rate: number;
  rate_type: string;
}

/** Admin console — wraps user/role/CoA/FX/audit management inside the slide-over
 * panel shell instead of a standalone full-page route. */
export function AdminConsolePanel() {
  const hydrateWorkspace = useWorkspaceStore((s) => s.hydrate);
  const [tab, setTab] = useState<Tab>('users');
  const [users, setUsers] = useState<User[]>([]);
  const [roles, setRoles] = useState<any[]>([]);
  const [businessUnits, setBusinessUnits] = useState<BusinessUnit[]>([]);
  const [newCompanyName, setNewCompanyName] = useState('');
  const [creatingCompany, setCreatingCompany] = useState(false);
  const [editingUserId, setEditingUserId] = useState<string | null>(null);
  const [editRoleName, setEditRoleName] = useState('');
  const [editBusinessUnit, setEditBusinessUnit] = useState('');
  const [editActive, setEditActive] = useState(true);
  const [savingUserId, setSavingUserId] = useState<string | null>(null);
  const [savingRoleName, setSavingRoleName] = useState<string | null>(null);
  const [coa, setCoa] = useState<any>(null);
  const [audit, setAudit] = useState<any>(null);
  const [fxRates, setFxRates] = useState<FxRate[]>([]);
  const [reportingCurrency, setReportingCurrency] = useState('USD');
  const [fiscalCalendar, setFiscalCalendar] = useState({
    calendar_type: 'gregorian_month',
    fiscal_year_start_month: 2,
    week_start: 6,
  });
  const [fxForm, setFxForm] = useState({
    from_currency: 'EUR',
    to_currency: 'USD',
    period: new Date().toISOString().slice(0, 7),
    rate: '1.0',
    rate_type: 'average',
  });
  const [error, setError] = useState('');
  const [message, setMessage] = useState('');
  const [rescoring, setRescoring] = useState(false);
  const [modelPresets, setModelPresets] = useState<ModelPreset[]>([]);
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([]);
  const [presetForm, setPresetForm] = useState(emptyPresetForm);
  const [editingPresetId, setEditingPresetId] = useState<string | null>(null);
  const [savingPreset, setSavingPreset] = useState(false);

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

  const load = async (t: Tab) => {
    setError('');
    try {
      if (t === 'users') {
        const [userRows, roleRows, buRows] = await Promise.all([
          apiGet<User[]>('/admin/users'),
          apiGet<any[]>('/admin/roles'),
          apiGet<BusinessUnit[]>('/admin/business-units'),
        ]);
        setUsers(userRows);
        setRoles(roleRows);
        setBusinessUnits(buRows);
      }
      if (t === 'roles') setRoles(await apiGet('/admin/roles'));
      if (t === 'companies') setBusinessUnits(await apiGet('/admin/business-units'));
      if (t === 'coa') setCoa(await apiGet('/admin/coa'));
      if (t === 'audit') setAudit(await apiGet('/admin/audit?limit=50'));
      if (t === 'fx') {
        const [rates, currency, calendar] = await Promise.all([
          apiGet<FxRate[]>('/admin/fx/rates'),
          apiGet<{ reporting_currency: string }>('/admin/fx/settings/reporting_currency'),
          apiGet<{
            calendar_type: string;
            fiscal_year_start_month: number;
            week_start: number;
          }>('/admin/fx/settings/fiscal_calendar'),
        ]);
        setFxRates(rates);
        setReportingCurrency(currency.reporting_currency);
        setFiscalCalendar(calendar);
      }
      if (t === 'models') {
        const [presets, models] = await Promise.all([
          listModelPresets(true),
          getAvailableModels(),
        ]);
        setModelPresets(presets);
        setAvailableModels(models);
      }
    } catch (e: any) {
      setError(e.message || 'Failed to load');
    }
  };

  useEffect(() => {
    load(tab);
    // eslint-disable-next-line react-hooks/exhaustive-deps
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

  const saveFiscalCalendar = async () => {
    try {
      const res = await apiPut<typeof fiscalCalendar>(
        '/admin/fx/settings/fiscal_calendar',
        fiscalCalendar,
      );
      setFiscalCalendar(res);
      setMessage(`Fiscal calendar set to ${res.calendar_type}`);
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

  const createCompany = async () => {
    const name = newCompanyName.trim();
    if (!name) return;
    setCreatingCompany(true);
    setError('');
    try {
      await apiPost('/admin/business-units', { name });
      setNewCompanyName('');
      toast.success(`Created workspace "${name}"`);
      await load('companies');
      // The new company should show up in every workspace switcher right away.
      void hydrateWorkspace();
    } catch (e: any) {
      setError(e.message || 'Failed to create company');
      toast.error(e.message || 'Failed to create company');
    } finally {
      setCreatingCompany(false);
    }
  };

  const startEditUser = (u: User) => {
    setEditingUserId(u.id);
    setEditRoleName(u.role_name);
    setEditBusinessUnit(u.business_unit || '');
    setEditActive(u.is_active);
  };

  const cancelEditUser = () => setEditingUserId(null);

  const saveUser = async (u: User) => {
    setSavingUserId(u.id);
    setError('');
    try {
      await apiPatch(`/admin/users/${u.id}`, {
        role_name: editRoleName,
        business_unit: editBusinessUnit,
        is_active: editActive,
      });
      toast.success(`Updated ${u.username}`);
      setEditingUserId(null);
      await load('users');
      // A BU reassignment can affect who is a cross-company caller, and
      // switching a company off the roster of BUs a switcher should offer.
      void hydrateWorkspace();
    } catch (e: any) {
      setError(e.message || 'Failed to update user');
      toast.error(e.message || 'Failed to update user');
    } finally {
      setSavingUserId(null);
    }
  };

  const toggleRolePermission = (roleName: string, key: string) => {
    setRoles((prev) =>
      prev.map((r) => (r.name === roleName ? { ...r, [key]: !r[key] } : r)),
    );
  };

  const saveRole = async (role: any) => {
    setSavingRoleName(role.name);
    setError('');
    try {
      await apiPatch(`/admin/roles/${role.name}`, {
        can_input: role.can_input,
        can_generate: role.can_generate,
        can_override: role.can_override,
        can_review: role.can_review,
        can_publish: role.can_publish,
        can_admin: role.can_admin,
        can_view_all_bus: role.can_view_all_bus,
        can_manage_drivers: role.can_manage_drivers,
      });
      toast.success(`Updated role "${role.name}"`);
    } catch (e: any) {
      setError(e.message || 'Failed to update role');
      toast.error(e.message || 'Failed to update role');
    } finally {
      setSavingRoleName(null);
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
    { id: 'companies', label: 'Companies', icon: Building2 },
    { id: 'coa', label: 'Chart of Accounts', icon: ListTree },
    { id: 'fx', label: 'FX Rates', icon: DollarSign },
    { id: 'models', label: 'Models', icon: Cpu },
    { id: 'audit', label: 'Audit Log', icon: ScrollText },
  ];

  const startEditPreset = (p: ModelPreset) => {
    setEditingPresetId(p.id);
    setPresetForm({
      name: p.name,
      description: p.description || '',
      model_type: p.model_type,
      candidate_models: p.candidate_models || [],
      default_horizon_months: p.default_horizon_months ? String(p.default_horizon_months) : '',
    });
  };

  const cancelPresetEdit = () => {
    setEditingPresetId(null);
    setPresetForm(emptyPresetForm);
  };

  const savePreset = async () => {
    if (!presetForm.name.trim()) {
      setError('Preset name is required');
      return;
    }
    setSavingPreset(true);
    setError('');
    try {
      const body = {
        name: presetForm.name.trim(),
        description: presetForm.description.trim() || null,
        model_type: presetForm.model_type,
        candidate_models:
          presetForm.model_type === 'auto' && presetForm.candidate_models.length
            ? presetForm.candidate_models
            : null,
        default_horizon_months: presetForm.default_horizon_months
          ? Number(presetForm.default_horizon_months)
          : null,
      };
      if (editingPresetId) {
        await updateModelPreset(editingPresetId, body);
        toast.success(`Updated preset "${body.name}"`);
      } else {
        await createModelPreset(body);
        toast.success(`Created preset "${body.name}"`);
      }
      cancelPresetEdit();
      load('models');
    } catch (e: any) {
      setError(e.message || 'Failed to save model preset');
      toast.error(e.message || 'Failed to save model preset');
    } finally {
      setSavingPreset(false);
    }
  };

  const handleDeactivatePreset = async (p: ModelPreset) => {
    const ok = await confirmDialog(`Deactivate model preset "${p.name}"?`, {
      title: 'Deactivate preset',
      confirmLabel: 'Deactivate',
      danger: true,
    });
    if (!ok) return;
    try {
      await deactivateModelPreset(p.id);
      toast.success(`Deactivated "${p.name}"`);
      load('models');
    } catch (e: any) {
      toast.error(e.message || 'Failed to deactivate preset');
    }
  };

  const toggleCandidateModel = (name: string) => {
    setPresetForm((prev) => ({
      ...prev,
      candidate_models: prev.candidate_models.includes(name)
        ? prev.candidate_models.filter((m) => m !== name)
        : [...prev.candidate_models, name],
    }));
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        <div className="flex gap-1.5 flex-wrap flex-1">
          {tabs.map((t) => (
            <button
              key={t.id}
              type="button"
              onClick={() => setTab(t.id)}
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

      {tab === 'users' && (
        <table className="w-full text-sm">
          <thead className="text-surface-500 text-left">
            <tr>
              <th className="py-2">Username</th>
              <th>Email</th>
              <th>Role</th>
              <th>Company</th>
              <th>Active</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            {users.map((u) => {
              const editing = editingUserId === u.id;
              return (
                <tr key={u.id} className="border-t border-surface-700/40">
                  <td className="py-2">{u.username}</td>
                  <td className="text-surface-400">{u.email}</td>
                  <td>
                    {editing ? (
                      <select
                        value={editRoleName}
                        onChange={(e) => setEditRoleName(e.target.value)}
                        className="bg-surface-800 border border-surface-600 rounded-lg px-2 py-1 text-xs"
                      >
                        {roles.map((r) => (
                          <option key={r.name} value={r.name}>{r.name}</option>
                        ))}
                      </select>
                    ) : (
                      u.role_name
                    )}
                  </td>
                  <td className="text-surface-400">
                    {editing ? (
                      <select
                        value={editBusinessUnit}
                        onChange={(e) => setEditBusinessUnit(e.target.value)}
                        className="bg-surface-800 border border-surface-600 rounded-lg px-2 py-1 text-xs"
                      >
                        <option value="">— None —</option>
                        {businessUnits.map((bu) => (
                          <option key={bu.id} value={bu.name}>{bu.name}</option>
                        ))}
                      </select>
                    ) : (
                      u.business_unit || '—'
                    )}
                  </td>
                  <td>
                    {editing ? (
                      <input
                        type="checkbox"
                        checked={editActive}
                        onChange={(e) => setEditActive(e.target.checked)}
                        aria-label={`${u.username} active`}
                      />
                    ) : (
                      u.is_active ? 'Yes' : 'No'
                    )}
                  </td>
                  <td>
                    {editing ? (
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => saveUser(u)}
                          disabled={savingUserId === u.id}
                          className="text-xs px-2 py-1 bg-deloitte-green text-white font-semibold rounded-lg disabled:opacity-50"
                        >
                          Save
                        </button>
                        <button
                          type="button"
                          onClick={cancelEditUser}
                          className="text-xs px-2 py-1 bg-surface-800 border border-surface-700 text-surface-300 rounded-lg"
                        >
                          Cancel
                        </button>
                      </div>
                    ) : (
                      <button
                        type="button"
                        onClick={() => startEditUser(u)}
                        className="text-surface-400 hover:text-white"
                        aria-label={`Edit ${u.username}`}
                      >
                        <Pencil className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      )}

      {tab === 'roles' && (
        <div className="space-y-3">
          {roles.map((r) => (
            <div key={r.id} className="bg-surface-900 border border-surface-700/50 rounded-lg p-4">
              <div className="flex items-center justify-between mb-1">
                <div className="font-semibold">{r.name}</div>
                <button
                  type="button"
                  onClick={() => saveRole(r)}
                  disabled={savingRoleName === r.name}
                  className="text-xs px-2.5 py-1 bg-deloitte-green text-white font-semibold rounded-lg disabled:opacity-50"
                >
                  Save
                </button>
              </div>
              <p className="text-xs text-surface-500 mb-2">{r.description}</p>
              <div className="flex flex-wrap gap-3 text-xs">
                {ROLE_PERMISSION_KEYS.map(({ key, label }) => (
                  <label key={key} className="flex items-center gap-1.5 text-surface-300">
                    <input
                      type="checkbox"
                      checked={Boolean(r[key])}
                      onChange={() => toggleRolePermission(r.name, key)}
                    />
                    {label}
                  </label>
                ))}
              </div>
            </div>
          ))}
        </div>
      )}

      {tab === 'companies' && (
        <div className="space-y-4">
          <div className="bg-surface-900 border border-surface-700/50 rounded-xl p-4 space-y-3">
            <h3 className="text-sm font-semibold">New workspace</h3>
            <p className="text-xs text-surface-500">
              Each company is its own workspace — its datasets, drivers, and forecasts never
              mix with another company's data.
            </p>
            <div className="flex gap-2">
              <input
                value={newCompanyName}
                onChange={(e) => setNewCompanyName(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && createCompany()}
                placeholder="Company name (e.g. Carl Zeiss India)"
                className="flex-1 bg-surface-800 border border-surface-600 rounded-lg px-3 py-1.5 text-sm"
              />
              <button
                type="button"
                onClick={createCompany}
                disabled={creatingCompany || !newCompanyName.trim()}
                className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs bg-deloitte-green text-white font-semibold rounded-lg disabled:opacity-50"
              >
                <Plus className="w-3.5 h-3.5" /> Create
              </button>
            </div>
          </div>

          <table className="w-full text-sm">
            <thead className="text-surface-500 text-left">
              <tr>
                <th className="py-2">Company</th>
              </tr>
            </thead>
            <tbody>
              {businessUnits.map((bu) => (
                <tr key={bu.id} className="border-t border-surface-700/40">
                  <td className="py-2">{bu.name}</td>
                </tr>
              ))}
              {!businessUnits.length && (
                <tr>
                  <td className="py-4 text-surface-500 text-center">No companies yet</td>
                </tr>
              )}
            </tbody>
          </table>
        </div>
      )}

      {tab === 'coa' && coa && (
        <div className="space-y-4">
          <button
            type="button"
            onClick={wireCoa}
            className="px-3 py-1.5 text-xs bg-deloitte-green text-white font-semibold rounded-lg"
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
                  <tr key={li.id} className="border-t border-surface-700/40">
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
                className="px-3 py-1.5 text-xs bg-deloitte-green text-white font-semibold rounded-lg"
              >
                Save
              </button>
            </div>
          </div>

          <div className="bg-surface-900 border border-surface-700/50 rounded-xl p-4 space-y-3">
            <h3 className="text-sm font-semibold">Fiscal calendar</h3>
            <p className="text-xs text-surface-500">
              Gregorian months (YYYY-MM) or NRF-style 4-4-5 (FY2026-P01).
            </p>
            <div className="flex flex-wrap gap-2 items-center">
              <select
                value={fiscalCalendar.calendar_type}
                onChange={(e) =>
                  setFiscalCalendar({ ...fiscalCalendar, calendar_type: e.target.value })
                }
                className="bg-surface-800 border border-surface-600 rounded-lg px-3 py-1.5 text-sm"
              >
                <option value="gregorian_month">Gregorian months</option>
                <option value="fiscal_445">4-4-5 fiscal</option>
              </select>
              <label className="text-xs text-surface-400 flex items-center gap-1">
                FY start month
                <input
                  type="number"
                  min={1}
                  max={12}
                  value={fiscalCalendar.fiscal_year_start_month}
                  onChange={(e) =>
                    setFiscalCalendar({
                      ...fiscalCalendar,
                      fiscal_year_start_month: Number(e.target.value),
                    })
                  }
                  className="w-16 bg-surface-800 border border-surface-600 rounded-lg px-2 py-1 text-sm"
                />
              </label>
              <button
                type="button"
                onClick={saveFiscalCalendar}
                className="px-3 py-1.5 text-xs bg-deloitte-green text-white font-semibold rounded-lg"
              >
                Save calendar
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
              className="px-3 py-1.5 text-xs bg-deloitte-green text-white font-semibold rounded-lg"
            >
              Create rate
            </button>
          </div>

          <div className="bg-surface-900 border border-surface-700/50 rounded-xl p-4 space-y-2">
            <h3 className="text-sm font-semibold">Upload CSV</h3>
            <p className="text-xs text-surface-500">
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
                  <tr key={r.id} className="border-t border-surface-700/40">
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

      {tab === 'models' && (
        <div className="space-y-6">
          <div className="bg-surface-900 border border-surface-700/50 rounded-xl p-4 space-y-3">
            <h3 className="text-sm font-semibold">
              {editingPresetId ? 'Edit model preset' : 'New model preset'}
            </h3>
            <p className="text-xs text-surface-500">
              A saved shortcut for generate_baseline's model_type/models_to_test — pin one
              algorithm for every line item, or restrict the auto-selection pool.
            </p>
            <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
              <input
                value={presetForm.name}
                onChange={(e) => setPresetForm({ ...presetForm, name: e.target.value })}
                placeholder="Name (e.g. Conservative)"
                className="bg-surface-800 border border-surface-600 rounded-lg px-3 py-1.5 text-sm"
              />
              <input
                value={presetForm.description}
                onChange={(e) => setPresetForm({ ...presetForm, description: e.target.value })}
                placeholder="Description (optional)"
                className="bg-surface-800 border border-surface-600 rounded-lg px-3 py-1.5 text-sm"
              />
              <select
                value={presetForm.model_type}
                onChange={(e) =>
                  setPresetForm({ ...presetForm, model_type: e.target.value, candidate_models: [] })
                }
                className="bg-surface-800 border border-surface-600 rounded-lg px-3 py-1.5 text-sm"
              >
                <option value="auto">auto (best per line item)</option>
                {availableModels.map((m) => (
                  <option key={m.name} value={m.name}>
                    {m.display_label || m.name}
                  </option>
                ))}
              </select>
              <input
                type="number"
                min={1}
                max={60}
                value={presetForm.default_horizon_months}
                onChange={(e) =>
                  setPresetForm({ ...presetForm, default_horizon_months: e.target.value })
                }
                placeholder="Default horizon (months, optional)"
                className="bg-surface-800 border border-surface-600 rounded-lg px-3 py-1.5 text-sm"
              />
            </div>
            {presetForm.model_type === 'auto' && (
              <div>
                <p className="text-xs text-surface-500 mb-1.5">
                  Restrict candidate pool (optional — leave empty to consider all registered models)
                </p>
                <div className="flex flex-wrap gap-1.5">
                  {availableModels
                    .filter((m) => m.auto_selectable && !m.is_benchmark)
                    .map((m) => (
                      <button
                        key={m.name}
                        type="button"
                        onClick={() => toggleCandidateModel(m.name)}
                        className={`px-2 py-1 text-xs rounded-lg border transition-colors ${
                          presetForm.candidate_models.includes(m.name)
                            ? 'bg-deloitte-green/12 border-deloitte-green/30 text-deloitte-green font-semibold'
                            : 'bg-surface-800 border-surface-700 text-surface-400'
                        }`}
                      >
                        {m.display_label || m.name}
                      </button>
                    ))}
                </div>
              </div>
            )}
            <div className="flex gap-2">
              <button
                type="button"
                onClick={savePreset}
                disabled={savingPreset}
                className="px-3 py-1.5 text-xs bg-deloitte-green text-white font-semibold rounded-lg disabled:opacity-50"
              >
                {editingPresetId ? 'Save changes' : 'Create preset'}
              </button>
              {editingPresetId && (
                <button
                  type="button"
                  onClick={cancelPresetEdit}
                  className="px-3 py-1.5 text-xs bg-surface-800 border border-surface-700 text-surface-300 rounded-lg"
                >
                  Cancel
                </button>
              )}
            </div>
          </div>

          <div className="max-h-96 overflow-auto border border-surface-700/40 rounded-xl">
            <table className="w-full text-xs">
              <thead className="bg-surface-900 text-surface-500 sticky top-0">
                <tr>
                  <th className="px-3 py-2 text-left">Name</th>
                  <th className="px-3 py-2 text-left">Model type</th>
                  <th className="px-3 py-2 text-left">Candidate pool</th>
                  <th className="px-3 py-2 text-left">Active</th>
                  <th className="px-3 py-2 text-left">Actions</th>
                </tr>
              </thead>
              <tbody>
                {modelPresets.map((p) => (
                  <tr key={p.id} className="border-t border-surface-700/40">
                    <td className="px-3 py-1.5 font-medium">{p.name}</td>
                    <td className="px-3 py-1.5 text-surface-400">{p.model_type}</td>
                    <td className="px-3 py-1.5 text-surface-400">
                      {p.candidate_models?.length ? p.candidate_models.join(', ') : '—'}
                    </td>
                    <td className="px-3 py-1.5">{p.is_active ? 'Yes' : 'No'}</td>
                    <td className="px-3 py-1.5">
                      <div className="flex gap-2">
                        <button
                          type="button"
                          onClick={() => startEditPreset(p)}
                          className="text-surface-400 hover:text-white"
                          aria-label={`Edit ${p.name}`}
                        >
                          <Pencil className="w-3.5 h-3.5" />
                        </button>
                        {p.is_active && (
                          <button
                            type="button"
                            onClick={() => handleDeactivatePreset(p)}
                            className="text-surface-400 hover:text-red-400"
                            aria-label={`Deactivate ${p.name}`}
                          >
                            <Trash2 className="w-3.5 h-3.5" />
                          </button>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
                {!modelPresets.length && (
                  <tr>
                    <td colSpan={5} className="px-3 py-4 text-surface-500 text-center">
                      No model presets yet
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
  );
}
