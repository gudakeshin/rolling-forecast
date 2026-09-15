import { DataTable } from '../../ui/DataTable';

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
    <DataTable
      title={title}
      columns={columns}
      rows={rows}
      exportFilename={`${(title || 'table').replace(/\s+/g, '_').toLowerCase()}.csv`}
    />
  );
}
