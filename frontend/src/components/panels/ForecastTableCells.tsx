/** Forecast-table cells: line item, editable value, row actions, and the small badges. */
import {
  AlertTriangle, ArrowRightLeft, CheckCircle2, ChevronDown, ChevronRight, CircleDot, Edit3, Eye,
  Info, Lightbulb, Loader2, RefreshCw, Save, Search, ShieldCheck, Sparkles, Upload, UserCheck, X,
  XCircle, Zap,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';
import { apiPost } from '../../api/client';
import { toast } from '../../store/toastStore';
import { type ForecastRow } from './ForecastTableTypes';
import { type AIAction } from './shared/forecastContext';
import { formatCurrency } from './shared/format';

// ── Line Item Cell ───────────────────────────
export function ForecastLineItemCell({
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
        {row.models_disagree && (
          <span
            className="ml-1 px-1.5 py-0.5 rounded text-[10px] bg-amber-500/15 text-amber-400 flex-shrink-0"
            title="The forecast is outside Chronos-2's P10–P90 for most of the horizon"
            data-testid="models-disagree-badge"
          >
            Models disagree
          </span>
        )}
      </div>
    </div>
  );
}

// ── Editable Forecast Value Cell ─────────────
export function ForecastValueCell({
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
export function ForecastRowActions({
  row,
  onAction,
  isActioning,
  onToggle,
  onExplain,
}: {
  row: ForecastRow;
  onAction: (id: string, action: string) => void;
  isActioning: boolean;
  onToggle: () => void;
  onExplain: () => void;
}) {
  const recommendation = row.ai_recommendation;
  const reasoning = row.ai_reasoning;
  const hasIssue = recommendation && recommendation !== 'approve';
  const isReviewed = row.review_status === 'approved' || row.review_status === 'rejected';

  const explainButton = (
    <button
      onClick={(e) => {
        e.stopPropagation();
        onExplain();
      }}
      className="p-1 rounded-md hover:bg-surface-700 text-surface-600 hover:text-amber-300 transition-colors"
      title="Why this number?"
    >
      <Lightbulb className="w-3.5 h-3.5" />
    </button>
  );

  if (isReviewed) {
    return (
      <div className="flex items-center gap-1.5 justify-center">
        <span className="text-xs text-surface-500">Done</span>
        {explainButton}
      </div>
    );
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
        {explainButton}
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
      {explainButton}
    </div>
  );
}

// ── Action Icon Mapping ─────────────────────
export function ActionIcon({ type }: { type: string }) {
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

export function actionButtonColor(type: string): string {
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

export function chatCommandForAction(action: AIAction, row: ForecastRow): string | null {
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
export function RootCauseBadge({ cause }: { cause: string }) {
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
export function MaterialityBadge({ level }: { level: string }) {
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

// ── Confidence Indicator ─────────────────────
export function ConfidenceIndicator({ score, level }: { score: number; level: string }) {
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
export function StatusBadge({
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
