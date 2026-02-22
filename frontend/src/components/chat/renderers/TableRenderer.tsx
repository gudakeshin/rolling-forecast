interface Column {
  key: string;
  label: string;
}

interface Props {
  data: {
    title: string;
    columns: Column[];
    rows: Record<string, string>[];
  };
}

export function TableRenderer({ data }: Props) {
  const { title, columns, rows } = data;
  if (!rows || rows.length === 0) return null;

  return (
    <div className="bg-surface-800/80 border border-surface-700 rounded-xl overflow-hidden">
      {title && (
        <div className="px-4 py-2.5 border-b border-surface-700 flex items-center gap-2">
          <div className="w-0.5 h-4 bg-deloitte-green rounded-full" />
          <h4 className="text-sm font-semibold text-surface-200">{title}</h4>
        </div>
      )}
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-surface-700">
              {columns.map((col) => (
                <th
                  key={col.key}
                  className="px-4 py-2 text-left text-xs font-semibold text-surface-400 uppercase tracking-wider"
                >
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr
                key={i}
                className="border-b border-surface-700/30 last:border-0 hover:bg-deloitte-green/5 transition-colors"
              >
                {columns.map((col) => (
                  <td key={col.key} className="px-4 py-2 text-surface-300">
                    <CellValue value={row[col.key]} />
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function CellValue({ value }: { value: any }) {
  if (value === null || value === undefined || value === '') {
    return <span className="text-surface-600">-</span>;
  }

  // Coerce to string — backend may send numbers
  const str = String(value);

  // Color-code percentages (Deloitte green for positive, red for negative)
  if (str.startsWith('+')) {
    return <span className="text-deloitte-green font-medium">{str}</span>;
  }
  if (str.startsWith('-') && str.includes('%')) {
    return <span className="text-red-400 font-medium">{str}</span>;
  }

  // Color-code confidence levels
  if (str === 'high' || str === 'High (70+)') {
    return (
      <span className="inline-flex items-center gap-1">
        <span className="w-1.5 h-1.5 rounded-full bg-deloitte-green" />
        <span className="text-deloitte-green">{str}</span>
      </span>
    );
  }
  if (str === 'medium' || str === 'Medium (50-70)') {
    return (
      <span className="inline-flex items-center gap-1">
        <span className="w-1.5 h-1.5 rounded-full bg-yellow-400" />
        <span className="text-yellow-400">{str}</span>
      </span>
    );
  }
  if (str === 'low' || str === 'Low (<50)') {
    return (
      <span className="inline-flex items-center gap-1">
        <span className="w-1.5 h-1.5 rounded-full bg-red-400" />
        <span className="text-red-400">{str}</span>
      </span>
    );
  }

  // MAPE scores with star indicator (model comparison tables)
  if (str.endsWith('★')) {
    return <span className="text-deloitte-green font-semibold">{str}</span>;
  }

  // Auto-approvable
  if (str === 'Auto-approvable') {
    return <span className="text-deloitte-green text-xs">{str}</span>;
  }
  if (str.includes('required') || str.includes('recommended') || str.includes('Recommended')) {
    return <span className="text-yellow-400 text-xs">{str}</span>;
  }

  // N/A or error values
  if (str === 'N/A' || str.startsWith('N/A')) {
    return <span className="text-surface-500 text-xs">{str}</span>;
  }

  return <span>{str}</span>;
}
