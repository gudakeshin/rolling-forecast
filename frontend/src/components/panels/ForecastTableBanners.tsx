/** Calibration and quality banners and the stat cards at the top of the forecast table. */
import { AlertTriangle, Sparkles, Target, XCircle } from 'lucide-react';
import { type CalibrationSummary, type QualitySummary } from './ForecastTableTypes';

// ── Quality Summary Banner ──────────────────
export function CalibrationBanner({ calibration }: { calibration: CalibrationSummary }) {
  const { realized_coverage: realized, target_coverage: target } = calibration;
  const targetPct = Math.round((target ?? 0.8) * 100);

  // Say what is actually known. With too few closed cycles we can state that the
  // bands were calibrated on backtest errors, but not that they have been proven
  // against reality — and an honest 78% earns more trust than a silent 80%.
  const proven = realized != null && calibration.lines_with_realized_coverage > 0;
  const realizedPct = proven ? Math.round(realized * 100) : null;
  const drift = proven ? Math.abs((realizedPct as number) - targetPct) : 0;
  const tone = !proven
    ? 'text-surface-400'
    : drift <= 5
      ? 'text-deloitte-green'
      : 'text-amber-400';

  return (
    <div className="flex items-start gap-2 px-3 py-2 bg-surface-800/60 border border-surface-700/50 rounded-xl">
      <Target className="w-3.5 h-3.5 mt-0.5 shrink-0 text-surface-500" aria-hidden="true" />
      <p className="text-xs text-surface-400 leading-relaxed">
        <span className="font-semibold text-surface-200">
          {calibration.calibrated_lines} of {calibration.total_lines}
        </span>{' '}
        lines have prediction bands calibrated on out-of-sample backtest errors
        rather than the model{"'"}s own distributional assumption.{' '}
        {proven ? (
          <>
            Across{' '}
            <span className="font-semibold text-surface-200">
              {calibration.lines_with_realized_coverage}
            </span>{' '}
            lines with closed cycles,{' '}
            <span className={`font-semibold ${tone}`}>{realizedPct}%</span>{' '}
            of actuals landed inside the {targetPct}% band.
          </>
        ) : (
          <span className="text-surface-500">
            Not enough closed cycles yet to report realized coverage.
          </span>
        )}
      </p>
    </div>
  );
}

export function QualitySummaryBanner({
  quality,
  onOpenReview,
}: {
  quality: QualitySummary;
  onOpenReview: () => void;
}) {
  const hasCritical = quality.critical_count > 0;
  const hasWarning = quality.warning_count > 0;

  return (
    <div className="space-y-2">
      {/* Confidence summary bar */}
      <div className="bg-surface-800/80 border border-surface-700/50 rounded-xl p-3">
        <div className="flex items-center justify-between mb-2">
          <div className="flex items-center gap-2">
            <Sparkles className="w-4 h-4 text-deloitte-green" />
            <span className="text-xs font-semibold text-white">AI Quality Analysis</span>
          </div>
          <span className="text-xs text-surface-400">
            Avg. confidence: <span className="text-white font-bold">{quality.avg_confidence}</span>/100
          </span>
        </div>

        {/* Visual confidence distribution bar */}
        <div className="flex h-2.5 rounded-full overflow-hidden bg-surface-700 mb-2">
          {quality.ok_count > 0 && (
            <div
              className="bg-deloitte-green transition-all"
              style={{ width: `${(quality.ok_count / quality.total_scored) * 100}%` }}
              title={`${quality.ok_count} items approved`}
            />
          )}
          {quality.warning_count > 0 && (
            <div
              className="bg-amber-400 transition-all"
              style={{ width: `${(quality.warning_count / quality.total_scored) * 100}%` }}
              title={`${quality.warning_count} items need review`}
            />
          )}
          {quality.critical_count > 0 && (
            <div
              className="bg-red-500 transition-all"
              style={{ width: `${(quality.critical_count / quality.total_scored) * 100}%` }}
              title={`${quality.critical_count} items need action`}
            />
          )}
        </div>

        <div className="flex items-center gap-4 text-xs">
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-deloitte-green" />
            <span className="text-surface-400">{quality.ok_count} OK</span>
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-amber-400" />
            <span className="text-surface-400">{quality.warning_count} Review</span>
          </span>
          <span className="flex items-center gap-1">
            <span className="w-2 h-2 rounded-full bg-red-500" />
            <span className="text-surface-400">{quality.critical_count} Critical</span>
          </span>
        </div>
      </div>

      {/* Action alerts */}
      {hasCritical && (
        <div className="flex items-center gap-2 px-3 py-2 bg-red-500/8 border border-red-500/20 rounded-xl">
          <XCircle className="w-4 h-4 text-red-400 flex-shrink-0" />
          <div className="flex-1">
            <span className="text-xs font-semibold text-red-400">
              {quality.critical_count} items need immediate action
            </span>
            <p className="text-xs text-surface-400">
              These items have poor model fit or no data — override or provide manual values.
            </p>
          </div>
          <button
            onClick={onOpenReview}
            className="text-xs font-medium px-2.5 py-1 bg-red-500/15 text-red-400 rounded-lg hover:bg-red-500/25 transition-colors whitespace-nowrap"
          >
            Review All
          </button>
        </div>
      )}

      {hasWarning && !hasCritical && (
        <div className="flex items-center gap-2 px-3 py-2 bg-amber-500/8 border border-amber-500/20 rounded-xl">
          <AlertTriangle className="w-4 h-4 text-amber-400 flex-shrink-0" />
          <div className="flex-1">
            <span className="text-xs font-semibold text-amber-400">
              {quality.warning_count} items recommended for review
            </span>
            <p className="text-xs text-surface-400">
              Model accuracy is moderate — consider reviewing and adjusting.
            </p>
          </div>
          <button
            onClick={onOpenReview}
            className="text-xs font-medium px-2.5 py-1 bg-amber-500/15 text-amber-400 rounded-lg hover:bg-amber-500/25 transition-colors whitespace-nowrap"
          >
            Open Review
          </button>
        </div>
      )}
    </div>
  );
}

// ── Stat Card ────────────────────────────────
export function StatCard({
  label,
  value,
  variant,
}: {
  label: string;
  value: number;
  variant?: string;
}) {
  const colorClass =
    variant === 'green'
      ? 'text-deloitte-green border-deloitte-green/20'
      : variant === 'red'
      ? 'text-red-400 border-red-500/20'
      : 'text-white border-surface-700';

  return (
    <div className={`bg-surface-800/50 border rounded-lg p-3 text-center ${colorClass}`}>
      <div className="text-lg font-bold">{value ?? 0}</div>
      <div className="text-xs text-surface-500 uppercase tracking-wider font-medium">
        {label}
      </div>
    </div>
  );
}
