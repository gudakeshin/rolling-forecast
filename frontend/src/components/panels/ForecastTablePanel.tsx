import { useState, useCallback, useRef, useEffect, useMemo } from 'react';
import {
  AlertTriangle, CheckCircle2, XCircle, ChevronDown, ChevronRight,
  Edit3, ShieldCheck, Eye, Filter, BarChart3, TrendingDown, TrendingUp,
  Bot, Sparkles, Info, MessageSquare, Upload, RefreshCw, ArrowRightLeft,
  UserCheck, Search, Zap, CircleDot, Save, X, GitBranch, Activity,
  Loader2,
} from 'lucide-react';
import { apiPost } from '../../api/client';
import { usePanelStore } from '../../store/panelStore';
import { toast } from '../../store/toastStore';
import { DataTable, type DataTableColumn } from '../ui/DataTable';

// ── Types ────────────────────────────────────
interface QualitySummary {
  avg_confidence: number;
  critical_count: number;
  warning_count: number;
  ok_count: number;
  total_scored: number;
}

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

interface DriverInput {
  business_unit: string;
  field: string;
  value: number | null;
  reason: string;
  prior_value: number | null;
  submitted_at: string | null;
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
  driver_inputs: DriverInput[];
  active_overrides: ActiveOverride[];
}

interface ExogModeScores {
  n_folds: number;
  mase_forecast: number | null;
  mase_known: number | null;
}

interface ExogInfo {
  mode?: string;
  columns?: string[];
  drivers?: Array<{
    driver_id?: number;
    driver_key?: string;
    relation?: string;
    lag?: number;
  }>;
  exog_mode_scores?: ExogModeScores | null;
}

interface ForecastRow {
  id: string;
  line_item_id: number;
  line_item_name: string;
  account_code?: string;
  category: string;
  period?: string;
  periods?: string;
  period_count?: number;
  p50?: number;
  total_p50?: number;
  avg_p50?: number;
  p10?: number;
  p90?: number;
  p10_range?: number;
  p90_range?: number;
  confidence_score?: number;
  min_confidence?: number;
  avg_confidence?: number;
  confidence_level: string;
  model_type: string;
  model_mape?: number;
  is_overridden: boolean;
  override_value?: number;
  override_count?: number;
  indent_level?: number;
  is_subtotal?: boolean;
  ai_recommendation?: string;
  ai_reasoning?: string;
  ai_risk_score?: number;
  ai_actions?: AIAction[];
  materiality?: string;
  root_cause?: string;
  driver_context?: DriverContext;
  review_status?: string;
  exog_used?: boolean;
  exog?: ExogInfo | null;
}

interface VersionInfo {
  id: string;
  name: string;
  status: string;
  total_line_items?: number;
  high_confidence_count?: number;
  medium_confidence_count?: number;
  low_confidence_count?: number;
}

interface Props {
  data: {
    panel_type: string;
    title: string;
    data: {
      version?: VersionInfo;
      quality_summary?: QualitySummary;
      rows?: ForecastRow[];
      items?: ForecastRow[];
      view?: string;
      total_count?: number;
      limit?: number;
      offset?: number;
      has_more?: boolean;
      available_categories?: string[];
    };
  };
  onRefresh?: () => void;
  enablePolling?: boolean;
}

type ForecastTableRow = ForecastRow & Record<string, unknown>;

