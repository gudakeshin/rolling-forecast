import type { ExogInfo } from './forecastContext';

/** Exogenous-driver validation scores for one line (forecast vs known drivers).
 * `indent` aligns it under a row's expanded detail in the forecast table. */
export function ExogModeScoresCard({ exog, indent = false }: { exog?: ExogInfo | null; indent?: boolean }) {
  const scores = exog?.exog_mode_scores;
  if (!exog || !scores) return null;
  const formatMase = (value: number | null | undefined) =>
    value == null || !Number.isFinite(value) ? 'n/a' : value.toFixed(3);
  const forecastScore = typeof scores.mase_forecast === 'number' ? scores.mase_forecast : null;
  const knownScore = typeof scores.mase_known === 'number' ? scores.mase_known : null;
  const delta = forecastScore != null && knownScore != null ? forecastScore - knownScore : null;

  const card = (
    <div className="bg-surface-800/60 border border-deloitte-teal/20 rounded-lg p-2.5 space-y-1.5">
      <div className="flex items-center gap-2 flex-wrap">
        <span className="text-xs font-semibold text-deloitte-teal uppercase tracking-wider">
          Exogenous Validation
        </span>
        <span className="text-xs text-surface-500">folds: {scores.n_folds ?? 0}</span>
        <span className="text-xs text-surface-500">mode: {exog.mode || 'forecast'}</span>
      </div>
      <div className="grid grid-cols-3 gap-2">
        <div className="bg-surface-900/50 rounded px-2 py-1">
          <div className="text-xs text-surface-500">MASE (forecast exog)</div>
          <div className="text-xs font-mono text-surface-200">{formatMase(scores.mase_forecast)}</div>
        </div>
        <div className="bg-surface-900/50 rounded px-2 py-1">
          <div className="text-xs text-surface-500">MASE (known exog)</div>
          <div className="text-xs font-mono text-surface-200">{formatMase(scores.mase_known)}</div>
        </div>
        <div className="bg-surface-900/50 rounded px-2 py-1">
          <div className="text-xs text-surface-500">Gap (forecast-known)</div>
          <div className={`text-xs font-mono ${delta != null && delta > 0 ? 'text-amber-400' : 'text-deloitte-green'}`}>
            {delta == null ? 'n/a' : `${delta > 0 ? '+' : ''}${delta.toFixed(3)}`}
          </div>
        </div>
      </div>
    </div>
  );
  return indent ? <div className="pl-7">{card}</div> : card;
}
