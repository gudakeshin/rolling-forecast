import { useMemo, useState } from 'react';
import { ArrowUp, ArrowDown, Minus, ExternalLink, Table, GitCompare } from 'lucide-react';
import { usePanelStore } from '../../store/panelStore';
import { useVersionStore, versionsByScenario } from '../../store/versionStore';
import { DataTable, type DataTableColumn } from '../ui/DataTable';

interface Props {
  data: {
    panel_type: string;
    title: string;
    data: {
      version_a?: Record<string, any>;
      version_b?: Record<string, any>;
      rows?: any[];
      total_count?: number;
    };
  };
}

export function ComparisonPanel({ data }: Props) {
  const rows = data.data.rows || [];
  const va = data.data.version_a;
  const vb = data.data.version_b;
  const openPanel = usePanelStore((s) => s.openPanel);
  const versions = useVersionStore((s) => s.versions);
  const grouped = useMemo(() => versionsByScenario(versions), [versions]);
  const scenarios = Object.keys(grouped).sort();

  const [pickA, setPickA] = useState(va?.id || '');
  const [pickB, setPickB] = useState(vb?.id || '');

  const applyCompare = (a: string, b: string) => {
    if (!a || !b || a === b) return;
    openPanel('comparison', { version_id_a: a, version_id_b: b });
  };

  const compareScenariosPreset = () => {
    // Prefer newest version per distinct scenario (base vs first non-base)
    const base = grouped.base?.[0];
    const otherKey = scenarios.find((s) => s !== 'base');
    const other = otherKey ? grouped[otherKey]?.[0] : undefined;
    if (base && other) {
      setPickA(base.id);
      setPickB(other.id);
      applyCompare(base.id, other.id);
    }
  };

  if (rows.length === 0 && versions.length < 2) {
    return (
      <p className="text-surface-500 text-sm text-center py-8">No comparison data available</p>
    );
  }

  const totalA = rows.reduce((s, r) => s + (r.value_a || 0), 0);
  const totalB = rows.reduce((s, r) => s + (r.value_b || 0), 0);
  const totalVar = totalA - totalB;
  const totalPct = totalB !== 0 ? (totalVar / Math.abs(totalB)) * 100 : 0;

  const positive = rows.filter((r) => r.pct_change > 0).length;
  const negative = rows.filter((r) => r.pct_change < 0).length;
  const unchanged = rows.filter((r) => r.pct_change === 0).length;

  const categories: Record<string, { count: number; variance: number }> = {};
  rows.forEach((r) => {
    if (!categories[r.category]) categories[r.category] = { count: 0, variance: 0 };
    categories[r.category].count += 1;
    categories[r.category].variance += Math.abs(r.variance || 0);
  });

  const versionId = va?.id;

  return (
    <div className="space-y-4">
      {versions.length >= 2 && (
        <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3 space-y-2">
          <div className="flex items-center justify-between gap-2">
            <h4 className="text-xs font-semibold text-surface-400 uppercase tracking-wider">
              Compare versions
            </h4>
            {scenarios.length >= 2 && (
              <button
                type="button"
                onClick={compareScenariosPreset}
                className="inline-flex items-center gap-1 text-xs text-deloitte-green hover:underline"
              >
                <GitCompare className="w-3 h-3" /> Compare scenarios
              </button>
            )}
          </div>
          <div className="grid grid-cols-2 gap-2">
            <VersionSelect
              label="Version A"
              value={pickA}
              grouped={grouped}
              onChange={setPickA}
            />
            <VersionSelect
              label="Version B"
              value={pickB}
              grouped={grouped}
              onChange={setPickB}
            />
          </div>
          <button
            type="button"
            disabled={!pickA || !pickB || pickA === pickB}
            onClick={() => applyCompare(pickA, pickB)}
            className="w-full py-1.5 text-xs font-semibold bg-deloitte-green/15 text-deloitte-green border border-deloitte-green/30 rounded-lg disabled:opacity-40"
          >
            Run comparison
          </button>
        </div>
      )}

      {rows.length === 0 ? (
        <p className="text-surface-500 text-sm text-center py-6">
          Select two versions above to compare.
        </p>
      ) : (
        <>
          <div className="flex items-center justify-between bg-surface-800/50 rounded-lg p-3 border border-surface-700">
            <div className="text-center flex-1">
              <div className="text-xs text-surface-500 uppercase tracking-wider">Version A</div>
              <div className="text-sm font-semibold text-deloitte-green">{va?.name || 'Current'}</div>
              {va?.scenario && (
                <div className="text-xs text-surface-500 mt-0.5">{va.scenario}</div>
              )}
            </div>
            <div className="text-surface-600 text-xs">vs</div>
            <div className="text-center flex-1">
              <div className="text-xs text-surface-500 uppercase tracking-wider">Version B</div>
              <div className="text-sm font-semibold text-accent-500">{vb?.name || 'Previous'}</div>
              {vb?.scenario && (
                <div className="text-xs text-surface-500 mt-0.5">{vb.scenario}</div>
              )}
            </div>
          </div>

          <div className="grid grid-cols-3 gap-2">
            <MiniStat label="Increased" value={positive} icon={ArrowUp} color="text-deloitte-green" />
            <MiniStat label="Decreased" value={negative} icon={ArrowDown} color="text-red-400" />
            <MiniStat label="Unchanged" value={unchanged} icon={Minus} color="text-surface-400" />
          </div>

          <div className="bg-surface-800/50 rounded-lg p-3 border border-surface-700 text-center">
            <div className="text-xs text-surface-500 uppercase tracking-wider mb-1">Net Variance</div>
            <div className={`text-xl font-bold ${totalVar >= 0 ? 'text-deloitte-green' : 'text-red-400'}`}>
              {totalVar >= 0 ? '+' : ''}{formatCurrency(totalVar)}
              <span className="text-sm ml-1">({totalPct >= 0 ? '+' : ''}{totalPct.toFixed(1)}%)</span>
            </div>
          </div>

          <div>
            <h4 className="text-xs text-surface-400 font-semibold uppercase tracking-wider mb-2">
              By Category
            </h4>
            <div className="space-y-1.5">
              {Object.entries(categories)
                .sort((a, b) => b[1].variance - a[1].variance)
                .map(([cat, info]) => (
                  <div
                    key={cat}
                    className="flex items-center justify-between p-2 bg-surface-800/30 rounded-lg"
                  >
                    <span className="text-xs text-surface-300">{cat}</span>
                    <span className="text-xs text-surface-500">{info.count} items</span>
                  </div>
                ))}
            </div>
          </div>

          <DataTable
            title="Line comparison"
            maxHeight={480}
            exportFilename={`compare_${va?.name || 'a'}_${vb?.name || 'b'}`}
            columns={
              [
                { key: 'line_item_name', label: 'Line Item' },
                {
                  key: 'value_a',
                  label: va?.name?.substring(0, 10) || 'A',
                  align: 'right',
                  render: (v) => formatCurrency(Number(v) || 0),
                },
                {
                  key: 'value_b',
                  label: vb?.name?.substring(0, 10) || 'B',
                  align: 'right',
                  render: (v) => formatCurrency(Number(v) || 0),
                },
                {
                  key: 'pct_change',
                  label: '%',
                  align: 'right',
                  render: (v) => {
                    const pct = Number(v) || 0;
                    const cls =
                      pct > 0 ? 'text-deloitte-green' : pct < 0 ? 'text-red-400' : 'text-surface-500';
                    return (
                      <span className={cls}>
                        {pct > 0 ? '+' : ''}
                        {pct.toFixed(1)}%
                      </span>
                    );
                  },
                },
              ] as DataTableColumn<Record<string, unknown>>[]
            }
            rows={rows as Record<string, unknown>[]}
            getRowClassName={() => 'hover:bg-deloitte-green/5'}
            rowActions={(row) => (
              <>
                {versionId && row.line_item_id != null && (
                  <button
                    type="button"
                    title="Review"
                    onClick={(e) => {
                      e.stopPropagation();
                      openPanel('review_dashboard', {
                        version_id: versionId,
                        focus_line_item_id: row.line_item_id,
                      });
                    }}
                    className="p-1 text-surface-400 hover:text-deloitte-green focus-visible:opacity-100"
                  >
                    <ExternalLink className="w-3 h-3" />
                  </button>
                )}
                {versionId && (
                  <button
                    type="button"
                    title="Open table"
                    onClick={(e) => {
                      e.stopPropagation();
                      openPanel('forecast_table', { version_id: versionId });
                    }}
                    className="p-1 text-surface-400 hover:text-deloitte-green focus-visible:opacity-100"
                  >
                    <Table className="w-3 h-3" />
                  </button>
                )}
              </>
            )}
          />
        </>
      )}
    </div>
  );
}