// ── Main Component ───────────────────────────
export function ForecastTablePanel({ data, onRefresh, enablePolling }: Props) {
  const rows = data.data.rows || data.data.items || [];
  const version = data.data.version;
  const quality = data.data.quality_summary;
  const isSummaryView = data.data.view === 'summary' || (!data.data.view && rows.length > 0 && rows[0]?.period_count);
  const categories = data.data.available_categories || [];
  const hasMore = Boolean(data.data.has_more);
  const pageLimit = data.data.limit || 100;

  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [filterCategory, setFilterCategory] = useState<string>(
    typeof usePanelStore.getState().panelParams?.category === 'string'
      ? usePanelStore.getState().panelParams.category
      : 'all',
  );
  const [filterConfidence, setFilterConfidence] = useState<string>(
    typeof usePanelStore.getState().panelParams?.confidence_level === 'string'
      ? usePanelStore.getState().panelParams.confidence_level
      : 'all',
  );
  const [filterExog, setFilterExog] = useState<string>('all');
  const [sortBy, setSortBy] = useState<string>('default');
  const [actioningId, setActioningId] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const { openPanel, panelParams, setPanelParams } = usePanelStore();

  const pollEnabled = (enablePolling ?? true) && Boolean(onRefresh);

  useEffect(() => {
    if (!pollEnabled || !onRefresh) return;
    const id = window.setInterval(() => {
      void onRefresh();
    }, 60_000);
    return () => window.clearInterval(id);
  }, [pollEnabled, onRefresh]);

  const handleLoadMore = useCallback(() => {
    if (!version?.id || loadingMore) return;
    setLoadingMore(true);
    const nextOffset = (data.data.offset || 0) + pageLimit;
    setPanelParams({
      ...panelParams,
      version_id: version.id,
      offset: nextOffset,
      _append: true,
      _refresh: Date.now(),
    });
    // loading flag cleared when PanelContainer finishes (isLoading → false)
    setTimeout(() => setLoadingMore(false), 800);
  }, [version, loadingMore, data.data.offset, pageLimit, panelParams, setPanelParams]);

  const toggleExpand = useCallback((id: string) => {
    setExpandedRows(prev => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  }, []);

  const applyServerFilters = useCallback(
    (nextCategory: string, nextConfidence: string) => {
      const category =
        nextCategory !== 'all' ? nextCategory : undefined;
      const confidence_level =
        nextConfidence !== 'all' && nextConfidence !== 'action'
          ? nextConfidence
          : undefined;
      setPanelParams({
        ...panelParams,
        version_id: version?.id ?? panelParams.version_id,
        category,
        confidence_level,
        offset: 0,
        _append: false,
        _refresh: Date.now(),
      });
    },
    [panelParams, setPanelParams, version?.id],
  );

  // ── Sorting ──
  const exogGap = (row: ForecastRow): number | null => {
    const s = row.exog?.exog_mode_scores;
    if (!s) return null;
    const forecast = typeof s.mase_forecast === 'number' ? s.mase_forecast : null;
    const known = typeof s.mase_known === 'number' ? s.mase_known : null;
    if (forecast == null || known == null) return null;
    return forecast - known;
  };

  const displayRows =
    filterConfidence === 'action'
      ? rows.filter(
          (row) =>
            Boolean(row.ai_recommendation) && row.ai_recommendation !== 'approve',
        )
      : rows;

  const filteredByExog =
    filterExog === 'with_exog'
      ? displayRows.filter((row) => Boolean(row.exog_used))
      : filterExog === 'with_scores'
        ? displayRows.filter((row) => exogGap(row) != null)
        : displayRows;

  const sortedRows = [...filteredByExog].sort((a, b) => {
    if (sortBy === 'confidence_asc') return (a.min_confidence ?? a.confidence_score ?? 0) - (b.min_confidence ?? b.confidence_score ?? 0);
    if (sortBy === 'confidence_desc') return (b.min_confidence ?? b.confidence_score ?? 0) - (a.min_confidence ?? a.confidence_score ?? 0);
    if (sortBy === 'risk_desc') return (b.ai_risk_score ?? 0) - (a.ai_risk_score ?? 0);
    if (sortBy === 'value_desc') return Math.abs(b.total_p50 ?? b.p50 ?? 0) - Math.abs(a.total_p50 ?? a.p50 ?? 0);
    if (sortBy === 'exog_gap_desc') return Math.abs(exogGap(b) ?? -1) - Math.abs(exogGap(a) ?? -1);
    return 0; // default order from API
  });

  // ── Handle review action ──
  const handleReviewAction = useCallback(async (itemId: string, action: string) => {
    setActioningId(itemId);
    try {
      await apiPost('/panel/review-item', { item_id: itemId, action, comment: null });
      toast.success(action === 'approve' ? 'Item approved' : 'Review recorded');
      onRefresh?.();
    } catch (e: any) {
      toast.error(e?.message || 'Review action failed');
    } finally {
      setActioningId(null);
    }
  }, [onRefresh]);

  // ── Handle open review dashboard ──
  const handleOpenReview = useCallback(() => {
    if (version?.id) {
      openPanel('review_dashboard', { version_id: version.id });
    }
  }, [version, openPanel]);

  const tableRows = useMemo<ForecastTableRow[]>(
    () =>
      sortedRows.map((row) => ({
        ...row,
        line_item_name: row.line_item_name,
        category: row.category,
        period: row.period,
        period_count: row.period_count,
        p50: row.p50,
        total_p50: row.total_p50,
        p10: row.p10,
        p90: row.p90,
        confidence_level: row.confidence_level,
      })),
    [sortedRows],
  );

  const getRowClassName = useCallback((row: ForecastTableRow) => {
    const recommendation = row.ai_recommendation;
    const hasIssue = recommendation && recommendation !== 'approve';
    const parts: string[] = [];
    if (hasIssue) {
      parts.push(
        recommendation === 'override' || recommendation === 'manual_input'
          ? 'bg-red-500/5 hover:bg-red-500/10'
          : 'bg-amber-500/5 hover:bg-amber-500/8',
      );
    } else {
      parts.push('hover:bg-deloitte-green/5');
    }
    if (row.is_subtotal) parts.push('bg-surface-800/50');
    return parts.join(' ');
  }, []);

  const columns = useMemo<DataTableColumn<ForecastTableRow>[]>(() => {
    const forecastKey = isSummaryView ? 'total_p50' : 'p50';
    const periodKey = isSummaryView ? 'period_count' : 'period';
    const cols: DataTableColumn<ForecastTableRow>[] = [
      {
        key: 'line_item_name',
        label: 'Line Item',
        render: (_, row) => (
          <ForecastLineItemCell
            row={row}
            isExpanded={expandedRows.has(row.id)}
            onToggle={() => toggleExpand(row.id)}
          />
        ),
      },
      {
        key: periodKey,
        label: isSummaryView ? 'Periods' : 'Period',
        render: (_, row) => (
          <span className="text-surface-500 font-mono text-xs">
            {isSummaryView ? `${row.period_count}mo` : row.period}
          </span>
        ),
      },
      {
        key: forecastKey,
        label: 'Forecast (P50)',
        align: 'right',
        render: (_, row) => (
          <ForecastValueCell
            row={row}
            isSummary={!!isSummaryView}
            onOverrideSaved={onRefresh}
          />
        ),
      },
      {
        key: isSummaryView ? 'p10_range' : 'p10',
        label: 'P10',
        align: 'right',
        render: (_, row) => (
          <span className="font-mono text-surface-300">
            {formatCurrency(isSummaryView ? row.p10_range : row.p10)}
          </span>
        ),
      },
      {
        key: isSummaryView ? 'p90_range' : 'p90',
        label: 'P90',
        align: 'right',
        render: (_, row) => (
          <span className="font-mono text-surface-300">
            {formatCurrency(isSummaryView ? row.p90_range : row.p90)}
          </span>
        ),
      },
      {
        key: 'confidence_level',
        label: 'Confidence',
        align: 'center',
        render: (_, row) => (
          <ConfidenceIndicator
            score={row.min_confidence ?? row.confidence_score ?? 0}
            level={row.confidence_level || 'low'}
          />
        ),
      },
      {
        key: 'review_status',
        label: 'Status',
        align: 'center',
        render: (_, row) => (
          <StatusBadge
            recommendation={row.ai_recommendation}
            reviewStatus={row.review_status}
          />
        ),
      },
    ];
    return cols;
  }, [isSummaryView, expandedRows, toggleExpand, onRefresh]);

  if (rows.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center py-12 gap-3">
        <BarChart3 className="w-8 h-8 text-surface-600" />
        <p className="text-surface-500 text-sm">No forecast data to display</p>
      </div>
    );
  }

  return (
    <div className="space-y-3">
      {/* ── Quality Summary Banner ── */}
      {quality && (
        <QualitySummaryBanner quality={quality} onOpenReview={handleOpenReview} />
      )}

      {/* ── Version Stats (fallback when no quality_summary) ── */}
      {!quality && version && (
        <div className="grid grid-cols-3 gap-2">
          <StatCard label="Total Lines" value={version.total_line_items || 0} />
          <StatCard label="High Conf" value={version.high_confidence_count || 0} variant="green" />
          <StatCard label="Low Conf" value={version.low_confidence_count || 0} variant="red" />
        </div>
      )}

      {/* ── Filters & Sort ── */}
      <div className="flex items-center gap-2 flex-wrap">
        <Filter className="w-3.5 h-3.5 text-surface-500" />

        <select
          value={filterCategory}
          onChange={(e) => {
            const next = e.target.value;
            setFilterCategory(next);
            applyServerFilters(next, filterConfidence);
          }}
          className="text-xs bg-surface-800 border border-surface-700 text-surface-300 rounded-lg px-2 py-1.5 focus:border-deloitte-green/50 focus:outline-none"
        >
          <option value="all">All Categories</option>
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>

        <select
          value={filterConfidence}
          onChange={(e) => {
            const next = e.target.value;
            setFilterConfidence(next);
            applyServerFilters(filterCategory, next);
          }}
          className="text-xs bg-surface-800 border border-surface-700 text-surface-300 rounded-lg px-2 py-1.5 focus:border-deloitte-green/50 focus:outline-none"
        >
          <option value="all">All Confidence</option>
          <option value="high">High Only</option>
          <option value="medium">Medium Only</option>
          <option value="low">Low Only</option>
          <option value="action">Needs Action</option>
        </select>

        <select
          value={filterExog}
          onChange={(e) => setFilterExog(e.target.value)}
          className="text-xs bg-surface-800 border border-surface-700 text-surface-300 rounded-lg px-2 py-1.5 focus:border-deloitte-green/50 focus:outline-none"
        >
          <option value="all">All Exog Modes</option>
          <option value="with_exog">Exog Enabled</option>
          <option value="with_scores">Exog Scores Available</option>
        </select>

        <select
          value={sortBy}
          onChange={(e) => setSortBy(e.target.value)}
          className="text-xs bg-surface-800 border border-surface-700 text-surface-300 rounded-lg px-2 py-1.5 focus:border-deloitte-green/50 focus:outline-none"
        >
          <option value="default">Default Order</option>
          <option value="confidence_asc">Confidence: Low → High</option>
          <option value="confidence_desc">Confidence: High → Low</option>
          <option value="risk_desc">Risk: High → Low</option>
          <option value="value_desc">Value: High → Low</option>
          <option value="exog_gap_desc">Exog Gap: Largest First</option>
        </select>

        <span className="text-xs text-surface-500 ml-auto">
          {sortedRows.length} items
        </span>
      </div>

      <DataTable<ForecastTableRow>
        title={version?.name ? `Forecast — ${version.name}` : 'Forecast'}
        columns={columns}
        rows={tableRows}
        getRowId={(row) => row.id}
        getRowClassName={getRowClassName}
        expandedRowIds={expandedRows}
        renderExpandedRow={(row) =>
          row.ai_reasoning ? (
            <ExpandedRemediation
              row={row}
              reasoning={row.ai_reasoning}
              hasIssue={Boolean(row.ai_recommendation && row.ai_recommendation !== 'approve')}
              isReviewed={row.review_status === 'approved' || row.review_status === 'rejected'}
              isActioning={actioningId === row.id}
              onAction={handleReviewAction}
            />
          ) : null
        }
        rowActions={(row) => (
          <ForecastRowActions
            row={row}
            onAction={handleReviewAction}
            isActioning={actioningId === row.id}
            onToggle={() => toggleExpand(row.id)}
          />
        )}
        exportFilename={`forecast_${version?.name || 'export'}`}
        maxHeight={typeof window !== 'undefined' ? Math.max(400, window.innerHeight - 320) : 560}
        rowHeight={56}
      />

      <div className="flex flex-col items-center gap-2">
        <p className="text-xs text-surface-500 text-center">
          Showing {rows.length} items
          {data.data.total_count != null ? ` of ${data.data.total_count} total` : ''}
        </p>
        {hasMore && (
          <button
            type="button"
            onClick={handleLoadMore}
            disabled={loadingMore}
            className="text-xs font-medium px-3 py-1.5 rounded-lg border border-surface-600 text-surface-300 hover:border-deloitte-green/40 hover:text-white disabled:opacity-50"
          >
            {loadingMore ? 'Loading…' : 'Load more'}
          </button>
        )}
      </div>
    </div>
  );
}

