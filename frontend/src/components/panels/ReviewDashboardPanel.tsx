import {
  AlertTriangle, Bot, CheckCircle, Download, Filter, Loader2, RefreshCw, Shield, Sparkles,
  XCircle, Zap,
} from 'lucide-react';
import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Bar, BarChart, CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts';
import { apiPost } from '../../api/client';
import { batchReview, undoReview } from '../../api/dashboard';
import { useCan } from '../../store/authStore';
import { confirmDialog } from '../../store/confirmStore';
import { usePanelStore } from '../../store/panelStore';
import { toast } from '../../store/toastStore';
import { chartTheme } from '../../theme/chartTheme';
import { downloadCsv } from '../ui/DataTable';
import { COLORS, ChartTooltip, exogGapForItem } from './ReviewBadges';
import { BusinessAnalysisSummary } from './ReviewBusinessSummary';
import { type ReviewBuckets, type ReviewItem } from './ReviewDashboardTypes';
import { ReviewItemRow, previewLines } from './ReviewItemRow';

interface Props {
  data: {
    panel_type: string;
    title: string;
    data: {
      version: { id: string; name: string; status: string };
      buckets: ReviewBuckets;
      confidence_trend: any[];
      category_flag_chart: any[];
      summary: Record<string, number | boolean>;
    };
  };
  onRefresh?: () => void;
  focusLineItemId?: number | string;
}

// ─── Main Component ────────────────────────────────

type BucketKey = 'flagged' | 'needs_review' | 'ai_approved' | 'already_reviewed';

