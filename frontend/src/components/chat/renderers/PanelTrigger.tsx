import { ExternalLink } from 'lucide-react';
import { usePanelStore } from '../../../store/panelStore';

interface Props {
  data: {
    panel: string;
    params: Record<string, any>;
    label: string;
  };
}

export function PanelTrigger({ data }: Props) {
  const openPanel = usePanelStore((s) => s.openPanel);

  return (
    <button
      onClick={() => openPanel(data.panel, data.params)}
      className="inline-flex items-center gap-2 px-4 py-2 bg-deloitte-green/10 hover:bg-deloitte-green/20 border border-deloitte-green/25 rounded-lg text-deloitte-green text-sm font-medium transition-all hover:scale-[1.02]"
    >
      <ExternalLink className="w-4 h-4" />
      {data.label}
    </button>
  );
}
