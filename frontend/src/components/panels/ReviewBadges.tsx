/** Small review-dashboard visuals: risk bar, AI/confidence badges, materiality dot, chart tooltip. */
import { AlertTriangle, CheckCircle, Edit3, Eye, XCircle } from 'lucide-react';
import { chartTheme } from '../../theme/chartTheme';
import { type ReviewItem } from './ReviewDashboardTypes';

export const COLORS = {
  green: chartTheme.colors.primary,
  amber: chartTheme.colors.warning,
  red: chartTheme.colors.danger,
  teal: chartTheme.colors.secondary,
  coolGray: chartTheme.colors.tertiary,
  blue: '#3D6E8A',
};

export function exogGapForItem(item: ReviewItem): number | null {
  const s = item.exog?.exog_mode_scores;
  if (!s) return null;
  const forecast = typeof s.mase_forecast === 'number' ? s.mase_forecast : null;
  const known = typeof s.mase_known === 'number' ? s.mase_known : null;
  if (forecast == null || known == null) return null;
  return forecast - known;
}

export function RiskBar({ score }: { score: number }) {
  const color = score > 60 ? COLORS.red : score > 35 ? COLORS.amber : COLORS.green;
  return (
    <div className="flex items-center gap-1.5">
      <div className="w-14 h-1.5 bg-surface-700 rounded-full overflow-hidden">
        <div
          className="h-full rounded-full transition-all"
          style={{ width: `${Math.min(score, 100)}%`, backgroundColor: color }}
        />
      </div>
      <span className="text-xs font-mono" style={{ color }}>{score.toFixed(0)}</span>
    </div>
  );
}

export function AIBadge({ recommendation }: { recommendation: string }) {
  const config: Record<string, { label: string; class: string; icon: any }> = {
    approve: { label: 'Approve', class: 'bg-deloitte-green/15 text-deloitte-green border-deloitte-green/25', icon: CheckCircle },
    review: { label: 'Review', class: 'bg-amber-500/15 text-amber-400 border-amber-500/25', icon: Eye },
    flag: { label: 'Flag', class: 'bg-red-500/15 text-red-400 border-red-500/25', icon: AlertTriangle },
    override: { label: 'Override', class: 'bg-red-600/20 text-red-300 border-red-500/30', icon: XCircle },
    manual_input: { label: 'Manual', class: 'bg-red-600/20 text-red-300 border-red-500/30', icon: Edit3 },
  };
  const c = config[recommendation] || config.review;
  const Icon = c.icon;
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded text-xs font-semibold border ${c.class}`}>
      <Icon className="w-2.5 h-2.5" />
      {c.label}
    </span>
  );
}

export function ConfBadge({ score }: { score: number }) {
  const cls =
    score >= 70 ? 'bg-deloitte-green/15 text-deloitte-green border-deloitte-green/20'
    : score >= 50 ? 'bg-yellow-500/15 text-yellow-400 border-yellow-500/20'
    : 'bg-red-500/15 text-red-400 border-red-500/20';
  return (
    <span className={`inline-block px-1.5 py-0.5 rounded text-xs font-semibold border ${cls}`}>
      {Math.round(score)}
    </span>
  );
}

export function MaterialityDot({ level }: { level?: string }) {
  if (!level || level === 'low') return null;
  return (
    <span
      className={`inline-block w-1.5 h-1.5 rounded-full flex-shrink-0 ${
        level === 'high' ? 'bg-red-400' : 'bg-amber-400'
      }`}
      title={`${level} materiality`}
    />
  );
}

export const ChartTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-surface-800 border border-surface-600 rounded-lg px-3 py-2 shadow-xl">
      <p className="text-xs text-surface-400 mb-1">{label}</p>
      {payload.map((p: any, i: number) => (
        <p key={i} className="text-xs font-medium" style={{ color: p.color }}>
          {p.name}: {typeof p.value === 'number' ? p.value.toFixed(1) : p.value}
        </p>
      ))}
    </div>
  );
};
