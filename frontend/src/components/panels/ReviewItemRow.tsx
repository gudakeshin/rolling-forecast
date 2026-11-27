/** One review row with its expanded detail, plus the keyboard shortcuts for the review list. */
import {
  Activity, ArrowRight, Bot, ChevronDown, ChevronUp, DollarSign, Edit3, GitBranch, Loader2,
  MessageSquare, Save, ThumbsDown, ThumbsUp, TrendingDown, TrendingUp, X,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { Area, AreaChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts';
import { apiPost } from '../../api/client';
import { usePanelStore } from '../../store/panelStore';
import { toast } from '../../store/toastStore';
import { chartTheme } from '../../theme/chartTheme';
import { dispatchRecommendedAction } from '../../utils/recommendedActions';
import { AIBadge, COLORS, ConfBadge, MaterialityDot, RiskBar } from './ReviewBadges';
import { type ReviewItem } from './ReviewDashboardTypes';
import { ExogModeScoresCard } from './shared/ExogModeScoresCard';
import { formatCurrency } from './shared/format';

// ─── Review Item Row ───────────────────────────────

/** "A, B, C and 4 more" — what a batch action is about to touch, for its preview. */
export function previewLines(names: string[], max = 6): string {
  if (names.length <= max) return names.join(', ');
  return `${names.slice(0, max).join(', ')} and ${names.length - max} more`;
}

export type ReviewShortcut = 'approve' | 'reject' | 'select' | 'next' | 'prev';

/** Row-level review shortcuts. None fire while typing in a field or with a
 * modifier held, so ⌘K and browser shortcuts keep working. */
export function reviewShortcutFor(e: {
  key: string;
  target: EventTarget | null;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
}): ReviewShortcut | null {
  if (e.metaKey || e.ctrlKey || e.altKey) return null;
  const el = e.target as HTMLElement | null;
  const tag = el?.tagName;
  if (tag === 'INPUT' || tag === 'TEXTAREA' || tag === 'SELECT' || el?.isContentEditable) return null;
  switch (e.key) {
    case 'a':
      return 'approve';
    case 'r':
      return 'reject';
    case 'x':
      return 'select';
    case 'j':
    case 'ArrowDown':
      return 'next';
    case 'k':
    case 'ArrowUp':
      return 'prev';
    default:
      return null;
  }
}

/** Move focus to the previous/next row's main button within the same list. */
export function focusSiblingRow(from: HTMLElement, step: 1 | -1) {
  const rows = Array.from(
    from.closest('[data-review-list]')?.querySelectorAll<HTMLElement>('[data-review-row]') ?? [],
  );
  rows[rows.indexOf(from) + step]?.focus();
}

export function ReviewItemRow({
  item,
  onAction,
  isActioning,
  selected,
  onToggleSelect,
  selectable,
  autoExpand,
  rowRef,
}: {
  item: ReviewItem;
  onAction: (id: string, action: string, comment?: string) => void;
  isActioning: string | null;
  selected?: boolean;
  onToggleSelect?: (id: string) => void;
  selectable?: boolean;
  autoExpand?: boolean;
  rowRef?: (el: HTMLDivElement | null) => void;
}) {
  const [expanded, setExpanded] = useState(!!autoExpand);
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
  const commentRef = useRef<HTMLInputElement>(null);

  // The reason box only opens on an explicit reject (click or R), so take focus.
  useEffect(() => {
    if (commenting) commentRef.current?.focus();
  }, [commenting]);

  useEffect(() => {
    if (autoExpand) setExpanded(true);
  }, [autoExpand]);

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
      toast.success('Override applied');
    } catch (err: any) {
      toast.error(err?.message || 'Override failed');
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
  const isReviewed = item.review_status === 'approved' || item.review_status === 'rejected';

  const handleKeyDown = (e: React.KeyboardEvent<HTMLButtonElement>) => {
    const shortcut = reviewShortcutFor(e);
    if (!shortcut) return;
    e.preventDefault();
    e.stopPropagation();
    if (shortcut === 'next' || shortcut === 'prev') {
      focusSiblingRow(e.currentTarget, shortcut === 'next' ? 1 : -1);
    } else if (shortcut === 'select') {
      if (selectable) onToggleSelect?.(item.id);
    } else if (!isReviewed && !isLoading) {
      if (shortcut === 'approve') onAction(item.id, 'approve');
      else setCommenting(true);
    }
  };

  return (
    <div
      ref={rowRef}
      className={`border-b border-surface-700/20 last:border-0 ${autoExpand ? 'ring-1 ring-deloitte-green/40 bg-deloitte-green/5' : ''}`}
    >
      {/* Main row — expand control is a button; actions sit beside it (no nesting) */}
      <div className="flex items-center gap-2 px-3 py-2 hover:bg-deloitte-green/5 transition-colors">
        {selectable && (
          <input
            type="checkbox"
            checked={!!selected}
            onChange={() => onToggleSelect?.(item.id)}
            onClick={(e) => e.stopPropagation()}
            className="rounded border-surface-600 bg-surface-800 text-deloitte-green focus:ring-deloitte-green/40"
            aria-label={`Select ${item.line_item_name}`}
          />
        )}
        <button
          type="button"
          className="flex items-center gap-2 flex-1 min-w-0 text-left rounded focus:outline-none focus-visible:ring-2 focus-visible:ring-deloitte-green/50"
          onClick={() => setExpanded(!expanded)}
          onKeyDown={handleKeyDown}
          data-review-row
          aria-keyshortcuts="A R X J K ArrowDown ArrowUp"
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

          {/* Risk score */}
          <div className="flex-shrink-0 w-20">
            <RiskBar score={item.ai_risk_score} />
          </div>

          {/* Confidence */}
          <div className="flex-shrink-0 w-10 text-center">
            <ConfBadge score={item.avg_confidence} />
          </div>
        </button>

        {/* Forecast value (editable) — outside expand button */}
        <div className="flex-shrink-0 w-20 text-right">
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

        {/* Actions */}
        <div className="flex-shrink-0 flex items-center gap-1">
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
                type="button"
                onClick={() => onAction(item.id, 'approve')}
                className="p-1 hover:bg-deloitte-green/20 rounded text-deloitte-green/60 hover:text-deloitte-green transition-colors"
                title="Approve"
              >
                <ThumbsUp className="w-3.5 h-3.5" />
              </button>
              <button
                type="button"
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

          <ExogModeScoresCard exog={item.exog} />

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
                    <div className="flex-1 min-w-0">
                      <span className="text-xs font-semibold text-surface-200">{action.label}</span>
                      <p className="text-xs text-surface-300 leading-relaxed">{action.detail}</p>
                    </div>
                    <button
                      type="button"
                      disabled={isLoading}
                      onClick={(e) => {
                        e.stopPropagation();
                        void dispatchRecommendedAction(
                          {
                            type: action.type,
                            label: action.label,
                            detail: action.detail,
                            item_id: item.id,
                          },
                          {
                            openPanel: (type, params) => usePanelStore.getState().openPanel(type, params),
                            onApproveItem: (id) => onAction(id, 'approve'),
                            onRejectItem: (id) => onAction(id, 'reject'),
                            lineItemName: item.line_item_name,
                          },
                        );
                      }}
                      className="flex-shrink-0 text-xs font-medium px-2.5 py-1.5 rounded-lg border border-cyan-500/30 text-cyan-400 hover:bg-cyan-500/10 transition-colors min-h-[36px]"
                    >
                      {action.type === 'confirm_zero' || action.type === 'approve' ? 'Apply' : 'Go'}
                    </button>
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
                      <stop offset="5%" stopColor={COLORS.teal} stopOpacity={0.2} />
                      <stop offset="95%" stopColor={COLORS.teal} stopOpacity={0} />
                    </linearGradient>
                  </defs>
                  <XAxis dataKey="period" tick={{ fill: chartTheme.axis.fill, fontSize: 12 }} axisLine={false} tickLine={false} />
                  <YAxis tick={{ fill: chartTheme.axis.fill, fontSize: 12 }} axisLine={false} tickLine={false} />
                  <Tooltip contentStyle={chartTheme.tooltip.contentStyle} labelStyle={{ color: chartTheme.axis.fill, fontSize: '9px' }} />
                  {item.history[0]?.actual !== undefined && (
                    <Area type="monotone" dataKey="actual" name="Actual" stroke={COLORS.amber} fill="none" strokeWidth={1.5} dot={{ r: 2, fill: COLORS.amber }} />
                  )}
                  <Area type="monotone" dataKey="forecast" name="Forecast" stroke={COLORS.teal} fill={`url(#histGrad-${item.id})`} strokeWidth={1.5} dot={{ r: 2, fill: COLORS.teal }} />
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
              ref={commentRef}
              aria-label={`Rejection reason for ${item.line_item_name}`}
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
