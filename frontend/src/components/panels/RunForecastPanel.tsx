import { useEffect, useRef, useState } from 'react';
import { Play, Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import { apiGet } from '../../api/client';
import { listModelPresets, runForecastWithPreset, type ModelPreset } from '../../api/modelPresets';
import { useI18n } from '../../i18n/useI18n';
import { useAuthStore } from '../../store/authStore';
import { usePanelStore } from '../../store/panelStore';
import { useVersionStore } from '../../store/versionStore';
import { toast } from '../../store/toastStore';

interface BusinessUnit {
  id: string;
  name: string;
}

/** Make a just-generated forecast the app's active version so panels opened
 * afterward (sidebar nav, Header dropdown) show it, not whatever was active
 * before this run — mirrors the same sync done in ChatContainer. */
function syncActiveVersion(versionId: string | null | undefined) {
  if (!versionId) return;
  void useVersionStore
    .getState()
    .refresh()
    .then(() => useVersionStore.getState().setActiveVersionId(versionId));
}

interface JobRecord {
  job_id: string;
  status: string;
  progress?: number;
  step?: string;
  error?: string | null;
  result?: { data?: { version_id?: string } };
}

type RunState = 'idle' | 'running' | 'done' | 'error';

export function RunForecastPanel() {
  const { t } = useI18n();
  const openPanel = usePanelStore((s) => s.openPanel);
  const canViewAllBus = useAuthStore((s) => Boolean(s.user?.can_admin));

  const [presets, setPresets] = useState<ModelPreset[]>([]);
  const [presetId, setPresetId] = useState('');
  const [businessUnits, setBusinessUnits] = useState<BusinessUnit[]>([]);
  const [businessUnitId, setBusinessUnitId] = useState('');
  const [horizonMonths, setHorizonMonths] = useState('');
  const [scenario, setScenario] = useState('base');
  const [submitting, setSubmitting] = useState(false);
  const [runState, setRunState] = useState<RunState>('idle');
  const [step, setStep] = useState('');
  const [progress, setProgress] = useState(0);
  const [error, setError] = useState('');
  const [versionId, setVersionId] = useState<string | null>(null);
  const [warnings, setWarnings] = useState<string[]>([]);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    void listModelPresets()
      .then((rows) => {
        setPresets(rows);
        if (rows.length) setPresetId((prev) => prev || rows[0].id);
      })
      .catch((e) => toast.error(e?.message || t('runForecast.error.loadPresets')));
    // Only relevant to cross-company admins — a regular user belongs to at
    // most one business unit, which the backend infers automatically.
    if (canViewAllBus) {
      void apiGet<BusinessUnit[]>('/admin/business-units')
        .then(setBusinessUnits)
        .catch(() => {
          /* non-fatal: falls back to backend inference / explicit dataset_id */
        });
    }
    return () => {
      if (pollRef.current) clearInterval(pollRef.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const stopPolling = () => {
    if (pollRef.current) {
      clearInterval(pollRef.current);
      pollRef.current = null;
    }
  };

  const pollJob = (jobId: string) => {
    const poll = async () => {
      try {
        const job = await apiGet<JobRecord>(`/jobs/${jobId}`);
        if (job.progress != null) setProgress(job.progress);
        if (job.step) setStep(job.step);
        if (job.status === 'completed') {
          stopPolling();
          setRunState('done');
          const newVersionId = job.result?.data?.version_id || null;
          setVersionId(newVersionId);
          syncActiveVersion(newVersionId);
        } else if (job.status === 'failed') {
          stopPolling();
          setRunState('error');
          setError(job.error || t('runForecast.error.jobFailed'));
        }
      } catch (e: any) {
        stopPolling();
        setRunState('error');
        setError(e?.message || t('runForecast.error.jobFailed'));
      }
    };
    void poll();
    pollRef.current = setInterval(() => {
      if (!document.hidden) void poll();
    }, 2000);
  };

  const handleRun = async () => {
    if (!presetId) {
      toast.error(t('runForecast.error.noPreset'));
      return;
    }
    setSubmitting(true);
    setRunState('running');
    setError('');
    setVersionId(null);
    setWarnings([]);
    setStep(t('runForecast.starting'));
    setProgress(0);
    try {
      const res = await runForecastWithPreset(presetId, {
        business_unit: businessUnitId || undefined,
        horizon_months: horizonMonths ? Number(horizonMonths) : undefined,
        scenario: scenario.trim() || 'base',
        async_job: true,
      });
      setWarnings(res.warnings || []);
      if (!res.success) {
        setRunState('error');
        setError(res.message);
        return;
      }
      if (res.data?.job_id && res.data.status !== 'completed' && res.data.status !== 'failed') {
        pollJob(res.data.job_id);
      } else {
        // Ran synchronously (no async worker configured) — already finished.
        setRunState('done');
        const newVersionId = res.data?.version_id || null;
        setVersionId(newVersionId);
        syncActiveVersion(newVersionId);
      }
    } catch (e: any) {
      setRunState('error');
      setError(e?.message || t('runForecast.error.run'));
    } finally {
      setSubmitting(false);
    }
  };

  const pct = Math.round(progress * 100);

  return (
    <div className="space-y-4">
      <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3 space-y-3">
        <div className="flex items-center gap-2">
          <Play className="w-4 h-4 text-deloitte-green" />
          <span className="text-sm font-semibold text-white">{t('runForecast.title')}</span>
        </div>
        <p className="text-xs text-surface-400">{t('runForecast.subtitle')}</p>

        {presets.length === 0 ? (
          <p className="text-xs text-surface-500">{t('runForecast.emptyPresets')}</p>
        ) : (
          <>
            <label className="block space-y-1">
              <span className="text-xs text-surface-500">{t('runForecast.preset')}</span>
              <select
                value={presetId}
                onChange={(e) => setPresetId(e.target.value)}
                className="w-full text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
              >
                {presets.map((p) => (
                  <option key={p.id} value={p.id}>
                    {p.name} ({p.model_type})
                  </option>
                ))}
              </select>
            </label>

            {canViewAllBus && businessUnits.length > 0 && (
              <label className="block space-y-1">
                <span className="text-xs text-surface-500">{t('runForecast.businessUnit')}</span>
                <select
                  value={businessUnitId}
                  onChange={(e) => setBusinessUnitId(e.target.value)}
                  className="w-full text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
                >
                  <option value="">{t('runForecast.businessUnitInferred')}</option>
                  {businessUnits.map((bu) => (
                    <option key={bu.id} value={bu.id}>
                      {bu.name}
                    </option>
                  ))}
                </select>
              </label>
            )}

            <div className="grid grid-cols-2 gap-2">
              <label className="block space-y-1">
                <span className="text-xs text-surface-500">{t('runForecast.horizon')}</span>
                <input
                  type="number"
                  min={1}
                  max={60}
                  value={horizonMonths}
                  onChange={(e) => setHorizonMonths(e.target.value)}
                  placeholder={t('runForecast.horizonPlaceholder')}
                  className="w-full text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
                />
              </label>
              <label className="block space-y-1">
                <span className="text-xs text-surface-500">{t('runForecast.scenario')}</span>
                <input
                  value={scenario}
                  onChange={(e) => setScenario(e.target.value)}
                  className="w-full text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
                />
              </label>
            </div>

            <button
              type="button"
              onClick={() => void handleRun()}
              disabled={submitting || runState === 'running'}
              className="inline-flex items-center gap-1.5 text-xs font-medium px-3 py-2 rounded-lg bg-deloitte-green text-white hover:bg-deloitte-green/90 disabled:opacity-50"
            >
              {runState === 'running' ? (
                <Loader2 className="w-3.5 h-3.5 animate-spin" />
              ) : (
                <Play className="w-3.5 h-3.5" />
              )}
              {t('runForecast.run')}
            </button>
          </>
        )}
      </div>

      {runState === 'running' && (
        <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3 space-y-2">
          <div className="w-full bg-surface-700 rounded-full h-1.5">
            <div
              className="bg-deloitte-green h-1.5 rounded-full transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="flex justify-between text-xs text-surface-500">
            <span>{step}</span>
            <span>{pct}%</span>
          </div>
        </div>
      )}

      {runState === 'error' && (
        <div className="bg-surface-800/60 border border-red-500/30 rounded-xl p-3 flex items-start gap-2">
          <AlertCircle className="w-4 h-4 text-red-400 shrink-0 mt-0.5" />
          <p className="text-xs text-red-400">{error}</p>
        </div>
      )}

      {runState === 'done' && (
        <div className="bg-surface-800/60 border border-deloitte-green/25 rounded-xl p-3 space-y-2">
          <div className="flex items-center gap-2">
            <CheckCircle2 className="w-4 h-4 text-deloitte-green" />
            <p className="text-sm text-white font-semibold">{t('runForecast.done')}</p>
          </div>
          {warnings.map((w) => (
            <p key={w} className="text-xs text-yellow-400">
              {w}
            </p>
          ))}
          {versionId && (
            <button
              type="button"
              onClick={() => openPanel('forecast_table', { version_id: versionId })}
              className="text-xs text-deloitte-green hover:underline"
            >
              {t('runForecast.openTable')}
            </button>
          )}
        </div>
      )}
    </div>
  );
}
