import { useMemo, useState } from 'react';
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table';
import { Download, ArrowUpDown } from 'lucide-react';

export interface DataTableColumn {
  key: string;
  label: string;
}

interface Props<T extends Record<string, unknown>> {
  title?: string;
  columns: DataTableColumn[];
  rows: T[];
  maxHeight?: number;
  exportFilename?: string;
}

function toCsv(columns: DataTableColumn[], rows: Record<string, unknown>[]): string {
  const escape = (v: unknown) => {
    const s = v == null ? '' : String(v);
    if (/[",\n]/.test(s)) return `"${s.replace(/"/g, '""')}"`;
    return s;
  };
  const header = columns.map((c) => escape(c.label)).join(',');
  const body = rows.map((r) => columns.map((c) => escape(r[c.key])).join(',')).join('\n');
  return `${header}\n${body}`;
}

export function downloadCsv(filename: string, columns: DataTableColumn[], rows: Record<string, unknown>[]) {
  const blob = new Blob([toCsv(columns, rows)], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename.endsWith('.csv') ? filename : `${filename}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

export function DataTable<T extends Record<string, unknown>>({
  title,
  columns,
  rows,
  maxHeight = 360,
  exportFilename = 'export.csv',
}: Props<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);

  const colDefs = useMemo<ColumnDef<T>[]>(
    () =>
      columns.map((c) => ({
        accessorKey: c.key,
        header: ({ column }) => (
          <button
            type="button"
            className="inline-flex items-center gap-1 text-left hover:text-white"
            onClick={() => column.toggleSorting(column.getIsSorted() === 'asc')}
          >
            {c.label}
            <ArrowUpDown className="w-3 h-3 opacity-50" />
          </button>
        ),
        cell: (info) => String(info.getValue() ?? ''),
      })),
    [columns],
  );

  const table = useReactTable({
    data: rows,
    columns: colDefs,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
  });

  return (
    <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl my-2 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-700/50">
        <h4 className="text-sm font-semibold text-white flex items-center gap-2">
          {title && <div className="w-0.5 h-3.5 bg-deloitte-green rounded-full" />}
          {title || 'Table'}
        </h4>
        <button
          type="button"
          onClick={() => downloadCsv(exportFilename, columns, rows as Record<string, unknown>[])}
          className="inline-flex items-center gap-1.5 text-xs text-surface-300 hover:text-white px-2 py-1 rounded-md border border-surface-600 hover:border-deloitte-green/40"
          aria-label="Export CSV"
        >
          <Download className="w-3.5 h-3.5" />
          CSV
        </button>
      </div>
      <div className="overflow-auto" style={{ maxHeight }}>
        <table className="w-full text-xs">
          <thead className="sticky top-0 bg-surface-800 z-10">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} className="border-b border-surface-700">
                {hg.headers.map((h) => (
                  <th key={h.id} className="text-left px-3 py-2 text-surface-400 font-medium whitespace-nowrap">
                    {h.isPlaceholder ? null : flexRender(h.column.columnDef.header, h.getContext())}
                  </th>
                ))}
              </tr>
            ))}
          </thead>
          <tbody>
            {table.getRowModel().rows.map((row) => (
              <tr key={row.id} className="border-b border-surface-800/80 hover:bg-surface-700/30">
                {row.getVisibleCells().map((cell) => (
                  <td key={cell.id} className="px-3 py-1.5 text-surface-200 whitespace-nowrap">
                    {flexRender(cell.column.columnDef.cell, cell.getContext())}
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
