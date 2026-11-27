/** Companies: create a workspace and edit its forecast settings (AnalysisProfile). */
import { Plus } from 'lucide-react';
import { Fragment, useEffect, useState } from 'react';
import { apiGet, apiPost, apiPut } from '../../api/client';
import { toast } from '../../store/toastStore';
import { useWorkspaceStore } from '../../store/workspaceStore';
import { AnalysisProfileResponse, BusinessUnit, type AdminTabProps } from './AdminConsoleShared';

export function AdminCompaniesTab({ onError }: AdminTabProps) {
  const hydrateWorkspace = useWorkspaceStore((s) => s.hydrate);
  const [businessUnits, setBusinessUnits] = useState<BusinessUnit[]>([]);
  const [newCompanyName, setNewCompanyName] = useState('');
  const [creatingCompany, setCreatingCompany] = useState(false);
  const [openProfileBuId, setOpenProfileBuId] = useState<string | null>(null);
  const [analysisProfile, setAnalysisProfile] = useState<AnalysisProfileResponse | null>(null);
  const [analysisProfileDraft, setAnalysisProfileDraft] = useState<Record<string, string | boolean>>({});
  const [loadingProfile, setLoadingProfile] = useState(false);
  const [savingProfile, setSavingProfile] = useState(false);

  const load = async () => {
    try {
      setBusinessUnits(await apiGet('/admin/business-units'));
    } catch (e: any) {
      onError(e.message || 'Failed to load');
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const createCompany = async () => {
    const name = newCompanyName.trim();
    if (!name) return;
    setCreatingCompany(true);
    onError('');
    try {
      await apiPost('/admin/business-units', { name });
      setNewCompanyName('');
      toast.success(`Created workspace "${name}"`);
      await load();
      // The new company should show up in every workspace switcher right away.
      void hydrateWorkspace();
    } catch (e: any) {
      onError(e.message || 'Failed to create company');
      toast.error(e.message || 'Failed to create company');
    } finally {
      setCreatingCompany(false);
    }
  };

  const toggleAnalysisProfile = async (buId: string) => {
    if (openProfileBuId === buId) {
      setOpenProfileBuId(null);
      setAnalysisProfile(null);
      return;
    }
    setOpenProfileBuId(buId);
    setLoadingProfile(true);
    try {
      const resp = await apiGet<AnalysisProfileResponse>(
        `/admin/business-units/${buId}/analysis-profile`
      );
      setAnalysisProfile(resp);
      setAnalysisProfileDraft(resp.settings as Record<string, string | boolean>);
    } catch (e: any) {
      toast.error(e.message || 'Failed to load forecast settings');
      setOpenProfileBuId(null);
    } finally {
      setLoadingProfile(false);
    }
  };

  const saveAnalysisProfile = async (buId: string) => {
    if (!analysisProfile) return;
    setSavingProfile(true);
    try {
      const patch: Record<string, string | number | boolean> = {};
      for (const [key, type] of Object.entries(analysisProfile.field_types)) {
        const draftVal = analysisProfileDraft[key];
        const current = analysisProfile.settings[key];
        if (type === 'bool') {
          if (draftVal !== current) patch[key] = Boolean(draftVal);
        } else if (type === 'int' || type === 'float') {
          const num = Number(draftVal);
          if (!Number.isNaN(num) && num !== current) patch[key] = num;
        } else if (draftVal !== current) {
          patch[key] = String(draftVal);
        }
      }
      if (Object.keys(patch).length === 0) {
        toast.success('No changes to save');
        return;
      }
      const resp = await apiPut<AnalysisProfileResponse>(
        `/admin/business-units/${buId}/analysis-profile`,
        { patch }
      );
      setAnalysisProfile(resp);
      setAnalysisProfileDraft(resp.settings as Record<string, string | boolean>);
      toast.success('Forecast settings saved');
    } catch (e: any) {
      toast.error(e.message || 'Failed to save forecast settings');
    } finally {
      setSavingProfile(false);
    }
  };

  return (
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
            <th className="py-2" />
          </tr>
        </thead>
        <tbody>
          {businessUnits.map((bu) => (
            <Fragment key={bu.id}>
              <tr className="border-t border-surface-700/40">
                <td className="py-2">{bu.name}</td>
                <td className="py-2 text-right">
                  <button
                    type="button"
                    onClick={() => toggleAnalysisProfile(bu.id)}
                    className="text-xs text-surface-400 hover:text-surface-100 underline"
                  >
                    {openProfileBuId === bu.id ? 'Close' : 'Forecast settings'}
                  </button>
                </td>
              </tr>
              {openProfileBuId === bu.id && (
                <tr className="border-t border-surface-700/40">
                  <td colSpan={2} className="py-3">
                    {loadingProfile && (
                      <p className="text-xs text-surface-500">Loading…</p>
                    )}
                    {!loadingProfile && analysisProfile && (
                      <div className="bg-surface-900 border border-surface-700/50 rounded-xl p-4 space-y-3">
                        <p className="text-xs text-surface-500">
                          Overrides for {bu.name}. A field left as-is keeps inheriting the
                          global default.
                        </p>
                        <div className="grid grid-cols-2 gap-3">
                          {Object.entries(analysisProfile.field_types).map(([key, type]) => {
                            const isOverridden = analysisProfile.overridden_fields.includes(key);
                            const val = analysisProfileDraft[key];
                            return (
                              <label
                                key={key}
                                className="flex items-center justify-between gap-2 text-xs bg-surface-800 rounded-lg px-2.5 py-1.5"
                              >
                                <span className={isOverridden ? 'text-deloitte-green font-medium' : 'text-surface-300'}>
                                  {key}
                                  {isOverridden && ' •'}
                                </span>
                                {type === 'bool' ? (
                                  <input
                                    type="checkbox"
                                    checked={Boolean(val)}
                                    onChange={(e) =>
                                      setAnalysisProfileDraft((d) => ({ ...d, [key]: e.target.checked }))
                                    }
                                  />
                                ) : (
                                  <input
                                    type="text"
                                    value={String(val ?? '')}
                                    onChange={(e) =>
                                      setAnalysisProfileDraft((d) => ({ ...d, [key]: e.target.value }))
                                    }
                                    className="w-20 bg-surface-700 border border-surface-600 rounded px-1.5 py-0.5 text-right"
                                  />
                                )}
                              </label>
                            );
                          })}
                        </div>
                        <button
                          type="button"
                          onClick={() => saveAnalysisProfile(bu.id)}
                          disabled={savingProfile}
                          className="px-3 py-1.5 text-xs bg-deloitte-green text-white font-semibold rounded-lg disabled:opacity-50"
                        >
                          {savingProfile ? 'Saving…' : 'Save changes'}
                        </button>
                      </div>
                    )}
                  </td>
                </tr>
              )}
            </Fragment>
          ))}
          {!businessUnits.length && (
            <tr>
              <td colSpan={2} className="py-4 text-surface-500 text-center">No companies yet</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