// ── Quality Summary Banner ──────────────────
function QualitySummaryBanner({
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

// ── Line Item Cell ───────────────────────────
function ForecastLineItemCell({
  row,
  isExpanded,
  onToggle,
}: {
  row: ForecastRow;
  isExpanded: boolean;
  onToggle: () => void;
}) {
  const reasoning = row.ai_reasoning;
  return (
    <div className="text-surface-300 whitespace-normal">
      <div className="flex items-center gap-1.5" style={{ paddingLeft: `${(row.indent_level || 0) * 12}px` }}>
        {reasoning ? (
          <button
            type="button"
            onClick={onToggle}
            aria-expanded={isExpanded}
            aria-label={isExpanded ? 'Collapse row details' : 'Expand row details'}
            className="p-0.5 rounded text-surface-500 hover:text-surface-300 focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-deloitte-green"
          >
            {isExpanded
              ? <ChevronDown className="w-3 h-3 flex-shrink-0" />
              : <ChevronRight className="w-3 h-3 flex-shrink-0" />}
          </button>
        ) : (
          <span className="w-4" aria-hidden="true" />
        )}
        <span className={`truncate ${row.is_subtotal ? 'font-bold text-white' : ''}`}>
          {row.line_item_name}
        </span>
      </div>
    </div>
  );
}

// ── Editable Forecast Value Cell ─────────────
function ForecastValueCell({
  row,
  isSummary,
  onOverrideSaved,
}: {
  row: ForecastRow;
  isSummary: boolean;
  onOverrideSaved?: () => void;
}) {
  const forecastValue = isSummary ? row.total_p50 : row.p50;

  const [isEditing, setIsEditing] = useState(false);
  const [editValue, setEditValue] = useState('');
  const [editReason, setEditReason] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [savedValue, setSavedValue] = useState<number | null>(null);
  const editInputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (isEditing && editInputRef.current) {
      editInputRef.current.focus();
      editInputRef.current.select();
    }
  }, [isEditing]);

  const handleStartEdit = useCallback((e: React.MouseEvent) => {
    e.stopPropagation();
    if (row.is_subtotal) return;
    const val = savedValue ?? forecastValue;
    setEditValue(val != null ? val.toString() : '0');
    setEditReason('');
    setIsEditing(true);
  }, [forecastValue, savedValue, row.is_subtotal]);

  const handleCancelEdit = useCallback((e?: React.MouseEvent) => {
    e?.stopPropagation();
    setIsEditing(false);
    setEditValue('');
    setEditReason('');
  }, []);

  const handleSaveEdit = useCallback(async (e?: React.MouseEvent) => {
    e?.stopPropagation();
    const numVal = parseFloat(editValue);
    if (isNaN(numVal)) return;
    if (editReason.trim().length < 10) return;

    setIsSaving(true);
    try {
      await apiPost('/panel/inline-override', {
        result_id: row.id,
        new_value: numVal,
        reason: editReason.trim(),
        apply_to: isSummary ? 'all' : 'single',
      });
      setSavedValue(numVal);
      setIsEditing(false);
      toast.success('Override applied');
      onOverrideSaved?.();
    } catch (err: any) {
      toast.error(err?.message || 'Override failed');
    } finally {
      setIsSaving(false);
    }
  }, [editValue, editReason, row.id, isSummary, onOverrideSaved]);

  const displayValue = savedValue ?? forecastValue;
  const wasOverridden = savedValue !== null;

  return (
    <div className="whitespace-normal">
      {isEditing ? (
        <div className="space-y-1.5">
          <div className="flex items-center gap-1 justify-end">
            <span className="text-xs text-surface-500">$</span>
            <input
              ref={editInputRef}
              type="number"
              value={editValue}
              onChange={(e) => setEditValue(e.target.value)}
              className="w-24 px-1.5 py-1 bg-surface-800 border border-cyan-500/50 rounded text-xs text-white text-right font-mono focus:outline-none focus:border-cyan-400"
              onKeyDown={(e) => {
                if (e.key === 'Escape') handleCancelEdit();
                if (e.key === 'Enter' && editReason.trim().length >= 10) handleSaveEdit();
              }}
            />
          </div>
          <input
            type="text"
            placeholder="Reason for change (min 10 chars)"
            value={editReason}
            onChange={(e) => setEditReason(e.target.value)}
            className="w-full px-1.5 py-1 bg-surface-800 border border-surface-600 rounded text-xs text-surface-300 placeholder-surface-600 focus:outline-none focus:border-cyan-500/50"
            onKeyDown={(e) => {
              if (e.key === 'Escape') handleCancelEdit();
              if (e.key === 'Enter' && editReason.trim().length >= 10) handleSaveEdit();
            }}
          />
          <div className="flex items-center gap-1 justify-end">
            <button
              onClick={handleSaveEdit}
              disabled={isSaving || editReason.trim().length < 10 || isNaN(parseFloat(editValue))}
              className="flex items-center gap-0.5 px-1.5 py-0.5 bg-deloitte-green/20 text-deloitte-green text-xs rounded hover:bg-deloitte-green/30 disabled:opacity-30 transition-colors"
              title="Save override (Enter)"
            >
              {isSaving ? <Loader2 className="w-2.5 h-2.5 animate-spin" /> : <Save className="w-2.5 h-2.5" />}
              Save
            </button>
            <button
              onClick={handleCancelEdit}
              className="flex items-center gap-0.5 px-1.5 py-0.5 bg-surface-700 text-surface-400 text-xs rounded hover:bg-surface-600 transition-colors"
              title="Cancel (Esc)"
            >
              <X className="w-2.5 h-2.5" />
            </button>
          </div>
        </div>
      ) : (
        <button
          type="button"
          disabled={row.is_subtotal}
          className={`group/edit inline-flex flex-col items-end ${!row.is_subtotal ? 'cursor-text' : 'cursor-default'}`}
          onClick={!row.is_subtotal ? handleStartEdit : undefined}
          title={!row.is_subtotal ? 'Click to edit forecast value' : undefined}
        >
          <span className={`font-medium font-mono ${wasOverridden ? 'text-cyan-400' : 'text-surface-200'} ${!row.is_subtotal ? 'group-hover/edit:text-cyan-300 group-hover/edit:underline group-hover/edit:decoration-dashed group-hover/edit:underline-offset-2' : ''}`}>
            {formatCurrency(displayValue)}
            {!row.is_subtotal && (
              <Edit3 className="w-2.5 h-2.5 inline-block ml-1 opacity-0 group-hover/edit:opacity-60 transition-opacity" />
            )}
          </span>
          {wasOverridden && (
            <span className="text-xs text-surface-500 line-through">
              was {formatCurrency(forecastValue)}
            </span>
          )}
          {isSummary && row.avg_p50 != null && !wasOverridden && (
            <span className="text-xs text-surface-500">
              avg: {formatCurrency(row.avg_p50)}/mo
            </span>
          )}
        </button>
      )}
    </div>
  );
}

