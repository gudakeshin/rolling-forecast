import { Info, AlertTriangle, TrendingUp, TrendingDown, History, Target } from 'lucide-react';

function formatCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined) return '-';
  if (Math.abs(value) >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
}

interface Comparison {
  model: string;
  mape: number | null;
  mase: number | null;
  pinball: number | null;
  n_folds: number;
  is_benchmark: boolean;
  eligible: boolean;
  error: string | null;
  selected: boolean;
}

interface WhyThisNumberData {
  result_id: string;
  line_item_name: string;
  period: string;
  p10: number | null;
  p50: number;
  p90: number | null;
  model_type: string | null;
  model_mape: number | null;
  model_mase: number | null;
  model_pinball: number | null;
  model_r_squared: number | null;
  confidence_score: number;
  confidence_level: string;
  bounds_method: string | null;
  is_overridden: boolean;
  model_selection: {
    best_model: string | null;
    selection_rule: string;
    data_points: number;
    comparisons: Comparison[];
  } | null;
  exog: {
    mode: string;
    admitted: boolean | null;
    rejected_reason: string | null;
    drivers: string[];
    plain_vs_exog: { admission_band?: number; mase_plain?: number; mase_exog?: number } | null;
  } | null;
  calibration: {
    method: string;
    n_residuals: number;
    scale: number | null;
  } | null;
  structural_break: { detected: boolean; period: string | null } | null;
  outlier_cleaning: { cleaned_periods: string[]; n_cleaned: number } | null;
  seasonality: { detected: boolean; period: number | null } | null;
  reconciliation: {
    pre_reconcile_p50: number;
    published_p50: number;
    delta: number;
    delta_pct: number | null;
  } | null;
  override_history: Array<{
    id: string;
    version_id: string;
    original_model_value: number;
    override_value: number;
    reason: string;
    status: string;
    created_at: string | null;
  }>;
  realized_coverage: {
    n_vintages: number;
    n_within_band: number;
    latest_actual: number;
    latest_pct_error: number | null;
  } | null;
}

function Section({ title, icon, children }: { title: string; icon?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="border-t border-surface-700/50 pt-3 mt-3 first:border-t-0 first:pt-0 first:mt-0">
      <div className="flex items-center gap-1.5 text-xs font-semibold text-surface-200 mb-2">
        {icon}
        {title}
      </div>
      {children}
    </div>
  );
}

const REASON_LABELS: Record<string, string> = {
  insufficient_train_points: 'Not enough training history for the exog model to fit reliably.',
  head_to_head_unavailable: 'The plain-vs-exog comparison could not be run.',
};

