import {
  Table,
  Edit,
  GitCompare,
  Shield,
  CheckSquare,
  AlertTriangle,
  Target,
  Sparkles,
  Brain,
  GitBranch,
  FileInput,
  Activity,
  FolderOpen,
  Settings2,
  Play,
} from 'lucide-react';
import { useAuthStore, useCan } from '../store/authStore';
import { usePanelStore } from '../store/panelStore';
import { useVersionStore } from '../store/versionStore';
import type { MessageKey } from '../i18n';

export interface NavItem {
  key: string;
  labelKey: MessageKey;
  icon: typeof Table;
  show: boolean;
  onClick: () => void;
}

export interface NavGroup {
  titleKey: MessageKey;
  items: NavItem[];
}

/**
 * The app's panel navigation, permission-gated. Single source of truth for
 * both the sidebar and the ⌘K command palette — duplicating this list would
 * let the two silently drift on who can see what.
 */
export function useNavItems(): NavGroup[] {
  const user = useAuthStore((s) => s.user);
  const openPanel = usePanelStore((s) => s.openPanel);
  const versions = useVersionStore((s) => s.versions);
  const activeVersionId = useVersionStore((s) => s.activeVersionId);
  const activeScenario = useVersionStore((s) => s.activeScenario);

  const canGenerate = useCan('can_generate');
  const canOverride = useCan('can_override');
  const canReview = useCan('can_review');
  const canInput = useCan('can_input');
  const canPublish = useCan('can_publish');
  const canAdmin = useCan('can_admin');
  const canManageDrivers = useCan('can_manage_drivers');
  const isAdminRole = canAdmin || user?.role_name === 'admin';

  const scenarioVersions = versions.filter((v) => (v.scenario || 'base') === activeScenario);
  const compareTargetId = scenarioVersions.find((v) => v.id !== activeVersionId)?.id ?? null;

  const open = (panel: string, params: Record<string, any> = {}) => () => openPanel(panel, params);

  return [
    {
      titleKey: 'nav.group.forecast',
      items: [
        {
          key: 'run_forecast',
          labelKey: 'nav.runForecast',
          icon: Play,
          show: canGenerate,
          onClick: open('run_forecast'),
        },
        {
          key: 'forecast_table',
          labelKey: 'nav.forecast',
          icon: Table,
          show: canGenerate || canReview,
          onClick: open('forecast_table'),
        },
        {
          key: 'overrides',
          labelKey: 'nav.overrides',
          icon: Edit,
          show: canOverride,
          onClick: open('overrides'),
        },
        {
          key: 'comparison',
          labelKey: 'nav.comparison',
          icon: GitCompare,
          show: (canGenerate || canReview) && Boolean(activeVersionId && compareTargetId),
          onClick: open('comparison', {
            version_id_a: activeVersionId,
            version_id_b: compareTargetId,
          }),
        },
      ],
    },
    {
      titleKey: 'nav.group.review',
      items: [
        {
          key: 'review_dashboard',
          labelKey: 'nav.review',
          icon: Shield,
          show: canReview,
          onClick: open('review_dashboard'),
        },
        {
          key: 'approvals',
          labelKey: 'nav.approvals',
          icon: CheckSquare,
          show: canReview || canPublish,
          onClick: open('approvals'),
        },
        {
          key: 'anomaly_dashboard',
          labelKey: 'nav.anomalies',
          icon: AlertTriangle,
          show: canReview,
          onClick: open('anomaly_dashboard'),
        },
      ],
    },
    {
      titleKey: 'nav.group.intelligence',
      items: [
        {
          key: 'accuracy_tracking',
          labelKey: 'nav.accuracy',
          icon: Target,
          show: canReview || canGenerate,
          onClick: open('accuracy_tracking'),
        },
        {
          key: 'explainability',
          labelKey: 'nav.explain',
          icon: Sparkles,
          show: canReview || canGenerate,
          onClick: open('explainability'),
        },
        {
          key: 'heuristics',
          labelKey: 'nav.heuristics',
          icon: Brain,
          show: canReview || canAdmin,
          onClick: open('heuristics'),
        },
        {
          key: 'what_if',
          labelKey: 'nav.whatIf',
          icon: GitBranch,
          show: canGenerate,
          onClick: open('what_if'),
        },
      ],
    },
    {
      titleKey: 'nav.group.inputs',
      items: [
        {
          key: 'driver_inputs',
          labelKey: 'nav.drivers',
          icon: FileInput,
          show: canInput,
          onClick: open('driver_inputs'),
        },
        {
          key: 'drivers',
          labelKey: 'nav.causalDrivers',
          icon: Activity,
          show: canManageDrivers || isAdminRole,
          onClick: open('drivers'),
        },
      ],
    },
    {
      titleKey: 'nav.group.library',
      items: [
        {
          key: 'document_library',
          labelKey: 'nav.documents',
          icon: FolderOpen,
          show: true,
          onClick: open('document_library'),
        },
      ],
    },
    {
      titleKey: 'nav.group.admin',
      items: [
        {
          key: 'skill_editor',
          labelKey: 'nav.skills',
          icon: Settings2,
          show: isAdminRole,
          onClick: open('skill_editor'),
        },
        {
          key: 'admin_console',
          labelKey: 'nav.admin',
          icon: Shield,
          show: isAdminRole,
          onClick: open('admin_console'),
        },
      ],
    },
  ];
}
