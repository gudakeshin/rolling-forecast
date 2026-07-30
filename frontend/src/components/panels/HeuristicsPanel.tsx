import { useCallback, useEffect, useState } from 'react';
import {
  AlertTriangle, Check, Info, Loader2, Play, RefreshCw, X,
} from 'lucide-react';
import {
  listHeuristics,
  promoteHeuristic,
  rejectHeuristic,
  runReflectionPass,
  type HeuristicKind,
  type HeuristicStatus,
  type LearnedHeuristic,
} from '../../api/heuristics';
import { useCan } from '../../store/authStore';
import { toast } from '../../store/toastStore';
import { useI18n } from '../../i18n/useI18n';
import type { MessageKey } from '../../i18n';

const STATUS_TABS: HeuristicStatus[] = ['candidate', 'active', 'rejected', 'superseded'];

const STATUS_CLASS: Record<HeuristicStatus, string> = {
  candidate: 'bg-amber-500/10 border-amber-500/20 text-amber-400',
  active: 'bg-deloitte-green/10 border-deloitte-green/20 text-deloitte-green',
  rejected: 'bg-red-500/10 border-red-500/20 text-red-400',
  superseded: 'bg-surface-700/40 border-surface-600 text-surface-400',
};

const STATUS_LABEL: Record<HeuristicStatus, MessageKey> = {
  candidate: 'heuristics.status.candidate',
  active: 'heuristics.status.active',
  rejected: 'heuristics.status.rejected',
  superseded: 'heuristics.status.superseded',
};

const KIND_LABEL: Record<HeuristicKind, MessageKey> = {
  error_bias: 'heuristics.kind.errorBias',
  override_pattern: 'heuristics.kind.overridePattern',
};

function formatEffect(value: number | null): string {
  if (value === null || Number.isNaN(value)) return '—';
  return `${value > 0 ? '+' : ''}${value.toFixed(1)}%`;
}

