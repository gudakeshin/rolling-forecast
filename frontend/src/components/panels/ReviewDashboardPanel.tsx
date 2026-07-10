import { useState, useCallback, useRef, useEffect } from 'react';
import {
  BarChart, Bar, LineChart, Line, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from 'recharts';
import {
  CheckCircle, AlertTriangle, XCircle, Shield,
  ChevronDown, ChevronUp, Sparkles, ThumbsUp,
  ThumbsDown, MessageSquare, Loader2,
  Bot, Eye, ArrowRight, Zap, RefreshCw, TrendingUp,
  TrendingDown, Activity, GitBranch, Edit3, Save, X,
  DollarSign, BarChart3, PieChart, FileText,
} from 'lucide-react';
import { apiPost } from '../../api/client';

const COLORS = {
  green: '#86BC25',
  amber: '#FFB547',
  red: '#E84855',
  teal: '#0076A8',
  coolGray: '#97999B',
  blue: '#62B5E5',
};

// ─── Types ─────────────────────────────────────────

interface AIAction {
  type: string;
  label: string;
  detail: string;
}

interface DriverDependency {
  name: string;
  category: string;
  relationship: string;
  weight: number;
  forecast_total: number;
}

interface ActualsTrend {
  periods: string[];
  values: number[];
  direction: string;
  direction_pct: number;
  mom_growth_pct: number | null;
  yoy_growth_pct: number | null;
  recent_avg: number;
  last_actual: number;
  last_period: string;
}

interface ActiveOverride {
  period: string;
  original_value: number;
  override_value: number;
  reason: string;
  delta_pct: number;
}

interface DriverContext {
  narrative: string;
  dependencies: DriverDependency[];
  actuals_trend: ActualsTrend | null;
  driver_inputs: { business_unit: string; field: string; value: number | null; reason: string }[];
  active_overrides: ActiveOverride[];
}

interface ReviewItem {
  id: string;
  line_item_id: number;
  line_item_name: string;
  account_code?: string;
  category: string;
  subcategory?: string;
  business_unit?: string;
  period_count: number;
  worst_period: string;
  p50_range: string;
  total_p50?: number;
  avg_p50?: number;
  avg_confidence: number;
  min_confidence: number;
  confidence_level: string;
  model_type: string;
  model_mape?: number;
  is_overridden: boolean;
  override_count: number;
  ai_recommendation: string;
  ai_reasoning: string;
  ai_risk_score: number;
  ai_actions?: AIAction[];
  materiality?: string;
  root_cause?: string;
  driver_context?: DriverContext;
  review_status: string | null;
  review_comment: string | null;
  reviewed_by: string | null;
  history?: { period: string; actual?: number; forecast?: number }[];
}

interface Props {
  data: {
    panel_type: string;
    title: string;
    data: {
      version: { id: string; name: string; status: string };
      buckets: {
        ai_approved: { items: ReviewItem[]; total: number; description: string };
        needs_review: { items: ReviewItem[]; total: number; description: string };
        flagged: { items: ReviewItem[]; total: number; description: string };
        already_reviewed: { items: ReviewItem[]; total: number; description: string };
      };
      confidence_trend: any[];
      category_flag_chart: any[];
      summary: Record<string, number | boolean>;
    };
  };
  onRefresh?: () => void;
}

// ─── Utilities ─────────────────────────────────────

function formatCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined) return '-';
  if (Math.abs(value) >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
}