function VersionSelect({
  label,
  value,
  grouped,
  onChange,
}: {
  label: string;
  value: string;
  grouped: Record<string, { id: string; name: string; label: string | null; scenario?: string }[]>;
  onChange: (id: string) => void;
}) {
  return (
    <label className="block text-xs text-surface-500">
      {label}
      <select
        value={value}
        onChange={(e) => onChange(e.target.value)}
        className="mt-1 w-full px-2 py-1.5 bg-surface-900 border border-surface-600 rounded text-xs text-white"
      >
        <option value="">Select…</option>
        {Object.keys(grouped)
          .sort()
          .map((scenario) => (
            <optgroup key={scenario} label={scenario}>
              {grouped[scenario].map((v) => (
                <option key={v.id} value={v.id}>
                  {v.label || v.name}
                </option>
              ))}
            </optgroup>
          ))}
      </select>
    </label>
  );
}

function MiniStat({
  label,
  value,
  icon: Icon,
  color,
}: {
  label: string;
  value: number;
  icon: any;
  color: string;
}) {
  return (
    <div className="bg-surface-800/50 border border-surface-700 rounded-lg p-2.5 text-center">
      <Icon className={`w-4 h-4 mx-auto ${color} mb-1`} />
      <div className={`text-sm font-bold ${color}`}>{value}</div>
      <div className="text-xs text-surface-500 uppercase tracking-wider">{label}</div>
    </div>
  );
}

function formatCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined) return '-';
  if (Math.abs(value) >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
}
