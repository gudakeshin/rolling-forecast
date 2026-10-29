import { useEffect, useState } from 'react';
import { ArrowDownRight, ArrowUpRight, Minus } from 'lucide-react';
import { apiGet } from '../../api/client';
import { useAuthStore } from '../../store/authStore';

type Metric = 'cycle_days' | 'override_rate' | 'flagged_review_share' | 'fva_vs_naive';

interface Trend {
  latest: number | null;
  change: number | null;
  improving: boolean | null;
}

export interface ValueMetricsResponse {
  versions: { version_id: string }[];
  trends: Record<Metric, Trend>;
}

const TILES: { key: Metric; label: string; hint: string; format: (v: number) => string }[] = [
  { key: 'cycle_days', label: 'Cycle time', hint: 'Created to approved/published', format: (v) => `${v.toFixed(1)} d` },
  { key: 'override_rate', label: 'Override rate', hint: 'Share of lines a human corrected', format: (v) => `${(v * 100).toFixed(0)}%` },
  { key: 'flagged_review_share', label: 'Review on flagged', hint: 'Of reviewed lines, share the AI had flagged', format: (v) => `${(v * 100).toFixed(0)}%` },
  { key: 'fva_vs_naive', label: 'FVA vs naive', hint: 'Naive WAPE minus published WAPE', format: (v) => `${v > 0 ? '+' : ''}${v.toFixed(1)} pts` },
];

/** Is the product paying off across cycles? (backend: app/services/value_metrics.py) */
export function ValueMetrics() {
  const businessUnitId = useAuthStore((s) => s.user?.business_unit_id);
  const [data, setData] = useState<ValueMetricsResponse | null>(null);

  useEffect(() => {
    if (!businessUnitId) return;
    let cancelled = false;
    apiGet<ValueMetricsResponse>('/panel/value-metrics')
      .then((d) => !cancelled && setData(d))
      .catch(() => !cancelled && setData(null));
    return () => {
      cancelled = true;
    };
  }, [businessUnitId]);

  if (!data || data.versions.length === 0) return null;

  return (
    <section aria-label="Value delivered" className="space-y-1.5">
      <h4 className="text-xs text-surface-500 uppercase tracking-wider font-semibold">
        Value delivered · last {data.versions.length} version{data.versions.length === 1 ? '' : 's'}
      </h4>
      <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
        {TILES.map(({ key, label, hint, format }) => {
          const t = data.trends[key];
          const Icon = t.improving == null ? Minus : (t.change ?? 0) > 0 ? ArrowUpRight : ArrowDownRight;
          const color =
            t.improving == null ? 'text-surface-400' : t.improving ? 'text-deloitte-green' : 'text-red-400';
          return (
            <div key={key} className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3" title={hint}>
              <div className="text-xs text-surface-500">{label}</div>
              <div className="text-sm font-bold font-mono text-white">
                {t.latest != null ? format(t.latest) : '—'}
              </div>
              {t.change != null && (
                <div className={`text-[11px] flex items-center gap-0.5 ${color}`}>
                  <Icon className="w-3 h-3" aria-hidden="true" />
                  {format(Math.abs(t.change)).replace(/^\+/, '')}
                  <span className="sr-only">{t.improving ? ' (improving)' : t.improving === false ? ' (worsening)' : ''}</span>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </section>
  );
}
