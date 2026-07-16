import { useEffect, useState } from 'react';
import {
  Loader2, Table, GitCompare, Edit, ClipboardList, Settings2,
  LayoutDashboard, Shield, Target, FileInput, AlertTriangle, FolderOpen,
  CheckSquare,
} from 'lucide-react';
import { usePanelStore } from '../../store/panelStore';
import { toast } from '../../store/toastStore';
import { apiGet } from '../../api/client';
import { SlidePanel } from '../ui/SlidePanel';
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
import { ApprovalsPanel } from './ApprovalsPanel';

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
  approvals: CheckSquare,
};

export function PanelContainer() {
  const {
    panelType,
    panelParams,
    panelData,
    isLoading,
    closePanel,
    setPanelData,
    setLoading,
    widthMode,
    toggleWidth,
    refreshPanel,
  } = usePanelStore();
  const [loadError, setLoadError] = useState<string | null>(null);

  useEffect(() => {
    if (!panelType || !panelParams) return;

    if (panelType === 'skill_editor' || panelType === 'approvals') {
      setPanelData({
        panel_type: panelType,
        title: panelType === 'approvals' ? 'Approvals' : 'Skill Editor',
        data: {},
      });
      return;
    }

    const controller = new AbortController();
    const append = Boolean(panelParams._append);
    const offset = Number(panelParams.offset || 0);

    const fetchData = async () => {
      if (!append) setLoading(true);
      setLoadError(null);
      try {
        let url = '';
        switch (panelType) {
          case 'forecast_table': {
            const qs = new URLSearchParams();
            if (panelParams.view) qs.set('view', String(panelParams.view));
            if (panelParams.category) qs.set('category', String(panelParams.category));
            if (panelParams.confidence_level) qs.set('confidence_level', String(panelParams.confidence_level));
            qs.set('offset', String(offset));
            if (panelParams.limit) qs.set('limit', String(panelParams.limit));
            url = `/panel/forecast-table/${panelParams.version_id}?${qs.toString()}`;
            break;
          }
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
        const data = await apiGet<any>(url, { signal: controller.signal });
        if (append && panelType === 'forecast_table') {
          const current = usePanelStore.getState().panelData;
          const prevRows = (current?.data?.rows as unknown[]) || [];
          const newRows = data?.data?.rows || [];
          setPanelData({
            ...data,
            data: {
              ...data.data,
              rows: [...prevRows, ...newRows],
            },
          });
        } else {
          setPanelData(data);
        }
      } catch (error: any) {
        if (error?.name === 'AbortError' || controller.signal.aborted) return;
        console.error('Failed to load panel data:', error);
        if (!append) {
          const message = error?.message || 'Failed to load panel data';
          setPanelData(null);
          setLoadError(message);
          toast.error(message);
        }
      } finally {
        if (!controller.signal.aborted) setLoading(false);
      }
    };
    fetchData();
    return () => controller.abort();
  }, [panelType, panelParams, setLoading, setPanelData]);

  const Icon = panelIcons[panelType || ''] || Table;

  if (panelType === 'skill_editor') {
    return (
      <SlidePanel
        title="Skill Editor"
        icon={<Settings2 className="w-4 h-4 text-deloitte-green" aria-hidden="true" />}
        onClose={closePanel}
        widthExpanded={widthMode === 'wide'}
        onToggleWidth={toggleWidth}
      >
        <div className="-m-4 h-[calc(100%+2rem)] overflow-hidden">
          <SkillEditorPanel />
        </div>
      </SlidePanel>
    );
  }

  if (panelType === 'approvals') {
    return (
      <SlidePanel
        title="Approvals"
        icon={<CheckSquare className="w-4 h-4 text-deloitte-green" aria-hidden="true" />}
        onClose={closePanel}
        widthExpanded={widthMode === 'wide'}
        onToggleWidth={toggleWidth}
      >
        <ApprovalsPanel />
      </SlidePanel>
    );
  }

  return (
    <SlidePanel
      title={panelData?.title || panelType?.replace(/_/g, ' ') || 'Details'}
      icon={<Icon className="w-4 h-4 text-deloitte-green" aria-hidden="true" />}
      onClose={closePanel}
      widthExpanded={widthMode === 'wide'}
      onToggleWidth={toggleWidth}
    >
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
        <PanelContent type={panelType} data={panelData} onRefresh={refreshPanel} />
      ) : (
        <p className="text-surface-500 text-sm text-center">No data available</p>
      )}
    </SlidePanel>
  );
}

function PanelContent({
  type,
  data,
  onRefresh,
}: {
  type: string | null;
  data: any;
  onRefresh: () => void;
}) {
  const focusLineItemId = usePanelStore((s) => s.panelParams.focus_line_item_id);

  switch (type) {
    case 'forecast_table':
    case 'review_queue':
      return <ForecastTablePanel data={data} onRefresh={onRefresh} />;
    case 'overrides':
      return <OverridesPanel data={data} onRefresh={onRefresh} />;
    case 'comparison':
      return <ComparisonPanel data={data} />;
    case 'executive_dashboard':
      return <ExecutiveDashboardPanel data={data} onRefresh={onRefresh} />;
    case 'review_dashboard':
      return (
        <ReviewDashboardPanel
          data={data}
          onRefresh={onRefresh}
          focusLineItemId={focusLineItemId}
        />
      );
    case 'accuracy_tracking':
      return <AccuracyTrackingPanel data={data} onRefresh={onRefresh} />;
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
