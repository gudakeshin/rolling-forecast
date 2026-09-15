import { lazy, memo, Suspense, useEffect, useMemo, useState, type ReactNode } from 'react';
import {
  Loader2, Table, GitCompare, Edit, ClipboardList, Settings2,
  LayoutDashboard, Shield, Target, FileInput, AlertTriangle, FolderOpen,
  CheckSquare, Activity, Sparkles, GitBranch, Brain, Lightbulb,
  Inbox, RefreshCw, MessageSquare, Play,
} from 'lucide-react';
import {
  usePanelStore,
  usePrimaryPanelStore,
  useSecondaryPanelStore,
  useWorkspaceLayoutStore,
  pinPrimaryToSecondary,
  PanelStoreProvider,
  type PanelSlot,
} from '../../store/panelStore';
import { useSavedViewsStore } from '../../store/savedViewsStore';
import { useComposerStore } from '../../store/composerStore';
import { toast } from '../../store/toastStore';
import { apiGet } from '../../api/client';
import { useI18n } from '../../i18n/useI18n';
import type { MessageKey } from '../../i18n';
import { SlidePanel } from '../ui/SlidePanel';

// Each panel is its own chunk — most are opened rarely (admin console, skill
// editor, drivers, ...) and none should be pulled into the initial bundle
// just because PanelContainer references its type. Each is also memoized so
// switching the *other* slot in a side-by-side layout, or an unrelated
// PanelContainerBody re-render, doesn't re-render every mounted panel.
const ForecastTablePanel = lazy(() =>
  import('./ForecastTablePanel').then((m) => ({ default: memo(m.ForecastTablePanel) })),
);
const OverridesPanel = lazy(() => import('./OverridesPanel').then((m) => ({ default: memo(m.OverridesPanel) })));
const ComparisonPanel = lazy(() => import('./ComparisonPanel').then((m) => ({ default: memo(m.ComparisonPanel) })));
const SkillEditorPanel = lazy(() =>
  import('./SkillEditorPanel').then((m) => ({ default: memo(m.SkillEditorPanel) })),
);
const ExecutiveDashboardPanel = lazy(() =>
  import('./ExecutiveDashboardPanel').then((m) => ({ default: memo(m.ExecutiveDashboardPanel) })),
);
const ReviewDashboardPanel = lazy(() =>
  import('./ReviewDashboardPanel').then((m) => ({ default: memo(m.ReviewDashboardPanel) })),
);
const AccuracyTrackingPanel = lazy(() =>
  import('./AccuracyTrackingPanel').then((m) => ({ default: memo(m.AccuracyTrackingPanel) })),
);
const DriverInputPanel = lazy(() =>
  import('./DriverInputPanel').then((m) => ({ default: memo(m.DriverInputPanel) })),
);
const AnomalyPanel = lazy(() => import('./AnomalyPanel').then((m) => ({ default: memo(m.AnomalyPanel) })));
const DocumentLibraryPanel = lazy(() =>
  import('./DocumentLibraryPanel').then((m) => ({ default: memo(m.DocumentLibraryPanel) })),
);
const ApprovalsPanel = lazy(() => import('./ApprovalsPanel').then((m) => ({ default: memo(m.ApprovalsPanel) })));
const DriversPanel = lazy(() => import('./DriversPanel').then((m) => ({ default: memo(m.DriversPanel) })));
const ExplainabilityPanel = lazy(() =>
  import('./ExplainabilityPanel').then((m) => ({ default: memo(m.ExplainabilityPanel) })),
);
const WhatIfPanel = lazy(() => import('./WhatIfPanel').then((m) => ({ default: memo(m.WhatIfPanel) })));
const RunForecastPanel = lazy(() =>
  import('./RunForecastPanel').then((m) => ({ default: memo(m.RunForecastPanel) })),
);
const HeuristicsPanel = lazy(() => import('./HeuristicsPanel').then((m) => ({ default: memo(m.HeuristicsPanel) })));
const AdminConsolePanel = lazy(() =>
  import('./AdminConsolePanel').then((m) => ({ default: memo(m.AdminConsolePanel) })),
);
const WhyThisNumberPanel = lazy(() =>
  import('./WhyThisNumberPanel').then((m) => ({ default: memo(m.WhyThisNumberPanel) })),
);

