import { useCallback, useEffect, useState } from 'react';
import { GitBranch, Loader2, Plus, Trash2 } from 'lucide-react';
import { listDrivers, type Driver } from '../../api/drivers';
import { createWhatIf, type DriverShock } from '../../api/scenarios';
import { usePanelStore } from '../../store/panelStore';
import { useVersionStore } from '../../store/versionStore';
import { toast } from '../../store/toastStore';

interface ShockRow {
  id: string;
  driver_id: string;
  mode: 'pct' | 'absolute' | 'replace';
  value: string;
}

export function WhatIfPanel() {
  const activeVersionId = useVersionStore((s) => s.activeVersionId);
  const refreshVersions = useVersionStore((s) => s.refresh);
  const setActiveVersionId = useVersionStore((s) => s.setActiveVersionId);
  const openPanel = usePanelStore((s) => s.openPanel);

  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [label, setLabel] = useState('scenario');
  const [shocks, setShocks] = useState<ShockRow[]>([
    { id: '1', driver_id: '', mode: 'pct', value: '-10' },
  ]);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<any>(null);

  useEffect(() => {
    void listDrivers()
      .then(setDrivers)
      .catch((e) => toast.error(e?.message || 'Failed to load drivers'));
  }, []);

  const addShock = () => {
    setShocks((prev) => [
      ...prev,
      { id: String(Date.now()), driver_id: '', mode: 'pct', value: '' },
    ]);
  };

  const removeShock = (id: string) => {
    setShocks((prev) => (prev.length <= 1 ? prev : prev.filter((s) => s.id !== id)));
  };

  const updateShock = (id: string, patch: Partial<ShockRow>) => {
    setShocks((prev) => prev.map((s) => (s.id === id ? { ...s, ...patch } : s)));
  };

  const handleRun = useCallback(async () => {
    if (!activeVersionId) {
      toast.error('Select a base forecast version first');
      return;
    }
    const parsed: DriverShock[] = [];
    for (const s of shocks) {
      const driverId = Number(s.driver_id);
      const value = Number(s.value);
      if (!Number.isFinite(driverId) || driverId <= 0) {
        toast.error('Each shock needs a driver');
        return;
      }
      if (!Number.isFinite(value)) {
        toast.error('Each shock needs a numeric value');
        return;
      }
      parsed.push({ driver_id: driverId, mode: s.mode, value });
    }
    if (!label.trim()) {
      toast.error('Scenario label is required');
      return;
    }

    setSubmitting(true);
    setResult(null);
    try {
      const out = await createWhatIf({
        base_version_id: activeVersionId,
        scenario_label: label.trim(),
        shocks: parsed,
      });
      setResult(out);
      toast.success(`Created scenario ${out.scenario_label || label}`);
      await refreshVersions();
      if (out.scenario_version_id) {
        setActiveVersionId(out.scenario_version_id);
      }
    } catch (e: any) {
      toast.error(e?.message || 'What-if failed');
    } finally {
      setSubmitting(false);
    }
  }, [activeVersionId, shocks, label, refreshVersions, setActiveVersionId]);

  return (
    <div className="space-y-4">
      <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3 space-y-3">
        <div className="flex items-center gap-2">
          <GitBranch className="w-4 h-4 text-deloitte-green" />
          <span className="text-sm font-semibold text-white">What-if scenario</span>
        </div>
        <p className="text-xs text-surface-400">
          Shock causal drivers and clone the active forecast into a scenario version.
          Linked line items are perturbed via coefficients; P10/P90 stay on the base.
        </p>

        <label className="block space-y-1">
          <span className="text-xs text-surface-500">Scenario label</span>
          <input
            value={label}
            onChange={(e) => setLabel(e.target.value)}
            className="w-full text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
            placeholder="e.g. headcount_down_10"
          />
        </label>

        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-xs font-semibold text-surface-300">Driver shocks</span>
            <button
              type="button"
              onClick={addShock}
              className="inline-flex items-center gap-1 text-xs text-deloitte-green hover:underline"
            >
              <Plus className="w-3 h-3" />
              Add shock
            </button>
          </div>
          {shocks.map((s) => (
            <div key={s.id} className="grid grid-cols-[1fr_auto_auto_auto] gap-1.5 items-center">
              <select
                value={s.driver_id}
                onChange={(e) => updateShock(s.id, { driver_id: e.target.value })}
                className="text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
              >
                <option value="">Select driver…</option>
                {drivers.map((d) => (
                  <option key={d.id} value={d.id}>
                    {d.key} — {d.name}
                  </option>
                ))}
              </select>
              <select
                value={s.mode}
                onChange={(e) =>
                  updateShock(s.id, { mode: e.target.value as ShockRow['mode'] })
                }
                className="text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
              >
                <option value="pct">% change</option>
                <option value="absolute">absolute Δ</option>
                <option value="replace">replace</option>
              </select>
              <input
                type="number"
                value={s.value}
                onChange={(e) => updateShock(s.id, { value: e.target.value })}
                className="w-20 text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
                placeholder={s.mode === 'pct' ? '-10' : 'value'}
              />
              <button
                type="button"
                onClick={() => removeShock(s.id)}
                className="p-1.5 text-surface-500 hover:text-red-400"
                aria-label="Remove shock"
              >
                <Trash2 className="w-3.5 h-3.5" />
              </button>
            </div>
          ))}
        </div>

        <button
          type="button"
          onClick={() => void handleRun()}
          disabled={submitting || !activeVersionId}
          className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-2 rounded-lg bg-deloitte-green text-white hover:bg-deloitte-green/90 disabled:opacity-50"
        >
          {submitting ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <GitBranch className="w-3.5 h-3.5" />}
          Run what-if
        </button>
      </div>

      {result && (
        <div className="bg-surface-800/60 border border-deloitte-green/25 rounded-xl p-3 space-y-2">
          <p className="text-sm text-white font-semibold">Scenario created</p>
          <ul className="text-xs text-surface-400 space-y-1">
            <li>
              Version:{' '}
              <span className="font-mono text-surface-200">{result.scenario_version_id}</span>
            </li>
            <li>Line-periods perturbed: {result.affected_line_periods ?? '—'}</li>
            <li>Line items affected: {result.affected_line_items ?? '—'}</li>
          </ul>
          <button
            type="button"
            onClick={() =>
              openPanel('forecast_table', { version_id: result.scenario_version_id })
            }
            className="text-xs text-deloitte-green hover:underline"
          >
            Open scenario forecast table
          </button>
        </div>
      )}

      {!drivers.length && (
        <p className="text-xs text-surface-500">
          No drivers available. Create or upload drivers first, then link them to line items.
        </p>
      )}
    </div>
  );
}