function RiskBar({ score }: { score: number }) {
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

function AIBadge({ recommendation }: { recommendation: string }) {
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

function ConfBadge({ score }: { score: number }) {
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

function MaterialityDot({ level }: { level?: string }) {
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

const ChartTooltip = ({ active, payload, label }: any) => {
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

// ─── Business Analysis Summary ─────────────────────

function BusinessAnalysisSummary({ buckets }: {
  buckets: Props['data']['data']['buckets'];
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

// ─── Review Item Row ───────────────────────────────

function ReviewItemRow({
  item,
  onAction,
  isActioning,
}: {
  item: ReviewItem;
  onAction: (id: string, action: string, comment?: string) => void;
  isActioning: string | null;
}) {
  const [expanded, setExpanded] = useState(false);
  const [commenting, setCommenting] = useState(false);
  const [comment, setComment] = useState('');
  const isLoading = isActioning === item.id;

  // Inline edit state
  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const [editReason, setEditReason] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [savedValue, setSavedValue] = useState<number | null>(null);
  const editRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isEditing && editRef.current) {
      editRef.current.focus();
      editRef.current.select();
    }
  }, [isEditing]);

  const handleStartEdit = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    const val = savedValue ?? item.avg_p50 ?? 0;
    setEditValue(val.toString());
    setEditReason('');
    setIsEditing(true);
  }, [savedValue, item.avg_p50]);

  const handleSaveEdit = useCallback(async (e?: React.MouseEvent) => {
    e?.stopPropagation();
    const numVal = parseFloat(editValue);
    if (isNaN(numVal) || editReason.trim().length < 10) return;
    setIsSaving(true);
    try {
      await apiPost('/panel/inline-override', {
        result_id: item.id,
        new_value: numVal,
        reason: editReason.trim(),
        apply_to: 'all',
      });
      setSavedValue(numVal);
      setIsEditing(false);
    } catch (err) {
      console.error('Override failed:', err);
    } finally {
      setIsSaving(false);
    }
  }, [editValue, editReason, item.id]);

  const handleCancelEdit = useCallback((e?: React.MouseEvent) => {
    e?.stopPropagation();
    setIsEditing(false);
  }, []);

  const displayValue = savedValue ?? item.avg_p50;
  const wasOverridden = savedValue !== null;
  const driverCtx = item.driver_context;

  return (
    <div className="border-b border-surface-700/20 last:border-0">
      {/* Main row */}
      <div
        className="flex items-center gap-2 px-3 py-2 hover:bg-deloitte-green/5 transition-colors cursor-pointer"
        onClick={() => setExpanded(!expanded)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setExpanded(!expanded);
          }
        }}
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
      >
        <span className="flex-shrink-0 text-surface-500" aria-hidden="true">
          {expanded ? <ChevronUp className="w-3 h-3" /> : <ChevronDown className="w-3 h-3" />}
        </span>

        {/* AI recommendation */}
        <div className="flex-shrink-0 w-16">
          <AIBadge recommendation={item.ai_recommendation} />
        </div>

        {/* Name + business context */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <MaterialityDot level={item.materiality} />
            <span className="text-xs text-surface-200 truncate">{item.line_item_name}</span>
          </div>
          <span className="text-xs text-surface-500">
            {item.category}
            {item.business_unit ? ` · ${item.business_unit}` : ''}
            {' · '}{item.period_count} periods
          </span>
        </div>

        {/* Forecast value (editable) */}
        {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions -- event barrier only */}
        <div className="flex-shrink-0 w-20 text-right" onMouseDown={(e) => e.stopPropagation()}>
          {isEditing ? (
            <div className="space-y-1">
              <input
                ref={editRef}
                type="number"
                value={editValue}
                onChange={(e) => setEditValue(e.target.value)}
                className="w-full px-1 py-0.5 bg-surface-800 border border-cyan-500/50 rounded text-xs text-white text-right font-mono focus:outline-none"
                onKeyDown={(e) => {
                  if (e.key === 'Escape') handleCancelEdit();
                  if (e.key === 'Enter' && editReason.trim().length >= 10) handleSaveEdit();
                }}
              />
              <input
                type="text"
                placeholder="Reason (min 10 chars)"
                value={editReason}
                onChange={(e) => setEditReason(e.target.value)}
                className="w-full px-1 py-0.5 bg-surface-800 border border-surface-600 rounded text-xs text-surface-300 placeholder-surface-600 focus:outline-none focus:border-cyan-500/50"
                onKeyDown={(e) => {
                  if (e.key === 'Escape') handleCancelEdit();
                  if (e.key === 'Enter' && editReason.trim().length >= 10) handleSaveEdit();
                }}
              />
              <div className="flex gap-1 justify-end">
                <button
                  type="button"
                  onClick={handleSaveEdit}
                  disabled={isSaving || editReason.trim().length < 10}
                  className="p-0.5 bg-deloitte-green/20 text-deloitte-green rounded hover:bg-deloitte-green/30 disabled:opacity-30"
                >
                  {isSaving ? <Loader2 className="w-2.5 h-2.5 animate-spin" /> : <Save className="w-2.5 h-2.5" />}
                </button>
                <button type="button" onClick={handleCancelEdit} className="p-0.5 bg-surface-700 text-surface-400 rounded hover:bg-surface-600">
                  <X className="w-2.5 h-2.5" />
                </button>
              </div>
            </div>
          ) : (
            <button
              type="button"
              className="group/val cursor-text w-full text-right"
              onClick={handleStartEdit}
              title="Click to edit"
            >
              <span className={`text-xs font-mono font-medium ${wasOverridden ? 'text-cyan-400' : 'text-surface-200'} group-hover/val:text-cyan-300 group-hover/val:underline group-hover/val:decoration-dashed group-hover/val:underline-offset-2`}>
                {formatCurrency(displayValue)}
                <Edit3 className="w-2 h-2 inline-block ml-0.5 opacity-0 group-hover/val:opacity-50" />
              </span>
              {wasOverridden && (
                <span className="block text-xs text-surface-500 line-through">
                  was {formatCurrency(item.avg_p50)}
                </span>
              )}
            </button>
          )}
        </div>

        {/* Risk score */}
        <div className="flex-shrink-0 w-20">
          <RiskBar score={item.ai_risk_score} />
        </div>

        {/* Confidence */}
        <div className="flex-shrink-0 w-10 text-center">
          <ConfBadge score={item.avg_confidence} />
        </div>

        {/* Actions */}
        {/* eslint-disable-next-line jsx-a11y/no-static-element-interactions -- event barrier only */}
        <div className="flex-shrink-0 flex items-center gap-1" onMouseDown={(e) => e.stopPropagation()}>
          {item.review_status ? (
            <span className={`text-xs font-semibold px-2 py-0.5 rounded ${
              item.review_status === 'approved'
                ? 'bg-deloitte-green/15 text-deloitte-green'
                : 'bg-red-500/15 text-red-400'
            }`}>
              {item.review_status === 'approved' ? '✓ Done' : '✗ Rej'}
            </span>
          ) : isLoading ? (
            <Loader2 className="w-3.5 h-3.5 animate-spin text-deloitte-green" />
          ) : (
            <>
              <button
                onClick={() => onAction(item.id, 'approve')}
                className="p-1 hover:bg-deloitte-green/20 rounded text-deloitte-green/60 hover:text-deloitte-green transition-colors"
                title="Approve"
              >
                <ThumbsUp className="w-3.5 h-3.5" />
              </button>
              <button
                onClick={() => setCommenting(true)}
                className="p-1 hover:bg-amber-500/20 rounded text-amber-500/60 hover:text-amber-400 transition-colors"
                title="Comment & Reject"
              >
                <ThumbsDown className="w-3.5 h-3.5" />
              </button>
            </>
          )}
        </div>
      </div>

      {/* Expanded detail */}
      {expanded && (
        <div className="px-3 pb-3 ml-5 space-y-2.5">
          {/* Business Driver Context */}
          {driverCtx && driverCtx.narrative && (
            <div className="bg-surface-800/60 border border-deloitte-teal/20 rounded-lg p-2.5 space-y-2">
              <div className="flex items-center gap-1.5">
                <Activity className="w-3.5 h-3.5 text-deloitte-teal" />
                <span className="text-xs font-semibold text-deloitte-teal uppercase tracking-wider">Business Drivers</span>
              </div>

              <p className="text-xs text-surface-300 leading-relaxed">
                {driverCtx.narrative}
              </p>

              {/* Dependencies */}
              {driverCtx.dependencies.length > 0 && (
                <div className="flex items-center gap-2 flex-wrap">
                  <GitBranch className="w-3 h-3 text-surface-500 flex-shrink-0" />
                  <span className="text-xs text-surface-500">Composed of:</span>
                  {driverCtx.dependencies.map((dep, i) => (
                    <span key={i} className="text-xs px-1.5 py-0.5 bg-surface-700/80 rounded text-surface-300 font-mono">
                      {dep.relationship === 'subtract' ? '−' : '+'} {dep.name}
                      {dep.forecast_total ? ` (${formatCurrency(dep.forecast_total)})` : ''}
                    </span>
                  ))}
                </div>
              )}

              {/* Actuals trend */}
              {driverCtx.actuals_trend && (
                <div className="flex items-center gap-3 text-xs flex-wrap">
                  {driverCtx.actuals_trend.direction === 'upward' ? (
                    <TrendingUp className="w-3 h-3 text-deloitte-green flex-shrink-0" />
                  ) : driverCtx.actuals_trend.direction === 'downward' ? (
                    <TrendingDown className="w-3 h-3 text-red-400 flex-shrink-0" />
                  ) : (
                    <Activity className="w-3 h-3 text-surface-500 flex-shrink-0" />
                  )}
                  <span className="text-surface-400">
                    Last actual: <span className="text-surface-200 font-mono">{formatCurrency(driverCtx.actuals_trend.last_actual)}</span>
                    <span className="text-surface-600 mx-1">|</span>
                    Avg: <span className="text-surface-200 font-mono">{formatCurrency(driverCtx.actuals_trend.recent_avg)}</span>/mo
                  </span>
                  {driverCtx.actuals_trend.mom_growth_pct != null && (
                    <span className={`font-semibold ${driverCtx.actuals_trend.mom_growth_pct >= 0 ? 'text-deloitte-green' : 'text-red-400'}`}>
                      MoM {driverCtx.actuals_trend.mom_growth_pct > 0 ? '+' : ''}{driverCtx.actuals_trend.mom_growth_pct.toFixed(1)}%
                    </span>
                  )}
                  {driverCtx.actuals_trend.yoy_growth_pct != null && (
                    <span className={`font-semibold ${driverCtx.actuals_trend.yoy_growth_pct >= 0 ? 'text-deloitte-green' : 'text-red-400'}`}>
                      YoY {driverCtx.actuals_trend.yoy_growth_pct > 0 ? '+' : ''}{driverCtx.actuals_trend.yoy_growth_pct.toFixed(1)}%
                    </span>
                  )}
                </div>
              )}

              {/* Active overrides */}
              {driverCtx.active_overrides.length > 0 && (
                <div className="flex items-start gap-2 text-xs">
                  <Edit3 className="w-3 h-3 text-cyan-400 flex-shrink-0 mt-0.5" />
                  <div className="text-surface-400">
                    <span className="text-cyan-400 font-semibold">Active overrides: </span>
                    {driverCtx.active_overrides.slice(0, 2).map((ov, i) => (
                      <span key={i}>
                        {i > 0 && ' · '}
                        {ov.period}: {formatCurrency(ov.original_value)} → <span className="text-cyan-300">{formatCurrency(ov.override_value)}</span>
                        {ov.reason && <span className="italic text-surface-500"> — {ov.reason.slice(0, 40)}{ov.reason.length > 40 ? '…' : ''}</span>}
                      </span>
                    ))}
                  </div>
                </div>
              )}

              {/* Driver inputs */}
              {driverCtx.driver_inputs.length > 0 && (
                <div className="flex items-start gap-2 text-xs">
                  <DollarSign className="w-3 h-3 text-amber-400 flex-shrink-0 mt-0.5" />
                  <div className="text-surface-400">
                    <span className="text-amber-400 font-semibold">BU inputs: </span>
                    {driverCtx.driver_inputs.slice(0, 2).map((di, i) => (
                      <span key={i}>
                        {i > 0 && ' · '}
                        {di.business_unit}: {di.reason || `value=${di.value}`}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </div>
          )}

          {/* AI reasoning */}
          <div className="flex items-start gap-2 p-2 bg-surface-800/80 border border-surface-700/40 rounded-lg">
            <Bot className="w-3.5 h-3.5 text-deloitte-teal mt-0.5 flex-shrink-0" />
            <div className="flex-1">
              <div className="flex items-center gap-2 flex-wrap">
                <span className="text-xs font-semibold text-deloitte-teal uppercase tracking-wider">Statistical Analysis</span>
                {item.root_cause && item.root_cause !== 'none' && (
                  <span className="text-xs font-semibold text-amber-400 bg-amber-500/10 px-1.5 py-0.5 rounded border border-amber-500/20">
                    {item.root_cause.replace(/_/g, ' ')}
                  </span>
                )}
                {item.materiality && item.materiality !== 'low' && (
                  <span className={`text-xs font-semibold uppercase px-1.5 py-0.5 rounded border ${
                    item.materiality === 'high'
                      ? 'text-red-400 bg-red-500/10 border-red-500/20'
                      : 'text-amber-400 bg-amber-500/10 border-amber-500/20'
                  }`}>
                    {item.materiality} materiality
                  </span>
                )}
              </div>
              {item.ai_reasoning.includes(' | ') ? (
                <ul className="mt-1 space-y-0.5">
                  {item.ai_reasoning.split(' | ').map((part, i) => (
                    <li key={i} className="flex items-start gap-1.5 text-xs text-surface-300 leading-relaxed">
                      <span className="w-1 h-1 rounded-full bg-surface-500 flex-shrink-0 mt-1.5" />
                      {part}
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="text-xs text-surface-300 mt-0.5 leading-relaxed">{item.ai_reasoning}</p>
              )}
            </div>
          </div>

          {/* Structured actions */}
          {item.ai_actions && item.ai_actions.length > 0 && item.ai_recommendation !== 'approve' && (
            <div className="bg-surface-800/50 border border-cyan-500/15 rounded-lg p-2.5">
              <div className="flex items-center gap-1.5 mb-2">
                <ArrowRight className="w-3 h-3 text-cyan-400" />
                <span className="text-xs font-semibold text-cyan-400 uppercase tracking-wider">Recommended Actions</span>
              </div>
              <div className="space-y-1.5">
                {item.ai_actions.map((action, idx) => (
                  <div key={idx} className="flex items-start gap-2">
                    <span className="text-xs font-bold text-surface-500 mt-0.5">{idx + 1}.</span>
                    <div>
                      <span className="text-xs font-semibold text-surface-200">{action.label}</span>
                      <p className="text-xs text-surface-400 leading-relaxed">{action.detail}</p>
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Detail cards */}
          <div className="grid grid-cols-4 gap-2 text-xs">
            <div className="bg-surface-800/50 rounded px-2 py-1.5">
              <span className="text-surface-500 block">Forecast Range</span>
              <span className="text-surface-200 font-mono">{item.p50_range}</span>
            </div>
            <div className="bg-surface-800/50 rounded px-2 py-1.5">
              <span className="text-surface-500 block">Model</span>
              <span className="text-surface-200">{item.model_type || 'N/A'}</span>
            </div>
            <div className="bg-surface-800/50 rounded px-2 py-1.5">
              <span className="text-surface-500 block">MAPE</span>
              <span className="text-surface-200">{item.model_mape != null ? `${item.model_mape.toFixed(1)}%` : 'N/A'}</span>
            </div>
            <div className="bg-surface-800/50 rounded px-2 py-1.5">
              <span className="text-surface-500 block">Overrides</span>
              <span className="text-surface-200">{item.override_count > 0 ? `${item.override_count} periods` : 'None'}</span>
            </div>
          </div>

          {/* Historical Context Mini-Chart */}
          {item.history && item.history.length > 1 && (
            <div className="bg-surface-800/50 border border-surface-700/30 rounded-lg p-2.5">
              <div className="flex items-center gap-1.5 mb-1.5">
                <TrendingUp className="w-3 h-3 text-deloitte-teal" />
                <span className="text-xs font-semibold text-surface-400 uppercase tracking-wider">Historical Context</span>
              </div>
              <ResponsiveContainer width="100%" height={80}>
                <AreaChart data={item.history} margin={{ top: 2, right: 5, left: -20, bottom: 2 }}>
                  <defs>
                    <linearGradient id={`histGrad-${item.id}`} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="5%" stopColor="#0076A8" stopOpacity={0.2} />
                      <stop offset="95%" stopColor="#0076A8" stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="period" tick={{ fill: '#97999B', fontSize: 8 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: '#97999B', fontSize: 8 }} axisLine={false} tickLine={false} />
                  <Tooltip
                    contentStyle={{ background: '#1a1d21', border: '1px solid #3a3d42', borderRadius: '8px', fontSize: '10px' }}
                    labelStyle={{ color: '#97999B', fontSize: '9px' }}
                  />
                  {item.history[0]?.actual !== undefined && (
                    <Area type="monotone" dataKey="actual" name="Actual" stroke="#FFB547" fill="none" strokeWidth={1.5} dot={{ r: 2, fill: '#FFB547' }} />
                  )}
                  <Area type="monotone" dataKey="forecast" name="Forecast" stroke="#0076A8" fill={`url(#histGrad-${item.id})`} strokeWidth={1.5} dot={{ r: 2, fill: '#0076A8' }} />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Review comment */}
          {item.review_comment && (
            <div className="flex items-start gap-1.5 text-xs text-surface-400">
              <MessageSquare className="w-3 h-3 mt-0.5 flex-shrink-0" />
              <span>"{item.review_comment}"</span>
            </div>
          )}
        </div>
      )}

      {/* Rejection comment */}
      {commenting && (
        // eslint-disable-next-line jsx-a11y/no-static-element-interactions -- event barrier only
        <div className="px-3 pb-2 ml-5" onMouseDown={(e) => e.stopPropagation()}>
          <div className="flex gap-2">
            <input
              type="text"
              value={comment}
              onChange={(e) => setComment(e.target.value)}
              placeholder="Rejection reason..."
              className="flex-1 px-2 py-1 bg-surface-800 border border-surface-600 rounded text-xs text-white placeholder-surface-500 focus:outline-none focus:border-amber-500/50"
              onKeyDown={(e) => {
                if (e.key === 'Enter' && comment.trim()) {
                  onAction(item.id, 'reject', comment);
                  setCommenting(false);
                  setComment('');
                }
              }}
            />
            <button
              type="button"
              onClick={() => { onAction(item.id, 'reject', comment); setCommenting(false); setComment(''); }}
              disabled={!comment.trim()}
              className="px-2 py-1 bg-red-500/20 text-red-400 rounded text-xs hover:bg-red-500/30 disabled:opacity-40 transition-colors"
            >
              Reject
            </button>
            <button
              type="button"
              onClick={() => { setCommenting(false); setComment(''); }}
              className="px-2 py-1 bg-surface-700 text-surface-400 rounded text-xs hover:bg-surface-600 transition-colors"
            >
              Cancel
            </button>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Main Component ────────────────────────────────

type BucketKey = 'flagged' | 'needs_review' | 'ai_approved' | 'already_reviewed';

export function ReviewDashboardPanel({ data, onRefresh }: Props) {
  const { version, buckets, confidence_trend, category_flag_chart, summary } = data.data;
  const [activeBucket, setActiveBucket] = useState<BucketKey>('flagged');
  const [actioningItem, setActioningItem] = useState<string | null>(null);
  const [isAcceptingAll, setIsAcceptingAll] = useState(false);
  const [isRescoring, setIsRescoring] = useState(false);
  const [localBuckets, setLocalBuckets] = useState(buckets);

  const handleRescore = useCallback(async () => {
    setIsRescoring(true);
    try {
      await apiPost(`/panel/rescore-forecasts/${version.id}`, {});
      onRefresh?.();
    } catch (error) {
      console.error('Rescore failed:', error);
    } finally {
      setIsRescoring(false);
    }
  }, [version.id, onRefresh]);

  const handleItemAction = useCallback(async (itemId: string, action: string, comment?: string) => {
    setActioningItem(itemId);
    try {
      await apiPost('/panel/review-item', { item_id: itemId, action, comment });

      setLocalBuckets((prev) => {
        const updated = { ...prev };
        for (const key of ['flagged', 'needs_review', 'ai_approved'] as BucketKey[]) {
          const bucket = updated[key];
          const idx = bucket.items.findIndex((i: ReviewItem) => i.id === itemId);
          if (idx >= 0) {
            const [item] = bucket.items.splice(idx, 1);
            bucket.total -= 1;
            item.review_status = action === 'approve' ? 'approved' : 'rejected';
            item.review_comment = comment || null;
            updated.already_reviewed = {
              ...updated.already_reviewed,
              items: [item, ...updated.already_reviewed.items],
              total: updated.already_reviewed.total + 1,
            };
            break;
          }
        }
        return { ...updated };
      });
    } catch (error) {
      console.error('Review action failed:', error);
    } finally {
      setActioningItem(null);
    }
  }, []);

  const handleAcceptAll = useCallback(async () => {
    setIsAcceptingAll(true);
    try {
      await apiPost('/panel/accept-ai-recommendations', { version_id: version.id });

      setLocalBuckets((prev) => {
        const approved = prev.ai_approved.items.map((i: ReviewItem) => ({
          ...i,
          review_status: 'approved',
          review_comment: 'Auto-approved based on AI recommendation',
        }));
        return {
          ...prev,
          ai_approved: { ...prev.ai_approved, items: [], total: 0 },
          already_reviewed: {
            ...prev.already_reviewed,
            items: [...approved, ...prev.already_reviewed.items],
            total: prev.already_reviewed.total + approved.length,
          },
        };
      });
    } catch (error) {
      console.error('Accept all failed:', error);
    } finally {
      setIsAcceptingAll(false);
    }
  }, [version.id]);

  const bucketConfig: { key: BucketKey; label: string; icon: any; color: string; bg: string }[] = [
    { key: 'flagged', label: 'Flagged', icon: XCircle, color: 'text-red-400', bg: 'bg-red-500/10 border-red-500/20' },
    { key: 'needs_review', label: 'Needs Review', icon: AlertTriangle, color: 'text-amber-400', bg: 'bg-amber-500/10 border-amber-500/20' },
    { key: 'ai_approved', label: 'AI Approved', icon: Sparkles, color: 'text-deloitte-green', bg: 'bg-deloitte-green/10 border-deloitte-green/20' },
    { key: 'already_reviewed', label: 'Reviewed', icon: Shield, color: 'text-blue-400', bg: 'bg-blue-500/10 border-blue-500/20' },
  ];

  const activeBucketData = localBuckets[activeBucket];

  return (
    <div className="space-y-3">
      {/* AI Analysis Banner */}
      <div className="flex items-center gap-2 px-3 py-2 bg-deloitte-green/8 border border-deloitte-green/20 rounded-xl">
        <Bot className="w-4 h-4 text-deloitte-green flex-shrink-0" />
        <div className="flex-1">
          <span className="text-xs font-semibold text-deloitte-green">AI Review Complete</span>
          <p className="text-xs text-surface-400">
            Analyzed {summary.total_line_items} line items —{' '}
            <span className="text-deloitte-green font-medium">{localBuckets.ai_approved.total} auto-approvable</span>,{' '}
            <span className="text-amber-400 font-medium">{localBuckets.needs_review.total} need review</span>,{' '}
            <span className="text-red-400 font-medium">{localBuckets.flagged.total} flagged</span>
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={handleRescore}
            disabled={isRescoring}
            className="flex items-center gap-1 px-2 py-1.5 bg-surface-700/60 border border-surface-600/50 text-surface-300 text-xs font-medium rounded-lg hover:bg-surface-700 disabled:opacity-50 transition-colors whitespace-nowrap"
            title="Re-run AI analysis with latest data"
          >
            {isRescoring ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
            Rescore
          </button>
          {localBuckets.ai_approved.total > 0 && (
            <button
              onClick={handleAcceptAll}
              disabled={isAcceptingAll}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-deloitte-green text-black text-xs font-semibold rounded-lg hover:bg-deloitte-green/90 disabled:opacity-50 transition-colors whitespace-nowrap"
            >
              {isAcceptingAll ? <Loader2 className="w-3 h-3 animate-spin" /> : <Zap className="w-3 h-3" />}
              Accept All AI ({localBuckets.ai_approved.total})
            </button>
          )}
        </div>
      </div>

      {/* Business Analysis Summary */}
      <BusinessAnalysisSummary buckets={localBuckets} />

      {/* Bucket tabs */}
      <div className="grid grid-cols-4 gap-1.5">
        {bucketConfig.map(({ key, label, icon: Icon, color, bg }) => (
          <button
            key={key}
            onClick={() => setActiveBucket(key)}
            className={`p-2 border rounded-xl text-center transition-all ${bg} ${
              activeBucket === key ? 'ring-1 ring-white/20 scale-[1.02]' : 'opacity-70 hover:opacity-100'
            }`}
          >
            <Icon className={`w-3.5 h-3.5 mx-auto mb-0.5 ${color}`} />
            <div className={`text-base font-bold ${color}`}>{localBuckets[key].total}</div>
            <div className="text-xs text-surface-400 uppercase tracking-wider font-medium leading-tight">{label}</div>
          </button>
        ))}
      </div>

      {/* Active bucket content */}
      <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl overflow-hidden">
        <div className="px-3 py-2 border-b border-surface-700/50 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Sparkles className="w-3 h-3 text-deloitte-green" />
            <span className="text-xs font-semibold text-white">
              {bucketConfig.find(b => b.key === activeBucket)?.label} ({activeBucketData.total})
            </span>
          </div>
          {activeBucket !== 'already_reviewed' && activeBucketData.items.length > 0 && (
            <span className="text-xs text-surface-500 italic">
              {activeBucketData.description}
            </span>
          )}
        </div>

        {/* Column headers */}
        {activeBucketData.items.length > 0 && (
          <div className="flex items-center gap-2 px-3 py-1.5 bg-surface-800/80 border-b border-surface-700/30 text-xs text-surface-500 uppercase tracking-wider font-semibold">
            <span className="w-4" />
            <span className="w-16">AI</span>
            <span className="flex-1">Line Item</span>
            <span className="w-20 text-right">Forecast</span>
            <span className="w-20 text-center">Risk</span>
            <span className="w-10 text-center">Conf</span>
            <span className="w-16 text-center">Action</span>
          </div>
        )}

        <div className="max-h-[320px] overflow-y-auto">
          {activeBucketData.items.length > 0 ? (
            activeBucketData.items.map((item: ReviewItem) => (
              <ReviewItemRow
                key={item.id}
                item={item}
                onAction={handleItemAction}
                isActioning={actioningItem}
              />
            ))
          ) : (
            <div className="py-8 text-center">
              <CheckCircle className="w-6 h-6 text-deloitte-green/40 mx-auto mb-2" />
              <p className="text-xs text-surface-500">No items in this bucket</p>
            </div>
          )}
        </div>
      </div>

      {/* Charts row */}
      <div className="grid grid-cols-2 gap-3">
        <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3">
          <h4 className="text-xs font-semibold text-surface-400 uppercase tracking-wider mb-2">Confidence Trend</h4>
          {confidence_trend.length > 1 ? (
            <ResponsiveContainer width="100%" height={120}>
              <LineChart data={confidence_trend} margin={{ top: 5, right: 5, left: -15, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
                <XAxis dataKey="version" tick={{ fill: '#97999B', fontSize: 9 }} />
                <YAxis tick={{ fill: '#97999B', fontSize: 9 }} domain={[0, 100]} />
                <Tooltip content={<ChartTooltip />} />
                <Line type="monotone" dataKey="avg_confidence" name="Avg Confidence" stroke={COLORS.green} strokeWidth={2} dot={{ r: 3 }} />
              </LineChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-surface-500 text-xs text-center py-4">More versions needed for trend</p>
          )}
        </div>

        <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3">
          <h4 className="text-xs font-semibold text-surface-400 uppercase tracking-wider mb-2">Issues by Category</h4>
          {category_flag_chart.length > 0 ? (
            <ResponsiveContainer width="100%" height={120}>
              <BarChart data={category_flag_chart.slice(0, 6)} layout="vertical" margin={{ left: 60, right: 5, top: 5, bottom: 5 }}>
                <XAxis type="number" tick={{ fill: '#97999B', fontSize: 9 }} />
                <YAxis type="category" dataKey="category" tick={{ fill: '#97999B', fontSize: 9 }} width={55} />
                <Tooltip content={<ChartTooltip />} />
                <Bar dataKey="count" name="Issues" fill={COLORS.red} radius={[0, 3, 3, 0]} />
              </BarChart>
            </ResponsiveContainer>
          ) : (
            <p className="text-surface-500 text-xs text-center py-4">No flagged items</p>
          )}
        </div>
      </div>
    </div>
  );
}