// ── Row Actions ──────────────────────────────
function ForecastRowActions({
  row,
  onAction,
  isActioning,
  onToggle,
}: {
  row: ForecastRow;
  onAction: (id: string, action: string) => void;
  isActioning: boolean;
  onToggle: () => void;
}) {
  const recommendation = row.ai_recommendation;
  const reasoning = row.ai_reasoning;
  const hasIssue = recommendation && recommendation !== 'approve';
  const isReviewed = row.review_status === 'approved' || row.review_status === 'rejected';

  if (isReviewed) {
    return <span className="text-xs text-surface-500">Done</span>;
  }

  if (hasIssue) {
    return (
      <div className="flex items-center gap-1 justify-center">
        <button
          onClick={(e) => {
            e.stopPropagation();
            onAction(row.id, 'approve');
          }}
          disabled={isActioning}
          className="p-1 rounded-md hover:bg-deloitte-green/20 text-deloitte-green transition-colors"
          title="Approve as-is"
        >
          <CheckCircle2 className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onAction(row.id, 'flag');
          }}
          disabled={isActioning}
          className="p-1 rounded-md hover:bg-amber-500/20 text-amber-400 transition-colors"
          title="Flag for later"
        >
          <Eye className="w-3.5 h-3.5" />
        </button>
        <button
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
          className="p-1 rounded-md hover:bg-cyan-500/20 text-cyan-400 transition-colors"
          title="View AI recommendations"
        >
          <Sparkles className="w-3.5 h-3.5" />
        </button>
      </div>
    );
  }

  return (
    <div className="flex items-center gap-1 justify-center">
      <button
        onClick={(e) => {
          e.stopPropagation();
          onAction(row.id, 'approve');
        }}
        disabled={isActioning}
        className="p-1 rounded-md hover:bg-deloitte-green/20 text-surface-500 hover:text-deloitte-green transition-colors"
        title="Approve"
      >
        <CheckCircle2 className="w-3.5 h-3.5" />
      </button>
      {reasoning && (
        <button
          onClick={(e) => {
            e.stopPropagation();
            onToggle();
          }}
          className="p-1 rounded-md hover:bg-surface-700 text-surface-600 hover:text-surface-300 transition-colors"
          title="View details"
        >
          <Info className="w-3.5 h-3.5" />
        </button>
      )}
    </div>
  );
}

