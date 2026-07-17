import { useEffect, useMemo, useState } from 'react';
import { ArrowDown, ArrowUp, RotateCcw, ExternalLink, Loader2, Plus } from 'lucide-react';
import { DataTable, type DataTableColumn } from '../ui/DataTable';
import { apiGet, apiPost } from '../../api/client';
import { usePanelStore } from '../../store/panelStore';
import { useCan } from '../../store/authStore';
import { toast } from '../../store/toastStore';

interface LineOption {
  id: string;
  line_item_id: number;
  line_item_name: string;
  period?: string;
}

interface Props {
  data: {
    panel_type: string;
    title: string;
    data: {
      version?: Record<string, any>;
      items?: any[];
      total_count?: number;
    };
  };
  onRefresh?: () => void;
}

export function OverridesPanel({ data, onRefresh }: Props) {
  const items = data.data.items || [];
  const version = data.data.version;
  const openPanel = usePanelStore((s) => s.openPanel);
  const canOverride = useCan('can_override');
  const [confirmId, setConfirmId] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [showForm, setShowForm] = useState(items.length === 0 && canOverride);
  const [lineOptions, setLineOptions] = useState<LineOption[]>([]);
  const [loadingLines, setLoadingLines] = useState(false);
  const [resultId, setResultId] = useState('');
  const [newValue, setNewValue] = useState('');
  const [reason, setReason] = useState('');
  const [applyTo, setApplyTo] = useState<'all' | 'single'>('all');
  const [creating, setCreating] = useState(false);

  useEffect(() => {
    if (!showForm || !version?.id) return;
    let cancelled = false;
    setLoadingLines(true);
    apiGet<any>(`/panel/forecast-table/${version.id}?view=summary&limit=200`)
      .then((res) => {
        if (cancelled) return;
        const rows = res?.data?.rows || [];
        setLineOptions(
          rows.map((r: any) => ({
            id: r.id,
            line_item_id: r.line_item_id,
            line_item_name: r.line_item_name || r.name || `Item ${r.line_item_id}`,
            period: r.period,
          })),
        );
        if (rows[0]?.id) setResultId(rows[0].id);
      })
      .catch((e: any) => {
        if (!cancelled) toast.error(e?.message || 'Failed to load line items');
      })
      .finally(() => {
        if (!cancelled) setLoadingLines(false);
      });
    return () => {
      cancelled = true;
    };
  }, [showForm, version?.id]);

  const handleRevert = async (overrideId: string) => {
    setBusyId(overrideId);
    try {
      await apiPost(`/panel/overrides/${overrideId}/revert`, {});
      toast.success('Override reverted');
      setConfirmId(null);
      onRefresh?.();
    } catch (e: any) {
      toast.error(e.message || 'Revert failed');
    } finally {
      setBusyId(null);
    }
  };

  const handleCreate = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!resultId) {
      toast.error('Select a line item');
      return;
    }
    const numVal = parseFloat(newValue);
    if (isNaN(numVal)) {
      toast.error('Enter a valid value');
      return;
    }
    if (reason.trim().length < 10) {
      toast.error('Reason must be at least 10 characters');
      return;
    }
    setCreating(true);
    try {
      await apiPost('/panel/inline-override', {
        result_id: resultId,
        new_value: numVal,
        reason: reason.trim(),
        apply_to: applyTo,
      });
      toast.success('Override created');
      setNewValue('');
      setReason('');
      setShowForm(false);
      onRefresh?.();
    } catch (err: any) {
      toast.error(err?.message || 'Failed to create override');
    } finally {
      setCreating(false);
    }
  };

  const activeCount = items.filter((i: any) => i.status === 'active').length;
  const revertedCount = items.filter((i: any) => i.status === 'reverted').length;

  const overrideColumns = useMemo<DataTableColumn<Record<string, unknown>>[]>(
    () => [
      { key: 'line_item_name', label: 'Line Item' },
      { key: 'period', label: 'Period' },
      {
        key: 'original_value',
        label: 'Original',
        align: 'right',
        render: (v) => `$${Number(v || 0).toLocaleString()}`,
      },
      {
        key: 'override_value',
        label: 'Override',
        align: 'right',
        render: (v) => `$${Number(v || 0).toLocaleString()}`,
      },
      {
        key: 'change_pct',
        label: 'Change',
        align: 'right',
        render: (v) => <ChangeIndicator pct={Number(v) || 0} />,
      },
      {
        key: 'status',
        label: 'Status',
        render: (v) => (
          <span
            className={`text-xs px-2 py-0.5 rounded-full ${
              v === 'active'
                ? 'bg-deloitte-green/15 text-deloitte-green'
                : 'bg-surface-600/30 text-surface-400'
            }`}
          >
            {String(v ?? '')}
          </span>
        ),
      },
      {
        key: 'reason',
        label: 'Reason',
        render: (v) => <span className="text-surface-500 italic truncate max-w-[180px] block">{String(v ?? '')}</span>,
      },
    ],
    [],
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between gap-2">
        <div className="grid grid-cols-3 gap-2 flex-1">
          <StatCard label="Active" value={activeCount} variant="green" />
          <StatCard label="Reverted" value={revertedCount} variant="gray" />
          <StatCard
            label="Downstream"
            value={items.reduce((sum: number, i: any) => sum + (i.downstream_recalc || 0), 0)}
            variant="teal"
          />
        </div>
        <div className="flex items-center gap-1.5 shrink-0">
          {canOverride && (
            <button
              type="button"
              onClick={() => setShowForm((v) => !v)}
              className="inline-flex items-center gap-1 px-2.5 py-1.5 text-xs bg-deloitte-green/15 text-deloitte-green border border-deloitte-green/30 rounded-lg hover:bg-deloitte-green/25"
            >
              <Plus className="w-3.5 h-3.5" />
              {showForm ? 'Hide' : 'Add'}
            </button>
          )}
        </div>
      </div>

      {showForm && canOverride && (
        <form
          onSubmit={handleCreate}
          className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3 space-y-2"
        >
          <h4 className="text-xs font-semibold text-white uppercase tracking-wider">New override</h4>
          {loadingLines ? (
            <div className="flex items-center gap-2 text-xs text-surface-500 py-2">
              <Loader2 className="w-3.5 h-3.5 animate-spin" /> Loading line items…
            </div>
          ) : (
            <select
              value={resultId}
              onChange={(e) => setResultId(e.target.value)}
              className="w-full px-2 py-1.5 bg-surface-900 border border-surface-600 rounded text-xs text-white"
              required
            >
              {lineOptions.length === 0 && <option value="">No line items available</option>}
              {lineOptions.map((opt) => (
                <option key={opt.id} value={opt.id}>
                  {opt.line_item_name}
                </option>
              ))}
            </select>
          )}
          <div className="flex gap-2">
            <input
              type="number"
              step="any"
              value={newValue}
              onChange={(e) => setNewValue(e.target.value)}
              placeholder="New value"
              className="flex-1 px-2 py-1.5 bg-surface-900 border border-surface-600 rounded text-xs text-white placeholder-surface-500"
              required
            />
            <select
              value={applyTo}
              onChange={(e) => setApplyTo(e.target.value as 'all' | 'single')}
              className="px-2 py-1.5 bg-surface-900 border border-surface-600 rounded text-xs text-white"
            >
              <option value="all">All periods</option>
              <option value="single">Single period</option>
            </select>
          </div>
          <textarea
            value={reason}
            onChange={(e) => setReason(e.target.value)}
            placeholder="Reason (min 10 characters)"
            rows={2}
            className="w-full px-2 py-1.5 bg-surface-900 border border-surface-600 rounded text-xs text-white placeholder-surface-500"
            required
          />
          <button
            type="submit"
            disabled={creating || loadingLines || !resultId}
            className="w-full py-1.5 text-xs font-semibold bg-deloitte-green text-black rounded-lg disabled:opacity-50"
          >
            {creating ? <Loader2 className="w-3.5 h-3.5 animate-spin mx-auto" /> : 'Apply override'}
          </button>
        </form>
      )}

      {items.length === 0 && !showForm && (
        <p className="text-surface-500 text-sm text-center py-8">No overrides applied</p>
      )}

      {items.length > 0 && (
        <DataTable
          title="Overrides"
          columns={overrideColumns}
          rows={items as Record<string, unknown>[]}
          maxHeight={420}
          exportFilename={`overrides_${version?.name || 'export'}`}
          getRowId={(row) => String(row.id ?? `${row.line_item_id}-${row.period}`)}
          getRowClassName={(row) => (row.status !== 'active' ? 'opacity-60' : '')}
          rowActions={(item) => (
            <>
              {version?.id && item.line_item_id != null && (
                <button
                  type="button"
                  onClick={(e) => {
                    e.stopPropagation();
                    openPanel('review_dashboard', {
                      version_id: version.id,
                      focus_line_item_id: item.line_item_id as number,
                    });
                  }}
                  className="inline-flex items-center gap-1 text-xs text-surface-400 hover:text-deloitte-green"
                >
                  <ExternalLink className="w-3 h-3" /> Open
                </button>
              )}
              {item.status === 'active' && canOverride && (
                confirmId === item.id ? (
                  <div className="flex items-center gap-1.5">
                    <span className="text-xs text-amber-400">Revert?</span>
                    <button
                      type="button"
                      disabled={busyId === item.id}
                      onClick={(e) => {
                        e.stopPropagation();
                        void handleRevert(String(item.id));
                      }}
                      className="px-2 py-0.5 bg-red-500/20 text-red-400 rounded text-xs disabled:opacity-50"
                    >
                      {busyId === item.id ? <Loader2 className="w-3 h-3 animate-spin" /> : 'Confirm'}
                    </button>
                    <button
                      type="button"
                      onClick={(e) => {
                        e.stopPropagation();
                        setConfirmId(null);
                      }}
                      className="px-2 py-0.5 text-surface-500 text-xs"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={(e) => {
                      e.stopPropagation();
                      setConfirmId(String(item.id));
                    }}
                    className="inline-flex items-center gap-1 text-xs text-amber-400 hover:text-amber-300"
                  >
                    <RotateCcw className="w-3 h-3" /> Revert
                  </button>
                )
              )}
            </>
          )}
        />
      )}
    </div>
  );
}

function ChangeIndicator({ pct }: { pct: number }) {
  if (pct > 0) {
    return (
      <span className="inline-flex items-center gap-0.5 text-deloitte-green">
        <ArrowUp className="w-3 h-3" />+{pct.toFixed(1)}%
      </span>
    );
  }
  if (pct < 0) {
    return (
      <span className="inline-flex items-center gap-0.5 text-red-400">
        <ArrowDown className="w-3 h-3" />{pct.toFixed(1)}%
      </span>
    );
  }
  return <span className="text-surface-500">0%</span>;
}

function StatCard({
  label,
  value,
  variant,
}: {
  label: string;
  value: number;
  variant: string;
}) {
  const colorClass =
    variant === 'green'
      ? 'text-deloitte-green border-deloitte-green/20'
      : variant === 'teal'
      ? 'text-accent-500 border-accent-500/20'
      : 'text-surface-400 border-surface-700';

  return (
    <div className={`bg-surface-800/50 border rounded-lg p-3 text-center ${colorClass}`}>
      <div className="text-lg font-bold">{value}</div>
      <div className="text-xs text-surface-500 uppercase tracking-wider font-medium">
        {label}
      </div>
    </div>
  );
}
