/** Business analysis summary across the review buckets (charts and headline numbers). */
import { Activity, BarChart3, Edit3, FileText, PieChart } from 'lucide-react';
import type { ReviewBuckets } from './ReviewDashboardTypes';
import { formatCurrency } from './shared/format';

// ─── Business Analysis Summary ─────────────────────

export function BusinessAnalysisSummary({ buckets }: {
  buckets: ReviewBuckets;
}) {
  const allItems = [
    ...buckets.flagged.items,
    ...buckets.needs_review.items,
    ...buckets.ai_approved.items,
    ...buckets.already_reviewed.items,
  ];

  const flaggedAndReview = [...buckets.flagged.items, ...buckets.needs_review.items];

  // Total forecast value and value at risk
  const totalForecastValue = allItems.reduce((sum, i) => sum + (i.total_p50 || 0), 0);
  const valueAtRisk = flaggedAndReview.reduce((sum, i) => sum + Math.abs(i.total_p50 || 0), 0);

  // Materiality breakdown
  const highMat = flaggedAndReview.filter(i => i.materiality === 'high');
  const medMat = flaggedAndReview.filter(i => i.materiality === 'medium');

  // Root cause distribution
  const rootCauseCounts: Record<string, number> = {};
  flaggedAndReview.forEach(i => {
    const rc = i.root_cause || 'unknown';
    if (rc !== 'none') rootCauseCounts[rc] = (rootCauseCounts[rc] || 0) + 1;
  });
  const topRootCauses = Object.entries(rootCauseCounts)
    .sort((a, b) => b[1] - a[1])
    .slice(0, 4);

  // Category $ at risk
  const categoryRisk: Record<string, { value: number; count: number }> = {};
  flaggedAndReview.forEach(i => {
    const cat = i.category;
    if (!categoryRisk[cat]) categoryRisk[cat] = { value: 0, count: 0 };
    categoryRisk[cat].value += Math.abs(i.total_p50 || 0);
    categoryRisk[cat].count += 1;
  });
  const topCategoriesByRisk = Object.entries(categoryRisk)
    .sort((a, b) => b[1].value - a[1].value)
    .slice(0, 5);

  // Business driver observations from items with context
  const driverObservations: string[] = [];
  const itemsWithTrend = flaggedAndReview
    .filter(i => i.driver_context?.actuals_trend)
    .sort((a, b) => Math.abs(b.total_p50 || 0) - Math.abs(a.total_p50 || 0));

  for (const item of itemsWithTrend.slice(0, 3)) {
    const trend = item.driver_context!.actuals_trend!;
    const avgP50 = item.avg_p50 || 0;
    if (trend.recent_avg !== 0 && avgP50 !== 0) {
      const forecastVsActual = ((avgP50 - trend.recent_avg) / Math.abs(trend.recent_avg)) * 100;
      if (Math.abs(forecastVsActual) > 10) {
        const dir = forecastVsActual > 0 ? 'above' : 'below';
        driverObservations.push(
          `${item.line_item_name} forecasts ${Math.abs(forecastVsActual).toFixed(0)}% ${dir} recent actuals` +
          (trend.direction !== 'flat' ? ` (actuals trending ${trend.direction})` : '')
        );
      }
    }
  }

  // Items with active overrides that need validation
  const overriddenItems = flaggedAndReview.filter(
    i => i.driver_context?.active_overrides && i.driver_context.active_overrides.length > 0
  );

  if (flaggedAndReview.length === 0) return null;

  const rootCauseLabels: Record<string, string> = {
    no_data: 'No Data',
    sparse_data: 'Sparse Data',
    low_confidence: 'Low Confidence',
    poor_model_fit: 'Poor Model Fit',
    high_uncertainty: 'High Uncertainty',
    forecast_drift: 'Forecast Drift',
    override_impact: 'Override Impact',
    category_outlier: 'Category Outlier',
    unknown: 'Other',
  };

  return (
    <div className="bg-surface-800/70 border border-surface-700/50 rounded-xl p-3 space-y-3">
      <div className="flex items-center gap-2">
        <FileText className="w-4 h-4 text-deloitte-teal" />
        <span className="text-xs font-semibold text-white">Business Analysis</span>
      </div>

      {/* KPI row */}
      <div className="grid grid-cols-4 gap-2">
        <div className="bg-surface-900/50 rounded-lg px-2.5 py-2 text-center">
          <div className="text-sm font-bold text-white">{formatCurrency(totalForecastValue)}</div>
          <div className="text-xs text-surface-500 uppercase tracking-wider">Total Forecast</div>
        </div>
        <div className="bg-surface-900/50 rounded-lg px-2.5 py-2 text-center">
          <div className="text-sm font-bold text-red-400">{formatCurrency(valueAtRisk)}</div>
          <div className="text-xs text-surface-500 uppercase tracking-wider">$ Under Review</div>
        </div>
        <div className="bg-surface-900/50 rounded-lg px-2.5 py-2 text-center">
          <div className="text-sm font-bold text-red-400">{highMat.length}</div>
          <div className="text-xs text-surface-500 uppercase tracking-wider">High Impact</div>
        </div>
        <div className="bg-surface-900/50 rounded-lg px-2.5 py-2 text-center">
          <div className="text-sm font-bold text-amber-400">{medMat.length}</div>
          <div className="text-xs text-surface-500 uppercase tracking-wider">Med Impact</div>
        </div>
      </div>

      {/* Two-column analysis */}
      <div className="grid grid-cols-2 gap-3">
        {/* Left: Category $ at risk */}
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <BarChart3 className="w-3 h-3 text-surface-500" />
            <span className="text-xs font-semibold text-surface-400 uppercase tracking-wider">$ at Risk by Category</span>
          </div>
          {topCategoriesByRisk.map(([cat, { value, count }]) => (
            <div key={cat} className="flex items-center gap-2">
              <span className="text-xs text-surface-400 w-20 truncate">{cat}</span>
              <div className="flex-1 h-1.5 bg-surface-700 rounded-full overflow-hidden">
                <div
                  className="h-full bg-red-500/60 rounded-full"
                  style={{ width: `${Math.min(100, (value / (valueAtRisk || 1)) * 100)}%` }}
                />
              </div>
              <span className="text-xs text-surface-300 font-mono w-14 text-right">{formatCurrency(value)}</span>
              <span className="text-xs text-surface-600">({count})</span>
            </div>
          ))}
        </div>

        {/* Right: Root cause distribution */}
        <div className="space-y-1.5">
          <div className="flex items-center gap-1.5">
            <PieChart className="w-3 h-3 text-surface-500" />
            <span className="text-xs font-semibold text-surface-400 uppercase tracking-wider">Issue Drivers</span>
          </div>
          {topRootCauses.map(([cause, count]) => (
            <div key={cause} className="flex items-center gap-2">
              <span className="text-xs text-surface-400 w-24 truncate">{rootCauseLabels[cause] || cause}</span>
              <div className="flex-1 h-1.5 bg-surface-700 rounded-full overflow-hidden">
                <div
                  className="h-full bg-amber-500/60 rounded-full"
                  style={{ width: `${Math.min(100, (count / flaggedAndReview.length) * 100)}%` }}
                />
              </div>
              <span className="text-xs text-surface-300 font-mono w-6 text-right">{count}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Key observations */}
      {(driverObservations.length > 0 || overriddenItems.length > 0) && (
        <div className="border-t border-surface-700/30 pt-2 space-y-1">
          <span className="text-xs font-semibold text-surface-400 uppercase tracking-wider">Key Observations</span>
          {driverObservations.map((obs, i) => (
            <div key={i} className="flex items-start gap-1.5 text-xs text-surface-300 leading-relaxed">
              <Activity className="w-3 h-3 text-deloitte-teal flex-shrink-0 mt-0.5" />
              {obs}
            </div>
          ))}
          {overriddenItems.length > 0 && (
            <div className="flex items-start gap-1.5 text-xs text-surface-300 leading-relaxed">
              <Edit3 className="w-3 h-3 text-cyan-400 flex-shrink-0 mt-0.5" />
              {overriddenItems.length} flagged item(s) have active manual overrides that may need re-validation
            </div>
          )}
        </div>
      )}
    </div>
  );
}
