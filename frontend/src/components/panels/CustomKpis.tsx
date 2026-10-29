import { useState } from 'react';
import { Calculator, Plus, Trash2, X } from 'lucide-react';
import { useCan } from '../../store/authStore';
import { toast } from '../../store/toastStore';
import {
  createKpi,
  deleteKpi,
  evaluateKpis,
  validateKpiFormula,
  type FormulaCheck,
  type KpiDisplayFormat,
  type KpiResult,
} from '../../api/kpis';

export function formatKpiValue(value: number | null, format: KpiDisplayFormat): string {
  if (value === null || value === undefined || !Number.isFinite(value)) return '—';
  switch (format) {
    case 'percent':
      return `${(value * 100).toFixed(1)}%`;
    case 'currency':
      return value.toLocaleString(undefined, { style: 'currency', currency: 'USD', maximumFractionDigits: 0 });
    case 'ratio':
      return `${value.toFixed(2)}x`;
    default:
      return value.toLocaleString(undefined, { maximumFractionDigits: 2 });
  }
}

const FORMATS: KpiDisplayFormat[] = ['number', 'currency', 'percent', 'ratio'];

/** Company-defined KPI formulas evaluated for this version (backend:
 * app/services/kpis.py). Admins can add and remove KPIs in place. */
export function CustomKpis({ versionId, initial }: { versionId: string; initial: KpiResult[] }) {
  const canAdmin = useCan('can_admin');
  const [kpis, setKpis] = useState<KpiResult[]>(initial);
  const [managing, setManaging] = useState(false);

  if (!kpis.length && !canAdmin) return null;

  const refresh = async () => setKpis(await evaluateKpis(versionId));

  const remove = async (kpi: KpiResult) => {
    try {
      await deleteKpi(kpi.id);
      await refresh();
    } catch (e: any) {
      toast.error(e?.message || 'Could not delete KPI');
    }
  };

  return (
    <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-xs text-surface-500 uppercase tracking-wider font-semibold flex items-center gap-1">
          <Calculator className="w-3 h-3" /> Company KPIs
        </h4>
        {canAdmin && (
          <button
            type="button"
            onClick={() => setManaging((m) => !m)}
            className="text-xs text-surface-400 hover:text-surface-200"
            aria-expanded={managing}
          >
            {managing ? 'Done' : 'Manage'}
          </button>
        )}
      </div>

      {kpis.length === 0 ? (
        <p className="text-xs text-surface-500">
          No KPIs yet. Add one with a formula such as{' '}
          <code className="font-mono">([category:Revenue] - [category:COGS]) / [category:Revenue]</code>.
        </p>
      ) : (
        <div className="grid grid-cols-2 sm:grid-cols-3 gap-2">
          {kpis.map((k) => {
            const note = k.error || (k.missing.length ? `No forecast for ${k.missing.join(', ')}` : '');
            return (
              <div key={k.id} className="relative bg-surface-900/40 rounded-lg px-2.5 py-2" title={`${k.name}: ${k.formula}`}>
                <div className="text-xs text-surface-400 truncate pr-4">{k.name}</div>
                <div className="text-sm font-bold font-mono text-white">
                  {formatKpiValue(k.total, k.display_format)}
                </div>
                {note && <div className="text-[10px] text-amber-400 truncate" title={note}>{note}</div>}
                {managing && (
                  <button
                    type="button"
                    onClick={() => remove(k)}
                    className="absolute top-1.5 right-1.5 text-surface-500 hover:text-red-400"
                    aria-label={`Delete KPI ${k.name}`}
                  >
                    <Trash2 className="w-3 h-3" />
                  </button>
                )}
              </div>
            );
          })}
        </div>
      )}

      {managing && <NewKpiForm onCreated={refresh} />}
    </div>
  );
}

function NewKpiForm({ onCreated }: { onCreated: () => Promise<void> }) {
  const [open, setOpen] = useState(false);
  const [name, setName] = useState('');
  const [formula, setFormula] = useState('');
  const [format, setFormat] = useState<KpiDisplayFormat>('number');
  const [check, setCheck] = useState<FormulaCheck | null>(null);
  const [saving, setSaving] = useState(false);

  const runCheck = async () => {
    if (!formula.trim()) {
      setCheck(null);
      return;
    }
    try {
      setCheck(await validateKpiFormula(formula));
    } catch (e: any) {
      setCheck({ valid: false, error: e?.message || 'Could not check formula' });
    }
  };

  const save = async () => {
    setSaving(true);
    try {
      await createKpi({ name: name.trim(), formula, display_format: format });
      await onCreated();
      setName('');
      setFormula('');
      setFormat('number');
      setCheck(null);
      setOpen(false);
    } catch (e: any) {
      setCheck({ valid: false, error: e?.message || 'Could not save KPI' });
    } finally {
      setSaving(false);
    }
  };

  if (!open) {
    return (
      <button
        type="button"
        onClick={() => setOpen(true)}
        className="mt-2 flex items-center gap-1 text-xs text-deloitte-green hover:underline"
      >
        <Plus className="w-3 h-3" /> Add KPI
      </button>
    );
  }

  return (
    <div className="mt-2 space-y-2 border-t border-surface-700/50 pt-2">
      <div className="flex items-center justify-between">
        <span className="text-xs font-semibold text-surface-300">New KPI</span>
        <button type="button" onClick={() => setOpen(false)} aria-label="Cancel new KPI" className="text-surface-500 hover:text-surface-300">
          <X className="w-3 h-3" />
        </button>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <input
          value={name}
          onChange={(e) => setName(e.target.value)}
          placeholder="Name, e.g. Gross margin %"
          aria-label="KPI name"
          maxLength={120}
          className="col-span-2 bg-surface-900/60 border border-surface-700 rounded px-2 py-1 text-xs text-surface-200"
        />
        <select
          value={format}
          onChange={(e) => setFormat(e.target.value as KpiDisplayFormat)}
          aria-label="KPI display format"
          className="bg-surface-900/60 border border-surface-700 rounded px-2 py-1 text-xs text-surface-200"
        >
          {FORMATS.map((f) => (
            <option key={f} value={f}>
              {f}
            </option>
          ))}
        </select>
      </div>
      <input
        value={formula}
        onChange={(e) => {
          setFormula(e.target.value);
          setCheck(null);
        }}
        onBlur={runCheck}
        placeholder="([category:Revenue] - [category:COGS]) / [category:Revenue]"
        aria-label="KPI formula"
        className="w-full bg-surface-900/60 border border-surface-700 rounded px-2 py-1 text-xs font-mono text-surface-200"
      />
      <p className="text-[10px] text-surface-500">
        Reference a line item by name or account code, or a category, in brackets: <code>[Materials]</code>,{' '}
        <code>[item:4000]</code>, <code>[category:OpEx]</code>. Operators: + − × ÷ and parentheses.
      </p>
      {check && (
        <p className={`text-xs ${check.valid ? 'text-deloitte-green' : 'text-red-400'}`} role="status">
          {check.valid
            ? `Formula OK: ${check.references.map((r) => `${r.reference} (${r.kind})`).join(', ')}`
            : check.error}
        </p>
      )}
      <div className="flex justify-end">
        <button
          type="button"
          onClick={save}
          disabled={saving || !name.trim() || !formula.trim()}
          className="px-3 py-1 text-xs bg-deloitte-green text-white font-semibold rounded disabled:opacity-50"
        >
          {saving ? 'Saving…' : 'Save KPI'}
        </button>
      </div>
    </div>
  );
}
