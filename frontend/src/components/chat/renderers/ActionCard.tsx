import { apiPost } from '../../../api/client';
import { usePanelStore } from '../../../store/panelStore';

interface Props {
  data: {
    actions: { id: string; label: string; variant?: string; href?: string; panel?: string; version_id?: string }[];
  };
}

export function ActionCard({ data }: Props) {
  const { actions } = data;
  const openPanel = usePanelStore((s) => s.openPanel);

  const handleAction = async (action: Props['data']['actions'][number]) => {
    const id = action.id.toLowerCase();

    if (action.panel || id.startsWith('open_') || id.includes('panel') || id.includes('drill')) {
      const panelType = action.panel || id.replace(/^open_/, '');
      openPanel(panelType, action.version_id ? { versionId: action.version_id } : {});
      return;
    }

    if (id.includes('approve') && action.version_id) {
      try {
        await apiPost('/approvals/submit', { version_id: action.version_id });
      } catch (e) {
        console.error('Approve action failed', e);
      }
      return;
    }

    if (action.href) {
      window.open(action.href, '_blank', 'noopener,noreferrer');
    }
  };

  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label="Suggested actions">
      {actions.map((action) => (
        <button
          key={action.id}
          type="button"
          onClick={() => handleAction(action)}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-all min-h-[44px] ${
            action.variant === 'primary'
              ? 'bg-deloitte-green hover:bg-deloitte-green/90 text-black'
              : action.variant === 'danger'
              ? 'bg-red-500/15 hover:bg-red-500/25 text-red-400 border border-red-500/30'
              : 'bg-surface-700 hover:bg-surface-600 text-surface-300 border border-surface-600'
          }`}
        >
          {action.label}
        </button>
      ))}
    </div>
  );
}
