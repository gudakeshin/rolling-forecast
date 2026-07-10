import { ArrowDown, ArrowUp, RotateCcw } from 'lucide-react';

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
}

export function OverridesPanel({ data }: Props) {
  const items = data.data.items || [];
  const version = data.data.version;

  if (items.length === 0) {
    return (
      <p className="text-surface-500 text-sm text-center py-8">No overrides applied</p>
    );
  }

  const activeCount = items.filter((i: any) => i.status === 'active').length;
  const revertedCount = items.filter((i: any) => i.status === 'reverted').length;

  return (
    <div className="space-y-4">
      {/* Summary */}
      <div className="grid grid-cols-3 gap-2">
        <StatCard label="Active" value={activeCount} variant="green" />
        <StatCard label="Reverted" value={revertedCount} variant="gray" />
        <StatCard
          label="Downstream"
          value={items.reduce((sum: number, i: any) => sum + (i.downstream_recalc || 0), 0)}
          variant="teal"
        />
      </div>

      {/* Override list */}
      <div className="space-y-2">
        {items.map((item: any, i: number) => (
          <div
            key={i}
            className={`p-3 rounded-lg border ${
              item.status === 'active'
                ? 'bg-surface-800/50 border-surface-700'
                : 'bg-surface-800/20 border-surface-700/30 opacity-60'
            }`}
          >
            <div className="flex items-center justify-between mb-1">
              <span className="text-sm font-medium text-white">{item.line_item_name}</span>
              <span
                className={`text-xs px-2 py-0.5 rounded-full ${
                  item.status === 'active'
                    ? 'bg-deloitte-green/15 text-deloitte-green'
                    : 'bg-surface-600/30 text-surface-400'
                }`}
              >
                {item.status}
              </span>
            </div>

            <div className="flex items-center gap-3 text-xs text-surface-400">
              <span className="font-mono">{item.period}</span>
              <span className="text-surface-500">
                ${item.original_value?.toLocaleString()} →
              </span>
              <span className="font-medium text-white">
                ${item.override_value?.toLocaleString()}
              </span>
              <ChangeIndicator pct={item.change_pct} />
            </div>

            <p className="text-xs text-surface-500 mt-1 italic">{item.reason}</p>

            {item.downstream_recalc > 0 && (
              <div className="text-xs text-accent-500 mt-1">
                {item.downstream_recalc} downstream items recalculated
              </div>
            )}
          </div>
        ))}
      </div>
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
