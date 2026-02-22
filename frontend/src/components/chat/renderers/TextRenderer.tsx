interface Props {
  text: string;
}

export function TextRenderer({ text }: Props) {
  // Guard against null/undefined text
  if (!text) return null;

  // Parse text into lines for multi-line support
  const lines = String(text).split('\n');

  return (
    <div className="text-surface-200 leading-relaxed space-y-1">
      {lines.map((line, lineIdx) => {
        // Empty line = paragraph break
        if (line.trim() === '') {
          return <div key={lineIdx} className="h-2" />;
        }

        // Bullet points
        if (line.trim().startsWith('- ')) {
          return (
            <div key={lineIdx} className="flex gap-2 pl-2">
              <span className="text-deloitte-green mt-1.5 text-xs">•</span>
              <span>{renderInline(line.trim().slice(2))}</span>
            </div>
          );
        }

        return <p key={lineIdx}>{renderInline(line)}</p>;
      })}
    </div>
  );
}

function renderInline(text: string): JSX.Element[] {
  // Parse bold (**text**), italic (_text_), and code (`text`)
  const parts = text.split(/(\*\*.*?\*\*|_.*?_|`.*?`)/g);

  return parts.map((part, i) => {
    if (part.startsWith('**') && part.endsWith('**')) {
      return (
        <strong key={i} className="text-white font-semibold">
          {part.slice(2, -2)}
        </strong>
      );
    }
    if (part.startsWith('_') && part.endsWith('_') && part.length > 2) {
      return (
        <em key={i} className="text-surface-300 italic">
          {part.slice(1, -1)}
        </em>
      );
    }
    if (part.startsWith('`') && part.endsWith('`')) {
      return (
        <code
          key={i}
          className="px-1.5 py-0.5 bg-deloitte-green/10 text-deloitte-green rounded text-xs font-mono"
        >
          {part.slice(1, -1)}
        </code>
      );
    }
    return <span key={i}>{part}</span>;
  });
}
