import { ArrowUp, ArrowDown, Minus } from 'lucide-react';

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

  if (rows.length === 0) {
    return (
      <p className="text-surface-500 text-sm text-center py-8">No comparison data available</p>
    );
  }

  // Summary metrics
  const totalA = rows.reduce((s, r) => s + (r.value_a || 0), 0);
  const totalB = rows.reduce((s, r) => s + (r.value_b || 0), 0);
  const totalVar = totalA - totalB;
  const totalPct = totalB !== 0 ? (totalVar / Math.abs(totalB)) * 100 : 0;

  const positive = rows.filter((r) => r.pct_change > 0).length;
  const negative = rows.filter((r) => r.pct_change < 0).length;
  const unchanged = rows.filter((r) => r.pct_change === 0).length;

  // Category breakdown
  const categories: Record<string, { count: number; variance: number }> = {};
  rows.forEach((r) => {
    if (!categories[r.category]) categories[r.category] = { count: 0, variance: 0 };
    categories[r.category].count += 1;
    categories[r.category].variance += Math.abs(r.variance || 0);
  });

  return (
    <div className="space-y-4">
      {/* Version labels */}
      <div className="flex items-center justify-between bg-surface-800/50 rounded-lg p-3 border border-surface-700">
        <div className="text-center flex-1">
          <div className="text-xs text-surface-500 uppercase tracking-wider">Version A</div>
          <div className="text-sm font-semibold text-deloitte-green">{va?.name || 'Current'}</div>
        </div>
        <div className="text-surface-600 text-xs">vs</div>
        <div className="text-center flex-1">
          <div className="text-xs text-surface-500 uppercase tracking-wider">Version B</div>
          <div className="text-sm font-semibold text-accent-500">{vb?.name || 'Previous'}</div>
        </div>
      </div>

      {/* Summary stats */}
      <div className="grid grid-cols-3 gap-2">
        <MiniStat label="Increased" value={positive} icon={ArrowUp} color="text-deloitte-green" />
        <MiniStat label="Decreased" value={negative} icon={ArrowDown} color="text-red-400" />
        <MiniStat label="Unchanged" value={unchanged} icon={Minus} color="text-surface-400" />
      </div>

      {/* Total variance */}
      <div className="bg-surface-800/50 rounded-lg p-3 border border-surface-700 text-center">
        <div className="text-xs text-surface-500 uppercase tracking-wider mb-1">Net Variance</div>
        <div className={`text-xl font-bold ${totalVar >= 0 ? 'text-deloitte-green' : 'text-red-400'}`}>
          {totalVar >= 0 ? '+' : ''}{formatCurrency(totalVar)}
          <span className="text-sm ml-1">({totalPct >= 0 ? '+' : ''}{totalPct.toFixed(1)}%)</span>
        </div>
      </div>

      {/* Category breakdown */}
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

      {/* Line-by-line table */}
      <div className="border border-surface-700 rounded-lg overflow-hidden">
        <div className="overflow-x-auto max-h-[calc(100vh-520px)]">
          <table className="w-full text-xs">
            <thead className="sticky top-0 bg-surface-800 z-10">
              <tr className="border-b border-surface-700">
                <th className="px-3 py-2 text-left text-surface-400 font-semibold text-xs uppercase tracking-wider">
                  Line Item
                </th>
                <th className="px-3 py-2 text-right text-surface-400 font-semibold text-xs uppercase tracking-wider">
                  {va?.name?.substring(0, 10) || 'A'}
                </th>
                <th className="px-3 py-2 text-right text-surface-400 font-semibold text-xs uppercase tracking-wider">
                  {vb?.name?.substring(0, 10) || 'B'}
                </th>
                <th className="px-3 py-2 text-right text-surface-400 font-semibold text-xs uppercase tracking-wider">
                  %
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row, i) => (
                <tr
                  key={i}
                  className="border-b border-surface-700/20 hover:bg-deloitte-green/5 transition-colors"
                >
                  <td className="px-3 py-1.5 text-surface-300 truncate max-w-[140px]" title={row.line_item_name}>
                    {row.line_item_name}
                  </td>
                  <td className="px-3 py-1.5 text-right text-surface-200 font-mono">
                    {formatCurrency(row.value_a)}
                  </td>
                  <td className="px-3 py-1.5 text-right text-surface-400 font-mono">
                    {formatCurrency(row.value_b)}
                  </td>
                  <td className="px-3 py-1.5 text-right font-mono">
                    <span
                      className={
                        row.pct_change > 0
                          ? 'text-deloitte-green'
                          : row.pct_change < 0
                          ? 'text-red-400'
                          : 'text-surface-500'
                      }
                    >
                      {row.pct_change > 0 ? '+' : ''}
                      {row.pct_change.toFixed(1)}%
                    </span>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </div>
    </div>
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
