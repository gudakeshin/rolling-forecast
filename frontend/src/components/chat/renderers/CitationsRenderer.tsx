import { usePanelStore } from '../../../store/panelStore';

export interface CitationItem {
  id?: string | number;
  label: string;
  document_id?: string;
  snippet?: string;
  score?: number;
}

interface Props {
  data: {
    citations: CitationItem[];
  };
}

export function CitationsRenderer({ data }: Props) {
  const openPanel = usePanelStore((s) => s.openPanel);
  const citations = data.citations || [];

  if (!citations.length) return null;

  return (
    <div className="my-2 rounded-xl border border-surface-700/50 bg-surface-800/40 px-3 py-2">
      <p className="text-[11px] uppercase tracking-wide text-surface-500 mb-1.5">Sources</p>
      <ol className="space-y-1.5 list-none">
        {citations.map((c, i) => (
          <li key={String(c.id ?? i)} className="flex gap-2 text-xs">
            <sup className="text-deloitte-green font-semibold mt-0.5 shrink-0">[{i + 1}]</sup>
            <button
              type="button"
              className="text-left text-surface-300 hover:text-white underline-offset-2 hover:underline"
              onClick={() => {
                if (c.document_id) {
                  openPanel('document_library', { document_id: c.document_id, highlight: c.snippet });
                }
              }}
              title={c.snippet || c.label}
            >
              <span className="font-medium">{c.label}</span>
              {c.snippet && (
                <span className="block text-surface-500 line-clamp-2 mt-0.5 no-underline">
                  {c.snippet}
                </span>
              )}
            </button>
          </li>
        ))}
      </ol>
    </div>
  );
}