function PanelChunkFallback() {
  return (
    <div className="flex flex-col items-center justify-center h-32 gap-2">
      <Loader2 className="w-6 h-6 animate-spin text-deloitte-green" />
      <span className="text-xs text-surface-500">Loading data...</span>
    </div>
  );
}

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
  drivers: Activity,
  explainability: Sparkles,
  what_if: GitBranch,
  run_forecast: Play,
  heuristics: Brain,
  anomaly_dashboard: AlertTriangle,
  document_library: FolderOpen,
  approvals: CheckSquare,
  admin_console: Shield,
  why_this_number: Lightbulb,
};

// Panel types that fetch nothing from the server — they own their data.
const STATIC_PANELS: Record<string, { title: string; titleKey?: MessageKey; render: () => ReactNode }> = {
  skill_editor: {
    title: 'Skill Editor',
    render: () => (
      <div className="-m-4 h-[calc(100%+2rem)] overflow-hidden">
        <SkillEditorPanel />
      </div>
    ),
  },
  approvals: { title: 'Approvals', render: () => <ApprovalsPanel /> },
  drivers: { title: 'Drivers', titleKey: 'panel.drivers', render: () => <DriversPanel /> },
  explainability: {
    title: 'Explainability',
    titleKey: 'panel.explainability',
    render: () => <ExplainabilityPanel />,
  },
  what_if: { title: 'What If', titleKey: 'panel.whatIf', render: () => <WhatIfPanel /> },
  run_forecast: {
    title: 'Run Forecast',
    titleKey: 'panel.runForecast',
    render: () => <RunForecastPanel />,
  },
  heuristics: { title: 'Heuristics', titleKey: 'panel.heuristics', render: () => <HeuristicsPanel /> },
  admin_console: { title: 'Admin Console', render: () => <AdminConsolePanel /> },
};

/**
 * Renders one panel slot. `variant="secondary"` wraps its subtree in a
 * PanelStoreProvider so nested panels that read `usePanelStore` directly
 * (rather than via props) transparently see the secondary slot's state —
 * see store/panelStore.ts for why that's safe for every existing panel.
 */
export function PanelContainer({ variant = 'primary' }: { variant?: PanelSlot }) {
  const store = variant === 'secondary' ? useSecondaryPanelStore : usePrimaryPanelStore;
  return (
    <PanelStoreProvider store={store}>
      <PanelContainerBody variant={variant} />
    </PanelStoreProvider>
  );
}

