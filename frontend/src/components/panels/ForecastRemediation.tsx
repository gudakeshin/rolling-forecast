/** Expanded row detail in the forecast table: drivers, history, exog validation and remediation actions. */
import {
  Activity, Bot, CheckCircle2, Edit3, Eye, GitBranch, Info, MessageSquare, TrendingDown,
  TrendingUp, XCircle, Zap,
} from 'lucide-react';
import { useCallback, useState } from 'react';
import { useComposerStore } from '../../store/composerStore';
import { usePanelStore } from '../../store/panelStore';
import {
  ActionIcon, MaterialityBadge, RootCauseBadge, actionButtonColor, chatCommandForAction,
} from './ForecastTableCells';
import { type ForecastRow } from './ForecastTableTypes';
import { ExogModeScoresCard } from './shared/ExogModeScoresCard';
import { type AIAction } from './shared/forecastContext';
import { formatCurrency } from './shared/format';

// ── Expanded Remediation Component ──────────────
export function ExpandedRemediation({
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
  const prefillComposer = useComposerStore((st) => st.prefill);

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
      // Stay on forecast table — put the chat command in the composer so the user can act via the agent
      const chatCmd = chatCommandForAction(action, row);
      if (chatCmd) {
        prefillComposer(chatCmd);
        setCopiedAction(action.type);
        setTimeout(() => setCopiedAction(null), 2000);
      }
      return;
    }
    if (action.type === 'investigate' || action.type === 'compare_peers') {
      openPanel(action.type === 'compare_peers' ? 'accuracy_tracking' : 'anomaly_dashboard', {});
      const chatCmd = chatCommandForAction(action, row);
      if (chatCmd) {
        prefillComposer(chatCmd);
        setCopiedAction(action.type);
        setTimeout(() => setCopiedAction(null), 2000);
      }
      return;
    }

    const chatCmd = chatCommandForAction(action, row);
    if (chatCmd) {
      prefillComposer(chatCmd);
      setCopiedAction(action.type);
      setTimeout(() => setCopiedAction(null), 2000);
    }
  }, [row, onAction, openPanel, prefillComposer]);

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
          <ExogModeScoresCard exog={row.exog} indent />

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
                          In chat
                        </>
                      ) : (
                        <>
                          <ActionIcon type={action.type} />
                          {action.type === 'approve' || action.type === 'confirm_zero'
                            ? action.label
                            : (
                              <>
                                <MessageSquare className="w-2.5 h-2.5 opacity-50" />
                                Ask in chat
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
