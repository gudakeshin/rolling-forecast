import { Fragment } from 'react';
import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';
import type { CitationItem } from './CitationsRenderer';
import { usePanelStore } from '../../../store/panelStore';

interface Props {
  text: string;
  citations?: CitationItem[];
}

export function TextRenderer({ text, citations }: Props) {
  const openPanel = usePanelStore((s) => s.openPanel);

  if (!text) return null;

  // Split prose on [n] markers so citations become inline superscripts
  const parts = citations?.length
    ? String(text).split(/(\[\d+\])/g)
    : [String(text)];

  return (
    <div className="text-surface-200 leading-relaxed prose prose-invert prose-sm max-w-none prose-p:my-1 prose-ul:my-1 prose-li:my-0.5 prose-strong:text-white prose-code:text-deloitte-green prose-code:bg-deloitte-green/10 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-xs">
      {parts.map((part, i) => {
        const m = part.match(/^\[(\d+)\]$/);
        if (m && citations?.length) {
          const idx = Number(m[1]) - 1;
          const cite = citations[idx];
          if (!cite) {
            return <Fragment key={i}>{part}</Fragment>;
          }
          return (
            <sup key={i}>
              <button
                type="button"
                className="text-deloitte-green font-semibold hover:underline px-0.5"
                title={cite.snippet || cite.label}
                aria-label={`Citation ${m[1]}: ${cite.label}`}
                onClick={() => {
                  if (cite.document_id) {
                    openPanel('document_library', {
                      document_id: cite.document_id,
                      highlight: cite.snippet,
                    });
                  }
                }}
              >
                [{m[1]}]
              </button>
            </sup>
          );
        }
        return (
          <ReactMarkdown key={i} remarkPlugins={[remarkGfm]}>
            {part}
          </ReactMarkdown>
        );
      })}
    </div>
  );
}
