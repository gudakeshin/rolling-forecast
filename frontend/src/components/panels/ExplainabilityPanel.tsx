import { useCallback, useEffect, useState } from 'react';
import { AlertTriangle, GitBranch, Loader2, RefreshCw, Sparkles } from 'lucide-react';
import { getBudgetBridge, getDriverDrilldown } from '../../api/scenarios';
import { usePanelStore } from '../../store/panelStore';
import { useVersionStore } from '../../store/versionStore';
import { toast } from '../../store/toastStore';
import { ChartRenderer } from '../chat/renderers/ChartRenderer';
import { DataTable, type DataTableColumn } from '../ui/DataTable';

function formatPct(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return `${v.toFixed(1)}%`;
}

function formatNum(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

export function ExplainabilityPanel() {
  const activeVersionId = useVersionStore((s) => s.activeVersionId);
  const openPanel = usePanelStore((s) => s.openPanel);
  const [loading, setLoading] = useState(false);
  const [convention, setConvention] = useState<'volume_first' | 'price_first'>('volume_first');
  const [bridge, setBridge] = useState<any>(null);
  const [drill, setDrill] = useState<any>(null);
  const [selectedLineId, setSelectedLineId] = useState<number | null>(null);
  const [periodFrom, setPeriodFrom] = useState('');
  const [periodTo, setPeriodTo] = useState('');

  const load = useCallback(async () => {
    if (!activeVersionId) {
      toast.error('Select a forecast version first');
      return;
    }
    setLoading(true);
    try {
      const data = await getBudgetBridge(activeVersionId, {
        attribute: true,
        convention,
        page_size: 100,
      });
      setBridge(data);
      if (!selectedLineId && data?.rows?.length) {
        const first = data.rows.find((r: any) => r.material) || data.rows[0];
        if (first?.line_item_id) setSelectedLineId(first.line_item_id);
      }
    } catch (e: any) {
      toast.error(e?.message || 'Failed to load bridge');
      setBridge(null);
    } finally {
      setLoading(false);
    }
  }, [activeVersionId, convention, selectedLineId]);

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps -- load on version/convention change
  }, [activeVersionId, convention]);

  const loadDrill = useCallback(async () => {
    if (!activeVersionId || selectedLineId == null) return;
    try {
      const data = await getDriverDrilldown(activeVersionId, {
        line_item_id: selectedLineId,
        period_from: periodFrom || undefined,
        period_to: periodTo || undefined,
        convention,
        basis: 'auto',
      });
      setDrill(data);
    } catch (e: any) {
      toast.error(e?.message || 'Drilldown failed');
      setDrill(null);
    }
  }, [activeVersionId, selectedLineId, periodFrom, periodTo, convention]);

  useEffect(() => {
    void loadDrill();
  }, [loadDrill]);

  const attribution = drill?.attributions?.[0];
  const waterfall = attribution?.waterfall || bridge?.waterfall;

  const columns: DataTableColumn<Record<string, unknown>>[] = [
    {
      key: 'line_item',
      label: 'Line',
      render: (_, row) => (
        <button
          type="button"
          className={`text-left hover:underline ${
            row.line_item_id === selectedLineId ? 'text-deloitte-green font-semibold' : 'text-surface-200'
          }`}
          onClick={() => setSelectedLineId(Number(row.line_item_id))}
        >
          {String(row.line_item)}
        </button>
      ),
    },
    {
      key: 'variance_vs_prior',
      label: 'Δ Prior',
      align: 'right',
      render: (v) => formatNum(Number(v)),
    },
    {
      key: 'variance_vs_budget',
      label: 'Δ Budget',
      align: 'right',
      render: (v) => formatNum(Number(v)),
    },
    {
      key: 'attribution',
      label: 'Method',
      render: (_v, row) => {
        const a = row.attribution as Record<string, unknown> | undefined;
        if (!a) return <span className="text-surface-500">—</span>;
        return (
          <span className="text-xs text-surface-300">
            {String(a.method || '—')}
            {a.explained_pct != null ? ` · ${formatPct(Number(a.explained_pct))} explained` : ''}
          </span>
        );
      },
    },
  ];

  if (!activeVersionId) {
    return (
      <div className="flex flex-col items-center justify-center py-12 gap-2 text-center">
        <AlertTriangle className="w-6 h-6 text-amber-400" />
        <p className="text-sm text-surface-400">Select a forecast version to explain variance.</p>
      </div>
    );
  }

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        <select
          value={convention}
          onChange={(e) => setConvention(e.target.value as 'volume_first' | 'price_first')}
          className="text-xs bg-surface-800 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-300"
        >
          <option value="volume_first">Convention: volume first</option>
          <option value="price_first">Convention: price first</option>
        </select>
        <input
          placeholder="period from"
          value={periodFrom}
          onChange={(e) => setPeriodFrom(e.target.value)}
          className="text-xs bg-surface-800 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-300 w-28"
        />
        <input
          placeholder="period to"
          value={periodTo}
          onChange={(e) => setPeriodTo(e.target.value)}
          className="text-xs bg-surface-800 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-300 w-28"
        />
        <button
          type="button"
          onClick={() => openPanel('what_if', {})}
          className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border border-surface-600 text-surface-300 hover:border-deloitte-green/40"
        >
          <GitBranch className="w-3.5 h-3.5" />
          What-if builder
        </button>
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border border-surface-600 text-surface-300"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          Refresh
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-10">
          <Loader2 className="w-5 h-5 animate-spin text-deloitte-green" />
        </div>
      ) : (
        <>
          {waterfall && (
            <div className="bg-surface-800/50 border border-surface-700/40 rounded-xl p-2">
              <ChartRenderer data={waterfall} />
            </div>
          )}

          {attribution && (
            <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3 space-y-2">
              <div className="flex items-center gap-2">
                <Sparkles className="w-4 h-4 text-deloitte-green" />
                <span className="text-xs font-semibold text-white">
                  Why did {attribution.line_item || 'this line'} move?
                </span>
              </div>
              <p className="text-xs text-surface-400">
                Method: <span className="text-surface-200">{attribution.method}</span>
                {attribution.convention ? ` · ${attribution.convention}` : ''}
                {' · '}
                Explained: <span className="text-surface-200">{formatPct(attribution.explained_pct)}</span>
              </p>
              {attribution.buckets && (
                <ul className="grid grid-cols-2 gap-1.5">
                  {Object.entries(attribution.buckets as Record<string, number | null>)
                    .filter(([, v]) => v != null)
                    .map(([k, v]) => (
                    <li
                      key={k}
                      className="text-xs px-2 py-1.5 rounded-lg bg-surface-900/70 border border-surface-700/40 flex justify-between"
                    >
                      <span className="text-surface-400 capitalize">{k.replace(/_/g, ' ')}</span>
                      <span className="font-mono text-surface-200">{formatNum(Number(v))}</span>
                    </li>
                  ))}
                </ul>
              )}
              {attribution.price_meta?.price_source === 'derived_l_over_q' && (
                <p className="text-xs text-amber-400/90">
                  Price is derived as L÷Q — it blends rate, mix, and discount effects; not measured ASP.
                </p>
              )}
              {attribution.note && (
                <p className="text-xs text-amber-400/90">{attribution.note}</p>
              )}
            </div>
          )}

          <DataTable
            title="Budget bridge (attributed)"
            columns={columns}
            rows={(bridge?.rows || []) as Record<string, unknown>[]}
            getRowId={(r) => String(r.line_item_id)}
            maxHeight={360}
            rowHeight={44}
          />
        </>
      )}
    </div>
  );
}