// ── Action Icon Mapping ─────────────────────
function ActionIcon({ type }: { type: string }) {
  switch (type) {
    case 'override': return <Edit3 className="w-3 h-3" />;
    case 'driver_input': return <UserCheck className="w-3 h-3" />;
    case 'upload_data': return <Upload className="w-3 h-3" />;
    case 'switch_model': return <RefreshCw className="w-3 h-3" />;
    case 'investigate': return <Search className="w-3 h-3" />;
    case 'compare_peers': return <ArrowRightLeft className="w-3 h-3" />;
    case 'review_override': return <Eye className="w-3 h-3" />;
    case 'confirm_zero': return <CheckCircle2 className="w-3 h-3" />;
    case 'approve': return <CheckCircle2 className="w-3 h-3" />;
    default: return <Zap className="w-3 h-3" />;
  }
}

function actionButtonColor(type: string): string {
  switch (type) {
    case 'override': return 'bg-cyan-500/10 text-cyan-400 hover:bg-cyan-500/20 border-cyan-500/20';
    case 'driver_input': return 'bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 border-amber-500/20';
    case 'upload_data': return 'bg-violet-500/10 text-violet-400 hover:bg-violet-500/20 border-violet-500/20';
    case 'switch_model': return 'bg-blue-500/10 text-blue-400 hover:bg-blue-500/20 border-blue-500/20';
    case 'investigate': return 'bg-orange-500/10 text-orange-400 hover:bg-orange-500/20 border-orange-500/20';
    case 'compare_peers': return 'bg-teal-500/10 text-teal-400 hover:bg-teal-500/20 border-teal-500/20';
    case 'review_override': return 'bg-amber-500/10 text-amber-400 hover:bg-amber-500/20 border-amber-500/20';
    case 'confirm_zero': return 'bg-deloitte-green/10 text-deloitte-green hover:bg-deloitte-green/20 border-deloitte-green/20';
    case 'approve': return 'bg-deloitte-green/10 text-deloitte-green hover:bg-deloitte-green/20 border-deloitte-green/20';
    default: return 'bg-surface-700/50 text-surface-300 hover:bg-surface-700 border-surface-600';
  }
}

function chatCommandForAction(action: AIAction, row: ForecastRow): string | null {
  switch (action.type) {
    case 'override':
      return `Override the forecast for "${row.line_item_name}" — I want to adjust the value.`;
    case 'driver_input':
      return `Collect driver assumptions for "${row.line_item_name}" in the ${row.category} category.`;
    case 'switch_model':
      return `Re-run the forecast for "${row.line_item_name}" using auto model selection to find the best fit.`;
    case 'investigate':
      return `Analyze the forecast for "${row.line_item_name}" — why does it deviate significantly from actuals? Show me the trend comparison.`;
    case 'compare_peers':
      return `Compare "${row.line_item_name}" against other items in the ${row.category} category.`;
    case 'review_override':
      return `Show me the override history and rationale for "${row.line_item_name}".`;
    case 'confirm_zero':
      return `Approve "${row.line_item_name}" as zero — confirm no activity expected.`;
    default:
      return null;
  }
}

