import { useEffect, useState } from 'react';
import {
  X, Loader2, Table, GitCompare, Edit, ClipboardList, Settings2,
  LayoutDashboard, Shield, Target, FileInput, AlertTriangle, FolderOpen,
} from 'lucide-react';
import { usePanelStore } from '../../store/panelStore';
import { apiGet } from '../../api/client';
import { ForecastTablePanel } from './ForecastTablePanel';
import { OverridesPanel } from './OverridesPanel';
import { ComparisonPanel } from './ComparisonPanel';
import { SkillEditorPanel } from './SkillEditorPanel';
import { ExecutiveDashboardPanel } from './ExecutiveDashboardPanel';
import { ReviewDashboardPanel } from './ReviewDashboardPanel';
import { AccuracyTrackingPanel } from './AccuracyTrackingPanel';
import { DriverInputPanel } from './DriverInputPanel';
import { AnomalyPanel } from './AnomalyPanel';
import { DocumentLibraryPanel } from './DocumentLibraryPanel';

const panelIcons: Record<string, any> = {
  forecast_table: Table,
  review_queue: ClipboardList,
  overrides: Edit,
  comparison: GitCompare,
  skill_editor: Settings2,
  executive_dashboard: LayoutDashboard,
  review_dashboard: Shield,
  accuracy_tracking: Target,
  driver_inputs: FileInput,
  anomaly_dashboard: AlertTriangle,
  document_library: FolderOpen,
};

export function PanelContainer() {
  const { panelType, panelParams, panelData, isLoading, closePanel, setPanelData, setLoading } =
    usePanelStore();
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!panelType || !panelParams) return;

    // Skill editor doesn't need API data fetch — it handles its own data
    if (panelType === 'skill_editor') {
      setPanelData({ panel_type: 'skill_editor', title: 'Skill Editor', data: {} });
      return;
    }

    const fetchData = async () => {
      setLoading(true);
      setLoadError(null);
      try {
        let url = '';
        switch (panelType) {
          case 'forecast_table':
            url = `/panel/forecast-table/${panelParams.version_id}`;
            break;
          case 'review_queue':
            url = `/panel/review-queue/${panelParams.version_id}`;
            break;
          case 'overrides':
            url = `/panel/overrides/${panelParams.version_id}`;
            break;
          case 'comparison':
            url = `/panel/comparison/${panelParams.version_id_a}/${panelParams.version_id_b}`;
            break;
          case 'executive_dashboard':
            url = `/panel/executive-dashboard/${panelParams.version_id}`;
            break;
          case 'review_dashboard':
            url = `/panel/review-dashboard/${panelParams.version_id}`;
            break;
          case 'accuracy_tracking':
            url = `/panel/accuracy-tracking/${panelParams.version_id}`;
            break;
          case 'driver_inputs':
            url = `/panel/driver-inputs/${panelParams.version_id}`;
            break;
          case 'anomaly_dashboard':
            url = `/panel/anomaly-dashboard/${panelParams.version_id}`;
            break;
          case 'document_library':
            url = `/context/document-panel/${panelParams.conversation_id || 'default'}`;
            break;
          default:
            setLoading(false);
            return;
        }
        const data = await apiGet(url);
        setPanelData(data as any);
      } catch (error: any) {
        console.error('Failed to load panel data:', error);
        setPanelData(null);
        setLoadError(error?.message || 'Failed to load panel data. Upstream data may be missing.');
      } finally {
        setLoading(false);
      }
    };
    fetchData();
  }, [panelType, panelParams]);

  const Icon = panelIcons[panelType || ''] || Table;

  // Skill editor gets a full-height, no-header treatment
  if (panelType === 'skill_editor') {
    return (
      <div className="h-full flex flex-col">
        <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700/50 bg-surface-800">
          <div className="flex items-center gap-2">
            <div className="w-0.5 h-4 bg-deloitte-green rounded-full" />
            <Settings2 className="w-4 h-4 text-deloitte-green" />
            <h3 className="text-sm font-semibold text-white">Skill Editor</h3>
          </div>
          <button
            onClick={closePanel}
            className="p-1.5 hover:bg-surface-700 rounded-lg transition-colors text-surface-400 hover:text-white"
          >
            <X className="w-4 h-4" />
          </button>
        </div>
        <div className="flex-1 overflow-hidden">
          <SkillEditorPanel />
        </div>
      </div>
    );
  }

  return (
    <div
      className="h-full flex flex-col"
      role="dialog"
      aria-modal="true"
      aria-labelledby="panel-title"
    >
      {/* Panel header */}
      <div className="flex items-center justify-between px-4 py-3 border-b border-surface-700/50 bg-surface-800">
        <div className="flex items-center gap-2">
          <div className="w-0.5 h-4 bg-deloitte-green rounded-full" />
          <Icon className="w-4 h-4 text-deloitte-green" aria-hidden="true" />
          <h3 id="panel-title" className="text-sm font-semibold text-white">
            {panelData?.title || panelType?.replace(/_/g, ' ') || 'Details'}
          </h3>
        </div>
        <button
          type="button"
          onClick={closePanel}
          aria-label="Close panel"
          className="p-1.5 hover:bg-surface-700 rounded-lg transition-colors text-surface-400 hover:text-white min-h-[44px] min-w-[44px] flex items-center justify-center"
        >
          <X className="w-4 h-4" aria-hidden="true" />
        </button>
      </div>

      {/* Panel content */}
      <div className="flex-1 overflow-y-auto p-4">
        {isLoading ? (
          <div className="flex flex-col items-center justify-center h-32 gap-2">
            <Loader2 className="w-6 h-6 animate-spin text-deloitte-green" />
            <span className="text-xs text-surface-500">Loading data...</span>
          </div>
        ) : loadError ? (
          <div className="flex flex-col items-center justify-center h-32 gap-2 text-center px-4">
            <AlertTriangle className="w-6 h-6 text-amber-400" />
            <p className="text-sm text-amber-300">{loadError}</p>
            <p className="text-xs text-surface-500">
              Ensure the forecast version exists and required upstream steps have completed.
            </p>
          </div>
        ) : panelData ? (
          <PanelContent type={panelType} data={panelData} />
        ) : (
          <p className="text-surface-500 text-sm text-center">No data available</p>
        )}
      </div>
    </div>
  );
}

function PanelContent({ type, data }: { type: string | null; data: any }) {
  switch (type) {
    case 'forecast_table':
    case 'review_queue':
      return <ForecastTablePanel data={data} />;
    case 'overrides':
      return <OverridesPanel data={data} />;
    case 'comparison':
      return <ComparisonPanel data={data} />;
    case 'executive_dashboard':
      return <ExecutiveDashboardPanel data={data} />;
    case 'review_dashboard':
      return <ReviewDashboardPanel data={data} />;
    case 'accuracy_tracking':
      return <AccuracyTrackingPanel data={data} />;
    case 'driver_inputs':
      return <DriverInputPanel data={data} />;
    case 'anomaly_dashboard':
      return <AnomalyPanel data={data} />;
    case 'document_library':
      return <DocumentLibraryPanel data={data} />;
    default:
      return (
        <pre className="text-xs text-surface-400 whitespace-pre-wrap">
          {JSON.stringify(data, null, 2)}
        </pre>
      );
  }
}
