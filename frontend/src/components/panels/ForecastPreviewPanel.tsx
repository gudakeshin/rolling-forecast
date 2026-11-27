import { Zap, AlertTriangle } from 'lucide-react';

function formatCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined) return '-';
  if (Math.abs(value) >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
}

interface PreviewPoint {
  period: string;
  p10: number;
  p50: number;
  p90: number;
}

interface PreviewLine {
  line_item_id: number;
  line_item_name: string;
  account_code: string;
  category: string;
  n_history: number;
  history: Array<{ period: string; actual: number }>;
  forecast: PreviewPoint[];
  total_p50: number;
  last_12_actual: number | null;
}

interface PreviewData {
  dataset_id: string;
  horizon: number;
  available: boolean;
  reason?: string;
  lines: PreviewLine[];
  skipped: Array<{ line_item: string; reason: string }>;
}

/** Tiny inline sparkline: history in grey, the preview P50 in green, P10-P90 shaded. */
function Spark({ line }: { line: PreviewLine }) {
  const hist = line.history.map((h) => h.actual);
  const p50 = line.forecast.map((f) => f.p50);
  const lo = line.forecast.map((f) => f.p10);
  const hi = line.forecast.map((f) => f.p90);
  const all = [...hist, ...lo, ...hi];
  if (all.length < 2) return null;
  const min = Math.min(...all);
  const max = Math.max(...all);
  const n = hist.length + p50.length;
  const w = 120;
  const h = 28;
  const x = (i: number) => (i / Math.max(n - 1, 1)) * w;
  const y = (v: number) => (max === min ? h / 2 : h - ((v - min) / (max - min)) * h);
  const path = (vals: number[], offset: number) =>
    vals.map((v, i) => `${i === 0 ? 'M' : 'L'}${x(i + offset).toFixed(1)},${y(v).toFixed(1)}`).join(' ');
  const off = hist.length;
  const band =
    lo.length > 0
      ? `M${x(off).toFixed(1)},${y(hi[0]).toFixed(1)} ` +
        hi.map((v, i) => `L${x(i + off).toFixed(1)},${y(v).toFixed(1)}`).join(' ') +
        ' ' +
        [...lo]
          .reverse()
          .map((v, i) => `L${x(off + lo.length - 1 - i).toFixed(1)},${y(v).toFixed(1)}`)
          .join(' ') +
        ' Z'
      : '';
  return (
    <svg width={w} height={h} aria-hidden="true">
      {band && <path d={band} className="fill-deloitte-green/15" />}
      <path d={path(hist, 0)} className="stroke-surface-400" fill="none" strokeWidth={1.25} />
      <path d={path(p50, off)} className="stroke-deloitte-green" fill="none" strokeWidth={1.5} />
    </svg>
  );
}

export function ForecastPreviewPanel({ data }: { data: any }) {
  const d: PreviewData | undefined = data?.data;
  if (!d) return null;
  if (!d.available) {
    return (
      <div className="p-4 text-xs text-surface-300" data-testid="forecast-preview-unavailable">
        <AlertTriangle className="w-4 h-4 text-amber-400 inline mr-1" />
        No instant preview: {d.reason}. The full forecast is unaffected; generate it as usual.
      </div>
    );
  }
  return (
    <div className="space-y-3" data-testid="forecast-preview">
      <div className="flex items-start gap-2 px-3 py-2 bg-surface-800/60 border border-surface-700/50 rounded-xl">
        <Zap className="w-3.5 h-3.5 text-deloitte-green mt-0.5 flex-shrink-0" />
        <p className="text-[11px] text-surface-300">
          Instant preview from Chronos-2 over the next {d.horizon} periods (P50 total; next period's
          P10–P90 underneath), for a first look only. It is not
          saved and skips model selection, drivers and calibration. Generate the baseline for the forecast you
          review and publish.
        </p>
      </div>
      <table className="w-full text-xs">
        <thead>
          <tr className="text-surface-500 text-left">
            <th className="py-1 font-medium">Line</th>
            <th className="py-1 font-medium">Shape</th>
            <th className="py-1 font-medium text-right">Next {d.horizon}</th>
            <th className="py-1 font-medium text-right">Last 12</th>
          </tr>
        </thead>
        <tbody>
          {d.lines.map((line) => (
            <tr key={line.line_item_id} className="border-t border-surface-800">
              <td className="py-1.5 pr-2">
                <div className="text-surface-200 truncate max-w-[160px]">{line.line_item_name}</div>
                <div className="text-[10px] text-surface-500">
                  {line.category} · {line.n_history} periods
                </div>
              </td>
              <td className="py-1.5">
                <Spark line={line} />
              </td>
              <td className="py-1.5 text-right">
                <div className="text-surface-200">{formatCurrency(line.total_p50)}</div>
                {line.forecast[0] && (
                  <div
                    className="text-[10px] text-surface-500 whitespace-nowrap"
                    title="Next period P10–P90"
                  >
                    {formatCurrency(line.forecast[0].p10)}–{formatCurrency(line.forecast[0].p90)}
                  </div>
                )}
              </td>
              <td className="py-1.5 text-right text-surface-400">{formatCurrency(line.last_12_actual)}</td>
            </tr>
          ))}
        </tbody>
      </table>
      {d.skipped.length > 0 && (
        <p className="text-[11px] text-surface-500">
          Not previewed: {d.skipped.map((s) => `${s.line_item} (${s.reason})`).join(', ')}.
        </p>
      )}
    </div>
  );
}