export function WhyThisNumberPanel({ data }: { data: any }) {
  const d: WhyThisNumberData = data?.data || data;
  if (!d) return <p className="text-surface-500 text-sm text-center">No data available</p>;

  const comparisons = d.model_selection?.comparisons || [];
  const sorted = [...comparisons]
    .filter((c) => c.eligible)
    .sort((a, b) => {
      const am = a.mase ?? a.mape ?? Infinity;
      const bm = b.mase ?? b.mape ?? Infinity;
      return am - bm;
    });
  const runnerUp = sorted.find((c) => !c.selected);
  const winner = sorted.find((c) => c.selected) || sorted[0];
  const margin =
    winner && runnerUp && winner.mase != null && runnerUp.mase != null
      ? runnerUp.mase - winner.mase
      : null;

  return (
    <div className="space-y-1 text-sm">
      <div className="flex items-baseline justify-between">
        <div>
          <div className="text-xs text-surface-500">{d.line_item_name} · {d.period}</div>
          <div className="text-lg font-bold text-white">{formatCurrency(d.p50)}</div>
          {(d.p10 !== null || d.p90 !== null) && (
            <div className="text-xs text-surface-500">
              P10 {formatCurrency(d.p10)} – P90 {formatCurrency(d.p90)}
            </div>
          )}
        </div>
        <div className="text-right">
          <span className="inline-block px-2 py-0.5 rounded-md bg-surface-700 text-xs font-medium text-surface-200">
            {d.model_type || 'unknown'}
          </span>
          <div className="text-[10px] text-surface-500 mt-1">
            {Math.round(d.confidence_score)}/100 · {d.confidence_level}
          </div>
        </div>
      </div>

      <Section title="Model chosen" icon={<Target className="w-3.5 h-3.5 text-deloitte-green" />}>
        {d.model_selection ? (
          <>
            <table className="w-full text-xs">
              <thead>
                <tr className="text-surface-500 text-left">
                  <th className="font-normal pb-1">Model</th>
                  <th className="font-normal pb-1 text-right">MASE</th>
                  <th className="font-normal pb-1 text-right">MAPE</th>
                  <th className="font-normal pb-1 text-right">Pinball</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map((c) => (
                  <tr
                    key={c.model}
                    className={c.selected ? 'text-deloitte-green font-semibold' : 'text-surface-300'}
                  >
                    <td className="py-0.5">
                      {c.model}
                      {c.selected && ' ✓'}
                      {c.is_benchmark && <span className="text-surface-500 font-normal"> (benchmark)</span>}
                    </td>
                    <td className="py-0.5 text-right">{c.mase != null ? c.mase.toFixed(3) : '—'}</td>
                    <td className="py-0.5 text-right">{c.mape != null ? `${c.mape.toFixed(1)}%` : '—'}</td>
                    <td className="py-0.5 text-right">{c.pinball != null ? c.pinball.toFixed(2) : '—'}</td>
                  </tr>
                ))}
              </tbody>
            </table>
            {runnerUp && margin != null && (
              <p className="text-[11px] text-surface-400 mt-1.5">
                Beat <span className="text-surface-300">{runnerUp.model}</span> by{' '}
                <span className="text-surface-300">{margin.toFixed(3)}</span> MASE
                {d.model_selection.selection_rule && ` (rule: ${d.model_selection.selection_rule})`}.
              </p>
            )}
          </>
        ) : (
          <p className="text-xs text-surface-500">
            Not available — this forecast was generated before model-selection detail was tracked.
          </p>
        )}
      </Section>

      {d.exog && (
        <Section title="Exogenous drivers" icon={<TrendingUp className="w-3.5 h-3.5 text-deloitte-green" />}>
          {d.exog.admitted ? (
            <p className="text-xs text-surface-300">
              Admitted — used {d.exog.drivers?.length ? d.exog.drivers.join(', ') : 'driver data'} as exogenous input.
            </p>
          ) : (
            <p className="text-xs text-surface-400">
              Not used.{' '}
              {d.exog.rejected_reason &&
                (REASON_LABELS[d.exog.rejected_reason] || d.exog.rejected_reason)}
            </p>
          )}
          {d.exog.plain_vs_exog && (
            <p className="text-[11px] text-surface-500 mt-1">
              Plain MASE {d.exog.plain_vs_exog.mase_plain?.toFixed(3)} vs exog MASE{' '}
              {d.exog.plain_vs_exog.mase_exog?.toFixed(3)}
              {d.exog.plain_vs_exog.admission_band != null &&
                ` (needed to beat plain by ${(d.exog.plain_vs_exog.admission_band * 100).toFixed(0)}%)`}
              .
            </p>
          )}
        </Section>
      )}

      <Section title="Prediction band" icon={<Info className="w-3.5 h-3.5 text-deloitte-green" />}>
        <p className="text-xs text-surface-300">
          {d.calibration ? (
            <>
              Calibrated on {d.calibration.n_residuals} out-of-sample residuals ({d.calibration.method}).
            </>
          ) : (
            <>Using the model's own analytic interval — not yet calibrated on realized residuals.</>
          )}
          {d.bounds_method && d.bounds_method !== 'model' && (
            <span className="text-surface-500"> Reconciled via {d.bounds_method}.</span>
          )}
        </p>
        {d.realized_coverage && (
          <p className="text-[11px] text-surface-500 mt-1">
            {d.realized_coverage.n_within_band} of {d.realized_coverage.n_vintages} closed vintage(s) landed
            inside this band. Latest actual: {formatCurrency(d.realized_coverage.latest_actual)}
            {d.realized_coverage.latest_pct_error != null &&
              ` (${d.realized_coverage.latest_pct_error > 0 ? '+' : ''}${d.realized_coverage.latest_pct_error.toFixed(1)}% error)`}.
          </p>
        )}
      </Section>

      {(d.structural_break?.detected || d.outlier_cleaning || d.seasonality?.detected) && (
        <Section title="History adjustments" icon={<AlertTriangle className="w-3.5 h-3.5 text-amber-400" />}>
          <ul className="text-xs text-surface-300 space-y-1 list-disc list-inside">
            {d.structural_break?.detected && (
              <li>Structural break detected at {d.structural_break.period} — history before it was down-weighted.</li>
            )}
            {d.outlier_cleaning && (
              <li>
                {d.outlier_cleaning.n_cleaned} period(s) winsorized as outliers:{' '}
                {d.outlier_cleaning.cleaned_periods.join(', ')}.
              </li>
            )}
            {d.seasonality?.detected && <li>Seasonality detected (period {d.seasonality.period}).</li>}
          </ul>
        </Section>
      )}

      {d.reconciliation && (
        <Section title="Reconciliation" icon={<TrendingDown className="w-3.5 h-3.5 text-deloitte-green" />}>
          <p className="text-xs text-surface-300">
            The model's own forecast was {formatCurrency(d.reconciliation.pre_reconcile_p50)}. Hierarchical
            reconciliation (MinT){' '}
            {d.reconciliation.delta_pct != null && Math.abs(d.reconciliation.delta_pct) < 0.05 ? (
              <>nudged it by a negligible amount</>
            ) : (
              <>
                adjusted it {d.reconciliation.delta >= 0 ? 'up' : 'down'} to{' '}
                {formatCurrency(d.reconciliation.published_p50)}
                {d.reconciliation.delta_pct != null &&
                  ` (${d.reconciliation.delta_pct > 0 ? '+' : ''}${d.reconciliation.delta_pct}%)`}
              </>
            )}
            {' '}so the hierarchy adds up.
          </p>
        </Section>
      )}

      {d.override_history.length > 0 && (
        <Section title="Override history" icon={<History className="w-3.5 h-3.5 text-deloitte-green" />}>
          <ul className="space-y-1.5">
            {d.override_history.map((o) => (
              <li key={o.id} className="text-xs text-surface-300">
                <span className="text-surface-200 font-medium">
                  {formatCurrency(o.original_model_value)} → {formatCurrency(o.override_value)}
                </span>{' '}
                <span className="text-surface-500">({o.status})</span>
                <div className="text-surface-500">{o.reason}</div>
              </li>
            ))}
          </ul>
        </Section>
      )}
    </div>
  );
}