export function HeuristicsPanel() {
  const { t } = useI18n();
  const canReview = useCan('can_review');
  const canAdmin = useCan('can_admin');
  const canDecide = canReview || canAdmin;

  const [status, setStatus] = useState<HeuristicStatus>('candidate');
  const [rows, setRows] = useState<LearnedHeuristic[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [running, setRunning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const res = await listHeuristics({ status, limit: 100 });
      setRows(res.heuristics);
    } catch (e: any) {
      setError(e?.message || t('heuristics.error.load'));
    } finally {
      setLoading(false);
    }
  }, [status, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const decide = async (row: LearnedHeuristic, action: 'promote' | 'reject') => {
    setBusyId(row.id);
    try {
      const updated =
        action === 'promote'
          ? await promoteHeuristic(row.id)
          : await rejectHeuristic(row.id);
      toast.success(
        action === 'promote'
          ? t('heuristics.success.promoted')
          : t('heuristics.success.rejected'),
      );
      if (action === 'promote' && updated.influences_selection === false) {
        toast.info(t('heuristics.advisoryOnly'));
      }
      await load();
    } catch (e: any) {
      toast.error(e?.message || t('heuristics.error.decide'));
    } finally {
      setBusyId(null);
    }
  };

  const runPass = async () => {
    setRunning(true);
    try {
      const summary = await runReflectionPass();
      toast.success(
        t('heuristics.success.run', {
          created: summary.created,
          updated: summary.updated,
        }),
      );
      setStatus('candidate');
      await load();
    } catch (e: any) {
      toast.error(e?.message || t('heuristics.error.run'));
    } finally {
      setRunning(false);
    }
  };

  return (
    <div className="space-y-3">
      <p className="text-xs text-surface-400">{t('heuristics.subtitle')}</p>

      <div className="flex items-center justify-between gap-2">
        <div className="flex flex-wrap gap-1">
          {STATUS_TABS.map((tab) => (
            <button
              key={tab}
              type="button"
              onClick={() => setStatus(tab)}
              className={`px-2.5 py-1 text-xs rounded-md border transition-colors ${
                status === tab
                  ? 'bg-deloitte-green/15 border-deloitte-green/40 text-deloitte-green'
                  : 'bg-surface-800/60 border-surface-700/50 text-surface-400 hover:text-white'
              }`}
            >
              {t(STATUS_LABEL[tab])}
            </button>
          ))}
        </div>
        <div className="flex items-center gap-1.5">
          {canDecide && (
            <button
              type="button"
              onClick={() => void runPass()}
              disabled={running}
              className="inline-flex items-center gap-1.5 text-xs text-surface-300 hover:text-white px-2 py-1 rounded-md border border-surface-600 hover:border-deloitte-green/40 disabled:opacity-50"
              title={t('heuristics.run')}
            >
              {running ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Play className="w-3.5 h-3.5" />
              )}
              {t('heuristics.run')}
            </button>
          )}
          <button
            type="button"
            onClick={() => void load()}
            disabled={loading}
            className="inline-flex items-center gap-1 text-xs text-surface-400 hover:text-deloitte-green disabled:opacity-50"
            title={t('heuristics.refresh')}
          >
            <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          </button>
        </div>
      </div>

      {loading ? (
        <div className="flex flex-col items-center justify-center h-24 gap-2">
          <Loader2 className="w-5 h-5 animate-spin text-deloitte-green" />
        </div>
      ) : error ? (
        <div className="flex flex-col items-center justify-center h-24 gap-2 text-center px-4">
          <AlertTriangle className="w-5 h-5 text-amber-400" />
          <p className="text-sm text-amber-300">{error}</p>
        </div>
      ) : rows.length === 0 ? (
        <p className="text-sm text-surface-500 text-center py-6">
          {t('heuristics.empty')}
        </p>
      ) : (
        <div className="space-y-2">
          {rows.map((row) => (
            <div
              key={row.id}
              className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3 space-y-2"
            >
              <div className="flex items-start justify-between gap-2">
                <p className="text-xs text-white leading-relaxed">{row.statement}</p>
                <span
                  className={`shrink-0 px-2 py-0.5 text-xs font-medium rounded-md border ${
                    STATUS_CLASS[row.status]
                  }`}
                >
                  {t(STATUS_LABEL[row.status])}
                </span>
              </div>

              <div className="flex flex-wrap gap-x-3 gap-y-1 text-xs text-surface-500">
                <span>{t(KIND_LABEL[row.kind])}</span>
                <span>
                  {t('heuristics.effect')}: {formatEffect(row.effect_size)}
                </span>
                {row.model_type && (
                  <span>
                    {t('heuristics.model')}: {row.model_type}
                  </span>
                )}
                {row.horizon_bucket && (
                  <span>
                    {t('heuristics.horizon')}: {row.horizon_bucket}
                  </span>
                )}
                {row.source && (
                  <span>
                    {t('heuristics.source')}: {row.source}
                  </span>
                )}
              </div>

              {row.status === 'active' && !row.influences_selection && (
                <p className="flex items-start gap-1.5 text-xs text-surface-400">
                  <Info className="w-3.5 h-3.5 shrink-0 mt-px text-amber-400" />
                  {t('heuristics.advisoryOnly')}
                </p>
              )}

              {canDecide && (row.status === 'candidate' || row.status === 'active') && (
                <div className="flex gap-2 pt-0.5">
                  {row.status === 'candidate' && (
                    <button
                      type="button"
                      disabled={busyId === row.id}
                      onClick={() => void decide(row, 'promote')}
                      className="inline-flex items-center gap-1 px-2.5 py-1.5 bg-deloitte-green text-white rounded-lg text-xs font-semibold hover:bg-deloitte-green/90 disabled:opacity-50"
                    >
                      <Check className="w-3.5 h-3.5" />
                      {t('heuristics.promote')}
                    </button>
                  )}
                  <button
                    type="button"
                    disabled={busyId === row.id}
                    onClick={() => void decide(row, 'reject')}
                    className="inline-flex items-center gap-1 px-2.5 py-1.5 bg-red-500/15 text-red-400 border border-red-500/25 rounded-lg text-xs hover:bg-red-500/25 disabled:opacity-50"
                  >
                    <X className="w-3.5 h-3.5" />
                    {t('heuristics.reject')}
                  </button>
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
