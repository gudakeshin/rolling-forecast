import { Filter } from 'lucide-react';
import { useCallback, useEffect, useMemo, useState } from 'react';
import { apiDownload, apiPost } from '../../api/client';
import { usePanelStore } from '../../store/panelStore';
import { toast } from '../../store/toastStore';
import { DataTable, type DataTableColumn } from '../ui/DataTable';
import { EmptyForecastState } from './EmptyForecastState';
import { ExpandedRemediation } from './ForecastRemediation';
import { CalibrationBanner, QualitySummaryBanner, StatCard } from './ForecastTableBanners';
import {
  ConfidenceIndicator, ForecastLineItemCell, ForecastRowActions, ForecastValueCell, StatusBadge,
} from './ForecastTableCells';
import {
  type CalibrationSummary, type ForecastRow, type ForecastTableRow, type QualitySummary,
  type VersionInfo,
} from './ForecastTableTypes';
import { formatCurrency } from './shared/format';

interface Props {
  data: {
    panel_type: string;
    title: string;
    data: {
      version?: VersionInfo;
      quality_summary?: QualitySummary;
      calibration_summary?: CalibrationSummary;
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

// ── Main Component ───────────────────────────
/** The table's own CSV/XLSX buttons export only the loaded page; this asks the
 * server for every line x period of the version under the current filters. */
function FullVersionExport({
  versionId,
  versionName,
  category,
  confidence,
}: {
  versionId: string;
  versionName: string;
  category: string;
  confidence: string;
}) {
  const [busy, setBusy] = useState<'csv' | 'xlsx' | null>(null);

  const download = async (format: 'csv' | 'xlsx') => {
    const qs = new URLSearchParams({ format });
    if (category !== 'all') qs.set('category', category);
    if (['high', 'medium', 'low'].includes(confidence)) qs.set('confidence_level', confidence);
    setBusy(format);
    try {
      const safe = versionName.replace(/[^A-Za-z0-9_-]+/g, '_');
      await apiDownload(`/panel/forecast-table/${versionId}/export?${qs.toString()}`, `${safe}.${format}`);
    } catch (e: any) {
      toast.error(e?.message || 'Export failed');
    } finally {
      setBusy(null);
    }
  };

  return (
    <div className="flex items-center gap-2 text-xs text-surface-400">
      <span>Export full version:</span>
      {(['csv', 'xlsx'] as const).map((f) => (
        <button
          key={f}
          type="button"
          onClick={() => download(f)}
          disabled={busy !== null}
          className="px-2 py-0.5 rounded border border-surface-700 hover:text-surface-200 disabled:opacity-50"
        >
          {busy === f ? 'Preparing…' : f.toUpperCase()}
        </button>
      ))}
    </div>
  );
}

export function ForecastTablePanel({ data, onRefresh, enablePolling }: Props) {
  const rows = data.data.rows || data.data.items || [];
  const version = data.data.version;
  const quality = data.data.quality_summary;
  const calibration = data.data.calibration_summary;
  const isSummaryView = data.data.view === 'summary' || (!data.data.view && rows.length > 0 && rows[0]?.period_count);
  const categories = data.data.available_categories || [];
  const hasMore = Boolean(data.data.has_more);
  const pageLimit = data.data.limit || 100;

  // Hoisted above the useState calls below so their initializers can read
  // the panel's own params via the (context-resolved) hook rather than a
  // static usePanelStore.getState() call, which would always read the
  // primary slot even when this component is rendered pinned in the
  // secondary slot (see store/panelStore.ts).
  const { openPanel, panelParams, setPanelParams } = usePanelStore();

  const [expandedRows, setExpandedRows] = useState<Set<string>>(new Set());
  const [filterCategory, setFilterCategory] = useState<string>(
    typeof panelParams?.category === 'string' ? panelParams.category : 'all',
  );
  const [filterConfidence, setFilterConfidence] = useState<string>(
    typeof panelParams?.confidence_level === 'string' ? panelParams.confidence_level : 'all',
  );
  const [filterExog, setFilterExog] = useState<string>(
    typeof panelParams?.exog_mode === 'string' ? panelParams.exog_mode : 'all',
  );
  const [sortBy, setSortBy] = useState<string>(
    typeof panelParams?.sort_by === 'string' ? panelParams.sort_by : 'default',
  );
  const [actioningId, setActioningId] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);

  const pollEnabled = (enablePolling ?? true) && Boolean(onRefresh);

  useEffect(() => {
    if (!pollEnabled || !onRefresh) return;
    const id = window.setInterval(() => {
      if (!document.hidden) void onRefresh();
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

  // Client-only view settings (no server round-trip — PanelContainer's fetch
  // effect only depends on the server-relevant subset of panelParams) that
  // still ride the URL/saved-view serialization so they survive close/reopen
  // and a shared link.
  const updateViewParam = useCallback(
    (key: 'exog_mode' | 'sort_by', value: string) => {
      setPanelParams({ ...panelParams, [key]: value });
    },
    [panelParams, setPanelParams],
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
        // Pinned: the horizon scrolls sideways and a number is meaningless once
        // its line name has scrolled off.
        pinned: true,
        width: 260,
        filterable: true,
        exportValue: (_, row) => String(row.line_item_name ?? ''),
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
        total: true,
        exportValue: (v) => (typeof v === 'number' ? v : null),
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
        filterable: true,
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
    return <EmptyForecastState />;
  }

  return (
    <div className="space-y-3">
      {/* ── Quality Summary Banner ── */}
      {quality && (
        <QualitySummaryBanner quality={quality} onOpenReview={handleOpenReview} />
      )}

      {/* ── Interval calibration provenance ── */}
      {calibration && calibration.calibrated_lines > 0 && (
        <CalibrationBanner calibration={calibration} />
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
          onChange={(e) => {
            setFilterExog(e.target.value);
            updateViewParam('exog_mode', e.target.value);
          }}
          className="text-xs bg-surface-800 border border-surface-700 text-surface-300 rounded-lg px-2 py-1.5 focus:border-deloitte-green/50 focus:outline-none"
        >
          <option value="all">All Exog Modes</option>
          <option value="with_exog">Exog Enabled</option>
          <option value="with_scores">Exog Scores Available</option>
        </select>

        <select
          value={sortBy}
          onChange={(e) => {
            setSortBy(e.target.value);
            updateViewParam('sort_by', e.target.value);
          }}
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
        searchable
        searchPlaceholder="Find a line item"
        showTotals
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
            onExplain={() =>
              openPanel('why_this_number', {
                result_id: row.id,
                line_item_id: row.line_item_id,
                period: row.period || row.periods,
              })
            }
          />
        )}
        exportFilename={`forecast_${version?.name || 'export'}`}
        maxHeight={typeof window !== 'undefined' ? Math.max(400, window.innerHeight - 320) : 560}
        rowHeight={56}
      />

      <div className="flex flex-col items-center gap-2">
        {version?.id && (
          <FullVersionExport
            versionId={version.id}
            versionName={version.name || 'forecast'}
            category={filterCategory}
            confidence={filterConfidence}
          />
        )}
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

export { CalibrationBanner } from './ForecastTableBanners';
export type { CalibrationSummary } from './ForecastTableTypes';