export function ReviewDashboardPanel({ data, onRefresh, focusLineItemId }: Props) {
  const { version, buckets, confidence_trend, category_flag_chart, summary } = data.data;
  const openPanel = usePanelStore((s) => s.openPanel);
  const canGenerate = useCan('can_generate');
  const [activeBucket, setActiveBucket] = useState<BucketKey>('flagged');
  const [actioningItem, setActioningItem] = useState<string | null>(null);
  const [isAcceptingAll, setIsAcceptingAll] = useState(false);
  const [isRescoring, setIsRescoring] = useState(false);
  const [localBuckets, setLocalBuckets] = useState(buckets);
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [batchComment, setBatchComment] = useState('');
  const [batchRejecting, setBatchRejecting] = useState(false);
  const [batchBusy, setBatchBusy] = useState(false);
  const [filterExog, setFilterExog] = useState<string>('all');
  const [sortBy, setSortBy] = useState<string>('default');
  const focusRowRef = useRef<HTMLDivElement | null>(null);
  const focusApplied = useRef(false);

  // Soft poll while review queue is open
  useEffect(() => {
    if (!onRefresh) return;
    const id = window.setInterval(() => {
      if (!document.hidden) onRefresh();
    }, 60_000);
    return () => window.clearInterval(id);
  }, [onRefresh]);

  // Deep-link: switch bucket + scroll to focused line item
  useEffect(() => {
    if (focusLineItemId == null || focusApplied.current) return;
    const target = Number(focusLineItemId);
    const keys: BucketKey[] = ['flagged', 'needs_review', 'ai_approved', 'already_reviewed'];
    for (const key of keys) {
      const found = localBuckets[key].items.find(
        (i: ReviewItem) => i.line_item_id === target || String(i.line_item_id) === String(focusLineItemId),
      );
      if (found) {
        setActiveBucket(key);
        focusApplied.current = true;
        requestAnimationFrame(() => {
          focusRowRef.current?.scrollIntoView({ behavior: 'smooth', block: 'center' });
        });
        break;
      }
    }
  }, [focusLineItemId, localBuckets]);

  const handleRescore = useCallback(async () => {
    setIsRescoring(true);
    try {
      await apiPost(`/panel/rescore-forecasts/${version.id}`, {});
      toast.success('Rescore complete');
      onRefresh?.();
    } catch (error: any) {
      toast.error(error?.message || 'Rescore failed');
    } finally {
      setIsRescoring(false);
    }
  }, [version.id, onRefresh]);

  const moveItemsToReviewed = useCallback((itemIds: string[], action: string, comment?: string) => {
    setLocalBuckets((prev) => {
      const updated = { ...prev };
      const moved: ReviewItem[] = [];
      for (const key of ['flagged', 'needs_review', 'ai_approved'] as BucketKey[]) {
        const bucket = { ...updated[key], items: [...updated[key].items] };
        for (const id of itemIds) {
          const idx = bucket.items.findIndex((i: ReviewItem) => i.id === id);
          if (idx >= 0) {
            const [item] = bucket.items.splice(idx, 1);
            item.review_status = action === 'approve' ? 'approved' : 'rejected';
            item.review_comment = comment || null;
            moved.push(item);
          }
        }
        bucket.total = bucket.items.length;
        updated[key] = bucket;
      }
      if (moved.length) {
        updated.already_reviewed = {
          ...updated.already_reviewed,
          items: [...moved, ...updated.already_reviewed.items],
          total: updated.already_reviewed.total + moved.length,
        };
      }
      return updated;
    });
  }, []);

  const handleUndo = useCallback(async (undoToken: string, label: string) => {
    try {
      await undoReview(undoToken);
      toast.success(`Undone: ${label}`);
      onRefresh?.();
    } catch (error: any) {
      toast.error(error?.message || 'Undo failed — the action may be too old to reverse');
    }
  }, [onRefresh]);

  const handleItemAction = useCallback(async (itemId: string, action: string, comment?: string) => {
    setActioningItem(itemId);
    try {
      const res = await apiPost<{ undo_token?: string }>('/panel/review-item', { item_id: itemId, action, comment });
      moveItemsToReviewed([itemId], action, comment);
      const label = action === 'approve' ? 'Item approved' : 'Item rejected';
      if (res?.undo_token) {
        toast.undo(label, () => handleUndo(res.undo_token!, label));
      } else {
        toast.success(label);
      }
    } catch (error: any) {
      toast.error(error?.message || 'Review action failed');
    } finally {
      setActioningItem(null);
    }
  }, [moveItemsToReviewed, handleUndo]);

  const handleAcceptAll = useCallback(async () => {
    const pending = localBuckets.ai_approved;
    const ok = await confirmDialog(
      `${previewLines(pending.items.map((i: ReviewItem) => i.line_item_name))}. ` +
        'You can undo this for a few seconds afterwards.',
      {
        title: `Accept ${pending.total} AI-approved line${pending.total === 1 ? '' : 's'}?`,
        confirmLabel: `Approve ${pending.total}`,
      },
    );
    if (!ok) return;
    setIsAcceptingAll(true);
    try {
      const res = await apiPost<{ undo_token?: string; approved_count?: number }>(
        '/panel/accept-ai-recommendations',
        { version_id: version.id },
      );

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
      const label = 'Accepted all AI recommendations';
      if (res?.undo_token) {
        toast.undo(label, () => handleUndo(res.undo_token!, label));
      } else {
        toast.success(label);
      }
    } catch (error: any) {
      toast.error(error?.message || 'Accept all failed');
    } finally {
      setIsAcceptingAll(false);
    }
  }, [version.id, handleUndo, localBuckets.ai_approved]);

  const selectableBucket = activeBucket === 'flagged' || activeBucket === 'needs_review';
  const activeItems = localBuckets[activeBucket].items;

  const displayItems = useMemo(() => {
    let items = activeItems as ReviewItem[];
    if (filterExog === 'with_exog') {
      items = items.filter((i) => Boolean(i.exog_used));
    } else if (filterExog === 'with_scores') {
      items = items.filter((i) => exogGapForItem(i) != null);
    }
    if (sortBy === 'exog_gap_desc') {
      return [...items].sort(
        (a, b) => Math.abs(exogGapForItem(b) ?? -1) - Math.abs(exogGapForItem(a) ?? -1),
      );
    }
    if (sortBy === 'risk_desc') {
      return [...items].sort((a, b) => b.ai_risk_score - a.ai_risk_score);
    }
    return items;
  }, [activeItems, filterExog, sortBy]);

  const exogFilterActive = filterExog !== 'all' || sortBy !== 'default';

  const toggleSelect = (id: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });
  };

  const toggleSelectAll = () => {
    if (selectedIds.size === displayItems.length) {
      setSelectedIds(new Set());
    } else {
      setSelectedIds(new Set(displayItems.map((i: ReviewItem) => i.id)));
    }
  };

  const handleBatch = async (action: 'approve' | 'reject') => {
    if (action === 'reject' && !batchComment.trim()) {
      toast.error('Comment required when rejecting');
      return;
    }
    const ids = Array.from(selectedIds);
    if (!ids.length) return;
    const verb = action === 'approve' ? 'Approve' : 'Reject';
    const names = displayItems
      .filter((i: ReviewItem) => selectedIds.has(i.id))
      .map((i: ReviewItem) => i.line_item_name);
    const ok = await confirmDialog(
      `${previewLines(names)}. You can undo this for a few seconds afterwards.`,
      {
        title: `${verb} ${ids.length} line${ids.length === 1 ? '' : 's'}?`,
        confirmLabel: `${verb} ${ids.length}`,
        danger: action === 'reject',
      },
    );
    if (!ok) return;
    setBatchBusy(true);
    try {
      const res = await batchReview(version.id, ids, action, batchComment.trim() || undefined);
      moveItemsToReviewed(ids, action, batchComment.trim() || undefined);
      setSelectedIds(new Set());
      setBatchRejecting(false);
      setBatchComment('');
      const label = `${action === 'approve' ? 'Approved' : 'Rejected'} ${ids.length} item(s)`;
      if (res?.undo_token) {
        toast.undo(label, () => handleUndo(res.undo_token!, label));
      } else {
        toast.success(label);
      }
    } catch (e: any) {
      toast.error(e?.message || 'Batch review failed');
    } finally {
      setBatchBusy(false);
    }
  };

  const allReviewed =
    localBuckets.flagged.total === 0 &&
    localBuckets.needs_review.total === 0 &&
    localBuckets.ai_approved.total === 0;

  const statusBadge =
    version.status === 'draft'
      ? 'bg-blue-500/10 border-blue-500/20 text-blue-400'
      : version.status === 'in_review'
      ? 'bg-amber-500/10 border-amber-500/20 text-amber-400'
      : 'bg-deloitte-green/10 border-deloitte-green/20 text-deloitte-green';

  const bucketConfig: { key: BucketKey; label: string; icon: any; color: string; bg: string }[] = [
    { key: 'flagged', label: 'Flagged', icon: XCircle, color: 'text-red-400', bg: 'bg-red-500/10 border-red-500/20' },
    { key: 'needs_review', label: 'Needs Review', icon: AlertTriangle, color: 'text-amber-400', bg: 'bg-amber-500/10 border-amber-500/20' },
    { key: 'ai_approved', label: 'AI Approved', icon: Sparkles, color: 'text-deloitte-green', bg: 'bg-deloitte-green/10 border-deloitte-green/20' },
    { key: 'already_reviewed', label: 'Reviewed', icon: Shield, color: 'text-blue-400', bg: 'bg-blue-500/10 border-blue-500/20' },
  ];

  const activeBucketData = localBuckets[activeBucket];
  const focusIdNum = focusLineItemId != null ? Number(focusLineItemId) : null;

  const reviewExportColumns = [
    { key: 'line_item_name', label: 'Line Item' },
    { key: 'category', label: 'Category' },
    { key: 'business_unit', label: 'Business Unit' },
    { key: 'avg_confidence', label: 'Confidence' },
    { key: 'ai_recommendation', label: 'Recommendation' },
    { key: 'review_status', label: 'Review Status' },
    { key: 'ai_risk_score', label: 'Risk Score' },
    { key: 'model_type', label: 'Model' },
    { key: 'materiality', label: 'Materiality' },
    { key: 'root_cause', label: 'Root Cause' },
  ];

  const handleExportBucket = () => {
    downloadCsv(
      `review_${activeBucket}_${version?.name || 'export'}`,
      reviewExportColumns,
      displayItems.map((item: ReviewItem) => ({
        line_item_name: item.line_item_name,
        category: item.category,
        business_unit: item.business_unit || '',
        avg_confidence: item.avg_confidence,
        ai_recommendation: item.ai_recommendation,
        review_status: item.review_status || '',
        ai_risk_score: item.ai_risk_score,
        model_type: item.model_type || '',
        materiality: item.materiality || '',
        root_cause: item.root_cause || '',
      })) as Record<string, unknown>[],
    );
  };

  return (
    <div className="space-y-3">
      {/* AI Analysis Banner */}
      <div className="flex items-center gap-2 px-3 py-2 bg-deloitte-green/8 border border-deloitte-green/20 rounded-xl">
        <Bot className="w-4 h-4 text-deloitte-green flex-shrink-0" />
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-2">
            <span className="text-xs font-semibold text-deloitte-green">AI Review Complete</span>
            <button
              type="button"
              onClick={() => openPanel('approvals', { version_id: version.id })}
              className={`px-1.5 py-0.5 text-xs font-medium rounded border ${statusBadge}`}
              title="Open approvals"
            >
              {version.status}
            </button>
          </div>
          <p className="text-xs text-surface-400">
            Analyzed {summary.total_line_items} line items —{' '}
            <span className="text-deloitte-green font-medium">{localBuckets.ai_approved.total} auto-approvable</span>,{' '}
            <span className="text-amber-400 font-medium">{localBuckets.needs_review.total} need review</span>,{' '}
            <span className="text-red-400 font-medium">{localBuckets.flagged.total} flagged</span>
          </p>
        </div>
        <div className="flex items-center gap-2 flex-shrink-0">
          <button
            type="button"
            onClick={() => openPanel('approvals', { version_id: version.id })}
            className="flex items-center gap-1 px-2 py-1.5 bg-surface-700/60 border border-surface-600/50 text-surface-300 text-xs font-medium rounded-lg hover:bg-surface-700 transition-colors whitespace-nowrap"
          >
            <Shield className="w-3 h-3" />
            Approvals
          </button>
          {version.status === 'draft' && allReviewed && canGenerate && (
            <button
              type="button"
              onClick={() => openPanel('approvals', { version_id: version.id })}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-deloitte-green text-white text-xs font-semibold rounded-lg hover:bg-deloitte-green/90 transition-colors whitespace-nowrap"
            >
              Submit for approval
            </button>
          )}
          <button
            onClick={handleRescore}
            disabled={isRescoring}
            className="flex items-center gap-1 px-2 py-1.5 bg-surface-700/60 border border-surface-600/50 text-surface-300 text-xs font-medium rounded-lg hover:bg-surface-700 disabled:opacity-50 transition-colors whitespace-nowrap"
            title="Re-run AI analysis with latest data"
          >
            {isRescoring ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
            Rescore
          </button>
          {activeItems.length > 0 && (
            <button
              type="button"
              onClick={handleExportBucket}
              className="inline-flex items-center gap-1.5 text-xs text-surface-300 hover:text-white px-2 py-1 rounded-md border border-surface-600 hover:border-deloitte-green/40 whitespace-nowrap"
              title="Export active bucket CSV"
            >
              <Download className="w-3.5 h-3.5" />
              CSV
            </button>
          )}
          {localBuckets.ai_approved.total > 0 && (
            <button
              onClick={handleAcceptAll}
              disabled={isAcceptingAll}
              className="flex items-center gap-1.5 px-3 py-1.5 bg-deloitte-green text-white text-xs font-semibold rounded-lg hover:bg-deloitte-green/90 disabled:opacity-50 transition-colors whitespace-nowrap"
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
            onClick={() => {
              setActiveBucket(key);
              setSelectedIds(new Set());
              setBatchRejecting(false);
              setFilterExog('all');
              setSortBy('default');
            }}
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
              {bucketConfig.find(b => b.key === activeBucket)?.label}{' '}
              ({exogFilterActive ? `${displayItems.length} of ${activeBucketData.total}` : activeBucketData.total})
            </span>
            {selectableBucket && displayItems.length > 0 && (
              <button
                type="button"
                onClick={toggleSelectAll}
                className="text-xs text-surface-400 hover:text-white ml-2"
              >
                {selectedIds.size === displayItems.length ? 'Deselect all' : 'Select all'}
              </button>
            )}
          </div>
          {activeBucket !== 'already_reviewed' && activeBucketData.items.length > 0 && (
            <span className="text-xs text-surface-500 italic">
              {activeBucketData.description}
            </span>
          )}
        </div>

        {activeBucketData.items.length > 0 && (
          <div className="px-3 py-2 border-b border-surface-700/40 flex items-center gap-2 flex-wrap">
            <Filter className="w-3.5 h-3.5 text-surface-500" />
            <select
              value={filterExog}
              onChange={(e) => {
                setFilterExog(e.target.value);
                setSelectedIds(new Set());
              }}
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
              <option value="risk_desc">Risk: High → Low</option>
              <option value="exog_gap_desc">Exog Gap: Largest First</option>
            </select>
          </div>
        )}

        {selectableBucket && selectedIds.size > 0 && (
          <div className="px-3 py-2 bg-surface-900/60 border-b border-surface-700/40 flex items-center gap-2 flex-wrap">
            <span className="text-xs text-surface-300">{selectedIds.size} selected</span>
            {batchRejecting ? (
              <>
                <input
                  type="text"
                  value={batchComment}
                  onChange={(e) => setBatchComment(e.target.value)}
                  placeholder="Rejection reason..."
                  className="flex-1 min-w-[140px] px-2 py-1 bg-surface-800 border border-surface-600 rounded text-xs text-white"
                />
                <button
                  type="button"
                  disabled={batchBusy || !batchComment.trim()}
                  onClick={() => handleBatch('reject')}
                  className="px-2 py-1 bg-red-500/20 text-red-400 rounded text-xs disabled:opacity-40"
                >
                  Reject {selectedIds.size}
                </button>
                <button type="button" onClick={() => setBatchRejecting(false)} className="text-xs text-surface-500">
                  Cancel
                </button>
              </>
            ) : (
              <>
                <button
                  type="button"
                  disabled={batchBusy}
                  onClick={() => handleBatch('approve')}
                  className="px-2 py-1 bg-deloitte-green/20 text-deloitte-green rounded text-xs font-medium disabled:opacity-50"
                >
                  Approve {selectedIds.size}
                </button>
                <button
                  type="button"
                  disabled={batchBusy}
                  onClick={() => setBatchRejecting(true)}
                  className="px-2 py-1 bg-red-500/15 text-red-400 rounded text-xs disabled:opacity-50"
                >
                  Reject {selectedIds.size}
                </button>
              </>
            )}
          </div>
        )}

        {/* Column headers */}
        {displayItems.length > 0 && (
          <div className="flex items-center gap-2 px-3 py-1.5 bg-surface-800/80 border-b border-surface-700/30 text-xs text-surface-500 uppercase tracking-wider font-semibold">
            {selectableBucket && <span className="w-4" />}
            <span className="w-4" />
            <span className="w-16">AI</span>
            <span className="flex-1">Line Item</span>
            <span className="w-20 text-right">Forecast</span>
            <span className="w-20 text-center">Risk</span>
            <span className="w-10 text-center">Conf</span>
            <span className="w-16 text-center">Action</span>
          </div>
        )}

        {displayItems.length > 0 && (
          <p className="px-3 pt-1.5 text-[10px] text-surface-500">
            Keyboard: <kbd>J</kbd>/<kbd>K</kbd> move · <kbd>A</kbd> approve · <kbd>R</kbd> reject
            {selectableBucket ? <> · <kbd>X</kbd> select</> : null}
          </p>
        )}
        <div className="max-h-[320px] overflow-y-auto" data-review-list>
          {displayItems.length > 0 ? (
            displayItems.map((item: ReviewItem) => {
              const isFocus =
                focusIdNum != null &&
                (item.line_item_id === focusIdNum || String(item.line_item_id) === String(focusLineItemId));
              return (
                <ReviewItemRow
                  key={item.id}
                  item={item}
                  onAction={handleItemAction}
                  isActioning={actioningItem}
                  selectable={selectableBucket}
                  selected={selectedIds.has(item.id)}
                  onToggleSelect={toggleSelect}
                  autoExpand={isFocus}
                  rowRef={isFocus ? (el) => { focusRowRef.current = el; } : undefined}
                />
              );
            })
          ) : (
            <div className="py-8 text-center">
              <CheckCircle className="w-6 h-6 text-deloitte-green/40 mx-auto mb-2" />
              <p className="text-xs text-surface-500">
                {exogFilterActive && activeBucketData.items.length > 0
                  ? 'No items match the exog filter'
                  : 'No items in this bucket'}
              </p>
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
                <CartesianGrid strokeDasharray="3 3" stroke={chartTheme.grid} />
                <XAxis dataKey="version" tick={{ fill: chartTheme.axis.fill, fontSize: 12 }} />
                <YAxis tick={{ fill: chartTheme.axis.fill, fontSize: 12 }} domain={[0, 100]} />
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
                <XAxis type="number" tick={{ fill: chartTheme.axis.fill, fontSize: 12 }} />
                <YAxis type="category" dataKey="category" tick={{ fill: chartTheme.axis.fill, fontSize: 12 }} width={55} />
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

export { previewLines,reviewShortcutFor } from './ReviewItemRow';
export type { ReviewShortcut } from './ReviewItemRow';
