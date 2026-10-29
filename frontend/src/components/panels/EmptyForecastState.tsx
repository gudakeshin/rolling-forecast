import { BarChart3, Play, Upload } from 'lucide-react';
import { useI18n } from '../../i18n/useI18n';
import { useCan } from '../../store/authStore';
import { usePanelStore } from '../../store/panelStore';

/** Empty forecast views point at the next step instead of dead-ending. */
export function EmptyForecastState() {
  const { t } = useI18n();
  const openPanel = usePanelStore((s) => s.openPanel);
  const canGenerate = useCan('can_generate');
  const canInput = useCan('can_input');

  return (
    <div className="flex flex-col items-center justify-center py-12 gap-3 text-center px-4">
      <BarChart3 className="w-8 h-8 text-surface-600" aria-hidden="true" />
      <div>
        <p className="text-sm font-medium text-surface-300">{t('empty.forecast.title')}</p>
        <p className="text-xs text-surface-500 mt-1">{t('empty.forecast.body')}</p>
      </div>
      <div className="flex items-center gap-2">
        {canInput && (
          <button
            type="button"
            onClick={() => openPanel('document_library', {})}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-surface-300 hover:text-white bg-surface-800 hover:bg-surface-700 border border-surface-700 rounded-lg"
          >
            <Upload className="w-3.5 h-3.5" aria-hidden="true" />
            {t('empty.forecast.upload')}
          </button>
        )}
        {canGenerate && (
          <button
            type="button"
            onClick={() => openPanel('run_forecast', {})}
            className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-semibold text-white bg-deloitte-green hover:bg-deloitte-green/90 rounded-lg"
          >
            <Play className="w-3.5 h-3.5" aria-hidden="true" />
            {t('empty.forecast.run')}
          </button>
        )}
      </div>
    </div>
  );
}