// ── Root Cause Badge ─────────────────────────
function RootCauseBadge({ cause }: { cause: string }) {
  const config: Record<string, { label: string; color: string }> = {
    no_data: { label: 'No Data', color: 'bg-red-500/15 text-red-400 border-red-500/20' },
    sparse_data: { label: 'Sparse Data', color: 'bg-amber-500/15 text-amber-400 border-amber-500/20' },
    low_confidence: { label: 'Low Confidence', color: 'bg-orange-500/15 text-orange-400 border-orange-500/20' },
    poor_model_fit: { label: 'Poor Model Fit', color: 'bg-red-500/15 text-red-400 border-red-500/20' },
    high_uncertainty: { label: 'High Uncertainty', color: 'bg-amber-500/15 text-amber-400 border-amber-500/20' },
    forecast_drift: { label: 'Forecast Drift', color: 'bg-orange-500/15 text-orange-400 border-orange-500/20' },
    override_impact: { label: 'Override Impact', color: 'bg-cyan-500/15 text-cyan-400 border-cyan-500/20' },
    category_outlier: { label: 'Category Outlier', color: 'bg-violet-500/15 text-violet-400 border-violet-500/20' },
  };
  const c = config[cause];
  if (!c) return null;
  return (
    <span className={`inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-xs font-semibold border ${c.color}`}>
      <CircleDot className="w-2 h-2" />
      {c.label}
    </span>
  );
}

// ── Materiality Badge ────────────────────────
function MaterialityBadge({ level }: { level: string }) {
  const colors: Record<string, string> = {
    high: 'text-red-400',
    medium: 'text-amber-400',
    low: 'text-surface-500',
  };
  return (
    <span className={`text-xs font-semibold uppercase ${colors[level] || colors.low}`}>
      {level} materiality
    </span>
  );
}

function ExogModeScoresCard({ exog }: { exog?: ExogInfo | null }) {
  const scores = exog?.exog_mode_scores;
  if (!exog || !scores) return null;
  const formatMase = (value: number | null | undefined) =>
    value == null || !Number.isFinite(value) ? 'n/a' : value.toFixed(3);
  const forecastScore = typeof scores.mase_forecast === 'number' ? scores.mase_forecast : null;
  const knownScore = typeof scores.mase_known === 'number' ? scores.mase_known : null;
  const delta = forecastScore != null && knownScore != null ? forecastScore - knownScore : null;

  return (
    <div className="pl-7">
      <div className="bg-surface-800/60 border border-deloitte-teal/20 rounded-lg p-2.5 space-y-1.5">
        <div className="flex items-center gap-2 flex-wrap">
          <span className="text-xs font-semibold text-deloitte-teal uppercase tracking-wider">
            Exogenous Validation
          </span>
          <span className="text-xs text-surface-500">
            folds: {scores.n_folds ?? 0}
          </span>
          <span className="text-xs text-surface-500">
            mode: {exog.mode || 'forecast'}
          </span>
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
    </div>
  );
}

