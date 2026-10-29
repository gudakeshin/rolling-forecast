import { useEffect, useState } from 'react';
import { CheckCircle2, Circle } from 'lucide-react';
import { apiGet } from '../../api/client';
import { useI18n } from '../../i18n/useI18n';
import type { MessageKey } from '../../i18n';
import { useNavItems } from '../../hooks/useNavItems';
import { useAuthStore } from '../../store/authStore';
import { useVersionStore } from '../../store/versionStore';

interface Step {
  key: string;
  labelKey: MessageKey;
  done: boolean;
  /** Nav item that performs the step; its permission gates the button. */
  navKey: string;
}

export function firstRunSteps(hasActuals: boolean, statuses: string[]): Step[] {
  const reviewed = statuses.some((s) => ['in_review', 'approved', 'published'].includes(s));
  return [
    { key: 'upload', labelKey: 'start.upload', done: hasActuals, navKey: 'document_library' },
    { key: 'generate', labelKey: 'start.generate', done: statuses.length > 0, navKey: 'run_forecast' },
    { key: 'review', labelKey: 'start.review', done: reviewed, navKey: 'review_dashboard' },
    { key: 'publish', labelKey: 'start.publish', done: statuses.includes('published'), navKey: 'approvals' },
  ];
}

/** First-run guided path (upload → generate → review → publish) built from
 * what the workspace actually contains; it disappears once every step is done. */
export function GettingStarted() {
  const { t } = useI18n();
  const navGroups = useNavItems();
  const versions = useVersionStore((s) => s.versions);
  const businessUnitId = useAuthStore((s) => s.user?.business_unit_id);
  const [hasActuals, setHasActuals] = useState<boolean | null>(null);

  useEffect(() => {
    let cancelled = false;
    apiGet<unknown[]>('/upload/datasets')
      .then((rows) => !cancelled && setHasActuals(rows.length > 0))
      .catch(() => !cancelled && setHasActuals(null));
    return () => {
      cancelled = true;
    };
  }, [businessUnitId]);

  if (hasActuals === null) return null;
  const steps = firstRunSteps(hasActuals, versions.map((v) => v.status));
  const doneCount = steps.filter((s) => s.done).length;
  if (doneCount === steps.length) return null;

  const navItems = navGroups.flatMap((g) => g.items);
  const nextIndex = steps.findIndex((s) => !s.done);

  return (
    <section
      aria-label={t('start.title')}
      className="mb-8 text-left bg-surface-800/60 border border-surface-700/50 rounded-xl p-4"
    >
      <div className="flex items-center justify-between mb-3">
        <h3 className="text-sm font-semibold text-surface-200">{t('start.title')}</h3>
        <span className="text-xs text-surface-500">
          {t('start.progress', { done: doneCount, total: steps.length })}
        </span>
      </div>
      <div
        className="h-1 bg-surface-700 rounded-full mb-3 overflow-hidden"
        role="progressbar"
        aria-valuemin={0}
        aria-valuemax={steps.length}
        aria-valuenow={doneCount}
      >
        <div className="h-full bg-deloitte-green" style={{ width: `${(doneCount / steps.length) * 100}%` }} />
      </div>
      <ol className="space-y-1.5">
        {steps.map((step, i) => {
          const nav = navItems.find((n) => n.key === step.navKey);
          const actionable = !step.done && nav?.show && !nav.disabledReason;
          return (
            <li key={step.key} className="flex items-center gap-2 text-sm">
              {step.done ? (
                <CheckCircle2 className="w-4 h-4 text-deloitte-green shrink-0" aria-hidden="true" />
              ) : (
                <Circle className="w-4 h-4 text-surface-500 shrink-0" aria-hidden="true" />
              )}
              <span className={step.done ? 'text-surface-500 line-through' : 'text-surface-300'}>
                {t(step.labelKey)}
              </span>
              {step.done && <span className="sr-only">{t('start.done')}</span>}
              {actionable && (
                <button
                  type="button"
                  onClick={nav!.onClick}
                  className={`ml-auto px-2.5 py-0.5 text-xs rounded-md ${
                    i === nextIndex
                      ? 'bg-deloitte-green text-white font-semibold'
                      : 'border border-surface-700 text-surface-300 hover:text-white'
                  }`}
                >
                  {t('start.go')}
                </button>
              )}
            </li>
          );
        })}
      </ol>
    </section>
  );
}
