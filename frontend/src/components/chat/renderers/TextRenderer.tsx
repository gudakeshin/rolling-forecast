import ReactMarkdown from 'react-markdown';
import remarkGfm from 'remark-gfm';

interface Props {
  text: string;
}

export function TextRenderer({ text }: Props) {
  if (!text) return null;

  return (
    <div className="text-surface-200 leading-relaxed prose prose-invert prose-sm max-w-none prose-p:my-1 prose-ul:my-1 prose-li:my-0.5 prose-strong:text-white prose-code:text-deloitte-green prose-code:bg-deloitte-green/10 prose-code:px-1.5 prose-code:py-0.5 prose-code:rounded prose-code:text-xs">
      <ReactMarkdown remarkPlugins={[remarkGfm]}>{String(text)}</ReactMarkdown>
    </div>
  );
}