// ── Expanded Remediation Component ──────────────
function ExpandedRemediation({
  row,
  reasoning,
  hasIssue,
  isReviewed,
  isActioning,
  onAction,
}: {
  row: ForecastRow;
  reasoning: string;
  hasIssue: boolean;
  isReviewed: boolean;
  isActioning: boolean;
  onAction: (id: string, action: string) => void;
}) {
  const { openPanel } = usePanelStore();
  const [copiedAction, setCopiedAction] = useState<string | null>(null);

  const handleActionClick = useCallback(async (action: AIAction) => {
    if (action.type === 'confirm_zero' || action.type === 'approve') {
      onAction(row.id, 'approve');
      return;
    }

    if (action.type === 'upload_data') {
      openPanel('document_library', {});
      return;
    }
    if (action.type === 'driver_input') {
      openPanel('driver_inputs', {});
      return;
    }
    if (action.type === 'override' || action.type === 'switch_model' || action.type === 'review_override') {
      // Stay on forecast table — copy chat command so the user can act via agent
      const chatCmd = chatCommandForAction(action, row);
      if (chatCmd) {
        await navigator.clipboard.writeText(chatCmd);
        setCopiedAction(action.type);
        setTimeout(() => setCopiedAction(null), 2000);
      }
      return;
    }
    if (action.type === 'investigate' || action.type === 'compare_peers') {
      openPanel(action.type === 'compare_peers' ? 'accuracy_tracking' : 'anomaly_dashboard', {});
      const chatCmd = chatCommandForAction(action, row);
      if (chatCmd) {
        await navigator.clipboard.writeText(chatCmd);
        setCopiedAction(action.type);
        setTimeout(() => setCopiedAction(null), 2000);
      }
      return;
    }

    const chatCmd = chatCommandForAction(action, row);
    if (chatCmd) {
      await navigator.clipboard.writeText(chatCmd);
      setCopiedAction(action.type);
      setTimeout(() => setCopiedAction(null), 2000);
    }
  }, [row, onAction, openPanel]);

  const aiActions = row.ai_actions || [];
  const recommendation = row.ai_recommendation;

  return (
    <div className="space-y-3">
          {/* Header with metadata */}
          <div className="flex items-center gap-3 flex-wrap">
            <Bot className="w-4 h-4 text-deloitte-green flex-shrink-0" />
            <span className="text-xs font-semibold text-deloitte-green uppercase tracking-wider">
              AI Analysis
            </span>
            {row.model_type && (
              <span className="text-xs text-surface-500 font-mono bg-surface-800 px-1.5 py-0.5 rounded">
                Model: {row.model_type}
              </span>
            )}
            {row.model_mape != null && (
              <span className="text-xs text-surface-500 font-mono bg-surface-800 px-1.5 py-0.5 rounded">
                MAPE: {row.model_mape.toFixed(1)}%
              </span>
            )}
            {row.root_cause && row.root_cause !== 'none' && (
              <RootCauseBadge cause={row.root_cause} />
            )}
            {row.materiality && (
              <MaterialityBadge level={row.materiality} />
            )}
          </div>

          {/* Business Driver Context */}
          {row.driver_context && row.driver_context.narrative && (
            <div className="pl-7">
              <div className="bg-surface-800/60 border border-surface-700/40 rounded-lg p-2.5 space-y-2">
                <div className="flex items-center gap-1.5">
                  <Activity className="w-3 h-3 text-deloitte-teal" />
                  <span className="text-xs font-semibold text-deloitte-teal uppercase tracking-wider">
                    Business Drivers
                  </span>
                </div>

                {/* Narrative */}
                <p className="text-xs text-surface-300 leading-relaxed">
                  {row.driver_context.narrative}
                </p>

                {/* Dependencies */}
                {row.driver_context.dependencies && row.driver_context.dependencies.length > 0 && (
                  <div className="flex items-center gap-2 flex-wrap">
                    <GitBranch className="w-3 h-3 text-surface-500 flex-shrink-0" />
                    <span className="text-xs text-surface-500">Driven by:</span>
                    {row.driver_context.dependencies.map((dep, i) => (
                      <span key={i} className="text-xs px-1.5 py-0.5 bg-surface-700/80 rounded text-surface-300 font-mono">
                        {dep.relationship === 'subtract' ? '−' : dep.relationship === 'multiply' ? '×' : '+'}{' '}
                        {dep.name}
                        {dep.forecast_total ? ` ($${Math.abs(dep.forecast_total) >= 1000 ? `${(dep.forecast_total/1000).toFixed(0)}K` : dep.forecast_total.toFixed(0)})` : ''}
                      </span>
                    ))}
                  </div>
                )}

                {/* Actuals trend mini-summary */}
                {row.driver_context.actuals_trend && (
                  <div className="flex items-center gap-3 text-xs flex-wrap">
                    {row.driver_context.actuals_trend.direction === 'upward' ? (
                      <TrendingUp className="w-3 h-3 text-deloitte-green flex-shrink-0" />
                    ) : row.driver_context.actuals_trend.direction === 'downward' ? (
                      <TrendingDown className="w-3 h-3 text-red-400 flex-shrink-0" />
                    ) : (
                      <Activity className="w-3 h-3 text-surface-500 flex-shrink-0" />
                    )}
                    <span className="text-surface-400">
                      Last actual: <span className="text-surface-200 font-mono">{formatCurrency(row.driver_context.actuals_trend.last_actual)}</span>
                      <span className="text-surface-600 mx-1">|</span>
                      Avg: <span className="text-surface-200 font-mono">{formatCurrency(row.driver_context.actuals_trend.recent_avg)}</span>/mo
                    </span>
                    {row.driver_context.actuals_trend.mom_growth_pct != null && (
                      <span className={`font-semibold ${row.driver_context.actuals_trend.mom_growth_pct >= 0 ? 'text-deloitte-green' : 'text-red-400'}`}>
                        MoM: {row.driver_context.actuals_trend.mom_growth_pct > 0 ? '+' : ''}{row.driver_context.actuals_trend.mom_growth_pct.toFixed(1)}%
                      </span>
                    )}
                    {row.driver_context.actuals_trend.yoy_growth_pct != null && (
                      <span className={`font-semibold ${row.driver_context.actuals_trend.yoy_growth_pct >= 0 ? 'text-deloitte-green' : 'text-red-400'}`}>
                        YoY: {row.driver_context.actuals_trend.yoy_growth_pct > 0 ? '+' : ''}{row.driver_context.actuals_trend.yoy_growth_pct.toFixed(1)}%
                      </span>
                    )}
                  </div>
                )}

                {/* Active overrides */}
                {row.driver_context.active_overrides && row.driver_context.active_overrides.length > 0 && (
                  <div className="flex items-start gap-2 text-xs">
                    <Edit3 className="w-3 h-3 text-cyan-400 flex-shrink-0 mt-0.5" />
                    <div>
                      <span className="text-cyan-400 font-semibold">Active overrides: </span>
                      {row.driver_context.active_overrides.slice(0, 2).map((ov, i) => (
                        <span key={i} className="text-surface-400">
                          {i > 0 && ' · '}
                          {ov.period}: {formatCurrency(ov.original_value)} → <span className="text-cyan-300">{formatCurrency(ov.override_value)}</span>
                          ({ov.delta_pct > 0 ? '+' : ''}{ov.delta_pct}%)
                          {ov.reason && <span className="italic text-surface-500"> — {ov.reason.slice(0, 40)}{ov.reason.length > 40 ? '…' : ''}</span>}
                        </span>
                      ))}
                    </div>
                  </div>
                )}
              </div>
            </div>
          )}

          {/* Reasoning — the actual AI finding */}
          <ExogModeScoresCard exog={row.exog} />

          {/* Reasoning — the actual AI finding */}
          <div className="pl-7">
            {reasoning.includes(' | ') ? (
              <ul className="space-y-1">
                {reasoning.split(' | ').map((part, i) => (
                  <li key={i} className="flex items-start gap-2 text-xs text-surface-300 leading-relaxed">
                    <span className="w-1 h-1 rounded-full bg-surface-500 flex-shrink-0 mt-1.5" />
                    {part}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="text-xs text-surface-300 leading-relaxed">{reasoning}</p>
            )}
          </div>

          {/* Actionable next steps */}
          {hasIssue && aiActions.length > 0 && (
            <div className="pl-7 space-y-2">
              <div className="flex items-center gap-1.5">
                <Zap className="w-3 h-3 text-cyan-400" />
                <span className="text-xs font-semibold text-cyan-400 uppercase tracking-wider">
                  Recommended Actions
                </span>
              </div>

              <div className="space-y-1.5">
                {aiActions.map((action, idx) => (
                  <div
                    key={idx}
                    className="flex items-start gap-2 px-2.5 py-2 bg-surface-800/80 rounded-lg border border-surface-700/30 group"
                  >
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 mb-0.5">
                        <span className="text-xs font-semibold text-surface-200">
                          {idx + 1}. {action.label}
                        </span>
                      </div>
                      <p className="text-xs text-surface-400 leading-relaxed">
                        {action.detail}
                      </p>
                    </div>
                    <button
                      onClick={(e) => {
                        e.stopPropagation();
                        handleActionClick(action);
                      }}
                      disabled={isActioning}
                      className={`flex-shrink-0 flex items-center gap-1 text-xs font-medium px-2.5 py-1.5 rounded-lg border transition-all ${
                        copiedAction === action.type
                          ? 'bg-deloitte-green/20 text-deloitte-green border-deloitte-green/30'
                          : actionButtonColor(action.type)
                      }`}
                      title={action.detail}
                    >
                      {copiedAction === action.type ? (
                        <>
                          <CheckCircle2 className="w-3 h-3" />
                          Copied
                        </>
                      ) : (
                        <>
                          <ActionIcon type={action.type} />
                          {action.type === 'approve' || action.type === 'confirm_zero'
                            ? action.label
                            : (
                              <>
                                <MessageSquare className="w-2.5 h-2.5 opacity-50" />
                                Copy to Chat
                              </>
                            )}
                        </>
                      )}
                    </button>
                  </div>
                ))}
              </div>
            </div>
          )}

          {/* Fallback for items without structured actions */}
          {hasIssue && (!aiActions || aiActions.length === 0) && (
            <div className="pl-7">
              <div className="flex items-start gap-2 px-2.5 py-1.5 bg-surface-800/80 rounded-lg border border-surface-700/30">
                <Info className="w-3.5 h-3.5 text-cyan-400 flex-shrink-0 mt-0.5" />
                <p className="text-xs text-surface-400 leading-relaxed">
                  <span className="text-cyan-400 font-semibold">Suggested: </span>
                  {recommendation === 'override' || recommendation === 'manual_input'
                    ? `Override this forecast for "${row.line_item_name}" with a business-informed estimate, or collect driver assumptions from the BU owner.`
                    : `Review this item and apply a manual adjustment if the model projection doesn't align with known business changes.`
                  }
                </p>
              </div>
            </div>
          )}

          {/* Quick review buttons */}
          {!isReviewed && (
            <div className="pl-7 flex items-center gap-2 pt-1 border-t border-surface-700/20">
              <span className="text-xs text-surface-500 mr-1">Review decision:</span>
              <button
                onClick={(e) => { e.stopPropagation(); onAction(row.id, 'approve'); }}
                disabled={isActioning}
                className="text-xs font-medium px-2.5 py-1 bg-deloitte-green/10 text-deloitte-green rounded-lg hover:bg-deloitte-green/20 transition-colors flex items-center gap-1 border border-deloitte-green/15"
              >
                <CheckCircle2 className="w-3 h-3" />
                Approve As-Is
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); onAction(row.id, 'flag'); }}
                disabled={isActioning}
                className="text-xs font-medium px-2.5 py-1 bg-amber-500/10 text-amber-400 rounded-lg hover:bg-amber-500/20 transition-colors flex items-center gap-1 border border-amber-500/15"
              >
                <Eye className="w-3 h-3" />
                Flag for Later
              </button>
              <button
                onClick={(e) => { e.stopPropagation(); onAction(row.id, 'reject'); }}
                disabled={isActioning}
                className="text-xs font-medium px-2.5 py-1 bg-red-500/10 text-red-400 rounded-lg hover:bg-red-500/20 transition-colors flex items-center gap-1 border border-red-500/15"
              >
                <XCircle className="w-3 h-3" />
                Reject
              </button>
        </div>
      )}
    </div>
  );
}