function PanelContainerBody({ variant }: { variant: PanelSlot }) {
  const { t } = useI18n();
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
  const maximizedSlot = useWorkspaceLayoutStore((s) => s.maximizedSlot);
  const toggleMaximize = useWorkspaceLayoutStore((s) => s.toggleMaximize);
  // The raw instance for this slot — needed for the one imperative getState()
  // read below; `usePanelStore` above is context-resolved for everything else.
  const rawStore = variant === 'secondary' ? useSecondaryPanelStore : usePrimaryPanelStore;
  const saveView = useSavedViewsStore((s) => s.saveView);
  const requestComposerFocus = useComposerStore((s) => s.requestFocus);

  const handleSaveView = () => {
    if (!panelType) return;
    const name = window.prompt('Name this view:');
    if (!name || !name.trim()) return;
    saveView(name.trim(), panelType, panelParams);
    toast.success(`Saved view "${name.trim()}"`);
  };

  // Only these fields actually change what gets fetched. Depending on the
  // whole panelParams object would refetch on every cosmetic param change
  // too (e.g. exog_mode/sort_by, added so those filters survive a saved
  // view/URL — see ForecastTablePanel) even though nothing server-side moved.
  const fetchKey = useMemo(
    () =>
      JSON.stringify({
        version_id: panelParams?.version_id,
        version_id_a: panelParams?.version_id_a,
        version_id_b: panelParams?.version_id_b,
        view: panelParams?.view,
        category: panelParams?.category,
        confidence_level: panelParams?.confidence_level,
        offset: panelParams?.offset,
        limit: panelParams?.limit,
        conversation_id: panelParams?.conversation_id,
        result_id: panelParams?.result_id,
        _append: panelParams?._append,
        _refresh: panelParams?._refresh,
      }),
    [panelParams],
  );

  useEffect(() => {
    if (!panelType || !panelParams) return;

    const staticPanel = STATIC_PANELS[panelType];
    if (staticPanel) {
      setPanelData({
        panel_type: panelType,
        title: staticPanel.titleKey ? t(staticPanel.titleKey) : staticPanel.title,
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
          case 'why_this_number':
            url = `/panel/why-this-number/${panelParams.result_id}`;
            break;
          default:
            setLoading(false);
            return;
        }
        const data = await apiGet<any>(url, { signal: controller.signal });
        if (append && panelType === 'forecast_table') {
          const current = rawStore.getState().panelData;
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
    // `t` is intentionally omitted: useI18n() returns a new function identity
    // every render, so including it here re-runs the fetch (and setPanelData)
    // on every render — an infinite update loop. Locale-driven titles inside
    // this effect only need the current translation at fetch time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [panelType, fetchKey, setLoading, setPanelData, rawStore]);

  const staticPanel = panelType ? STATIC_PANELS[panelType] : undefined;
  const Icon = panelIcons[panelType || ''] || Table;
  const title = staticPanel
    ? (staticPanel.titleKey ? t(staticPanel.titleKey) : staticPanel.title)
    : panelData?.title || panelType?.replace(/_/g, ' ') || 'Details';

  const content = staticPanel ? (
    staticPanel.render()
  ) : isLoading ? (
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
      <button
        type="button"
        onClick={refreshPanel}
        className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-surface-300 hover:text-white bg-surface-800 hover:bg-surface-700 border border-surface-700 rounded-lg transition-colors"
      >
        <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" />
        Retry
      </button>
    </div>
  ) : panelData ? (
    <PanelContent type={panelType} data={panelData} onRefresh={refreshPanel} />
  ) : (
    <div className="flex flex-col items-center justify-center h-40 gap-3 text-center px-4">
      <Inbox className="w-6 h-6 text-surface-500" aria-hidden="true" />
      <p className="text-sm text-surface-400">No data available yet</p>
      <div className="flex items-center gap-2">
        <button
          type="button"
          onClick={refreshPanel}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-surface-300 hover:text-white bg-surface-800 hover:bg-surface-700 border border-surface-700 rounded-lg transition-colors"
        >
          <RefreshCw className="w-3.5 h-3.5" aria-hidden="true" />
          Refresh
        </button>
        <button
          type="button"
          onClick={() => {
            closePanel();
            requestComposerFocus();
          }}
          className="inline-flex items-center gap-1.5 px-3 py-1.5 text-xs font-medium text-deloitte-green hover:text-white bg-deloitte-green/10 hover:bg-deloitte-green/20 border border-deloitte-green/20 rounded-lg transition-colors"
        >
          <MessageSquare className="w-3.5 h-3.5" aria-hidden="true" />
          Ask in chat
        </button>
      </div>
    </div>
  );

  return (
    <SlidePanel
      title={title}
      icon={<Icon className="w-4 h-4 text-deloitte-green" aria-hidden="true" />}
      onClose={closePanel}
      widthExpanded={widthMode === 'wide'}
      onToggleWidth={toggleWidth}
      onPin={variant === 'primary' ? pinPrimaryToSecondary : undefined}
      onToggleMaximize={() => toggleMaximize(variant)}
      isMaximized={maximizedSlot === variant}
      onSaveView={!staticPanel ? handleSaveView : undefined}
    >
      <Suspense fallback={<PanelChunkFallback />}>{content}</Suspense>
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
    case 'why_this_number':
      return <WhyThisNumberPanel data={data} />;
    default:
      return (
        <pre className="text-xs text-surface-400 whitespace-pre-wrap">
          {JSON.stringify(data, null, 2)}
        </pre>
      );
  }
}