// ── Confidence Indicator ─────────────────────
function ConfidenceIndicator({ score, level }: { score: number; level: string }) {
  const barColor =
    level === 'high' ? 'bg-deloitte-green' :
    level === 'medium' ? 'bg-amber-400' :
    'bg-red-500';

  const textColor =
    level === 'high' ? 'text-deloitte-green' :
    level === 'medium' ? 'text-amber-400' :
    'text-red-400';

  return (
    <div className="flex flex-col items-center gap-0.5">
      <span className={`text-xs font-bold ${textColor}`}>
        {Math.round(score)}
      </span>
      <div className="w-12 h-1.5 rounded-full bg-surface-700 overflow-hidden">
        <div
          className={`h-full rounded-full transition-all ${barColor}`}
          style={{ width: `${Math.min(100, score)}%` }}
        />
      </div>
    </div>
  );
}

// ── Status Badge ─────────────────────────────
function StatusBadge({
  recommendation,
  reviewStatus,
}: {
  recommendation?: string;
  reviewStatus?: string;
}) {
  if (reviewStatus === 'approved') {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-xs font-semibold bg-deloitte-green/15 text-deloitte-green border border-deloitte-green/20">
        <ShieldCheck className="w-2.5 h-2.5" />
        Approved
      </span>
    );
  }
  if (reviewStatus === 'rejected') {
    return (
      <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-xs font-semibold bg-red-500/15 text-red-400 border border-red-500/20">
        <XCircle className="w-2.5 h-2.5" />
        Rejected
      </span>
    );
  }

  switch (recommendation) {
    case 'override':
    case 'manual_input':
      return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-xs font-semibold bg-red-500/15 text-red-400 border border-red-500/20">
          <AlertTriangle className="w-2.5 h-2.5" />
          Action
        </span>
      );
    case 'review':
      return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-xs font-semibold bg-amber-500/15 text-amber-400 border border-amber-500/20">
          <Eye className="w-2.5 h-2.5" />
          Review
        </span>
      );
    case 'flag':
      return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-xs font-semibold bg-orange-500/15 text-orange-400 border border-orange-500/20">
          <AlertTriangle className="w-2.5 h-2.5" />
          Flagged
        </span>
      );
    case 'approve':
      return (
        <span className="inline-flex items-center gap-1 px-1.5 py-0.5 rounded-md text-xs font-semibold bg-deloitte-green/10 text-deloitte-green/70 border border-deloitte-green/15">
          <CheckCircle2 className="w-2.5 h-2.5" />
          OK
        </span>
      );
    default:
      return (
        <span className="text-xs text-surface-500">—</span>
      );
  }
}

// ── Stat Card ────────────────────────────────
function StatCard({
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

// ── Format Currency ──────────────────────────
function formatCurrency(value: number | null | undefined): string {
  if (value === null || value === undefined) return '-';
  if (Math.abs(value) >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
  if (Math.abs(value) >= 1_000) return `$${(value / 1_000).toFixed(0)}K`;
  return `$${value.toFixed(0)}`;
}
