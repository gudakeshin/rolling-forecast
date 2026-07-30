import { Fragment, useMemo, useState, useRef, type ReactNode } from 'react';
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table';
import { useVirtualizer } from '@tanstack/react-virtual';
import { Download, FileSpreadsheet, ArrowUpDown } from 'lucide-react';
import { t } from '../../i18n';

export interface DataTableColumn<T extends Record<string, unknown> = Record<string, unknown>> {
  key: string;
  label: string;
  align?: 'left' | 'right' | 'center';
  render?: (value: unknown, row: T) => ReactNode;
}

interface Props<T extends Record<string, unknown>> {
  title?: string;
  columns: DataTableColumn<T>[];
  rows: T[];
  maxHeight?: number;
  exportFilename?: string;
  rowHeight?: number;
  rowActions?: (row: T) => ReactNode;
  onRowClick?: (row: T) => void;
  getRowClassName?: (row: T) => string;
  getRowId?: (row: T) => string;
  expandedRowIds?: Set<string>;
  renderExpandedRow?: (row: T) => ReactNode;
  hideExport?: boolean;
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

export function downloadCsv(
  filename: string,
  columns: DataTableColumn<Record<string, unknown>>[],
  rows: Record<string, unknown>[],
) {
  const blob = new Blob([toCsv(columns, rows)], { type: 'text/csv;charset=utf-8' });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename.endsWith('.csv') ? filename : `${filename}.csv`;
  a.click();
  URL.revokeObjectURL(url);
}

async function downloadXlsx(
  filename: string,
  columns: DataTableColumn<Record<string, unknown>>[],
  rows: Record<string, unknown>[],
) {
  const ExcelJS = (await import('exceljs')).default;
  const wb = new ExcelJS.Workbook();
  const ws = wb.addWorksheet('Data');
  ws.addRow(columns.map((c) => c.label));
  for (const row of rows) {
    ws.addRow(columns.map((c) => {
      const v = row[c.key];
      return v == null ? '' : (v as string | number | boolean | Date);
    }));
  }
  ws.getRow(1).font = { bold: true };
  const buffer = await wb.xlsx.writeBuffer();
  const blob = new Blob([buffer], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = filename.endsWith('.xlsx') ? filename : `${filename.replace(/\.csv$/i, '')}.xlsx`;
  a.click();
  URL.revokeObjectURL(url);
}

const VIRTUALIZE_THRESHOLD = 40;

const alignClass = (align?: 'left' | 'right' | 'center') =>
  align === 'right' ? 'text-right' : align === 'center' ? 'text-center' : 'text-left';

export function DataTable<T extends Record<string, unknown>>({
  title,
  columns,
  rows,
  maxHeight = 360,
  exportFilename = 'export.csv',
  rowHeight = 32,
  rowActions,
  onRowClick,
  getRowClassName,
  getRowId,
  expandedRowIds,
  renderExpandedRow,
  hideExport = false,
}: Props<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [exportingXlsx, setExportingXlsx] = useState(false);
  const parentRef = useRef<HTMLDivElement>(null);

  const colDefs = useMemo<ColumnDef<T>[]>(
    () => {
      const defs: ColumnDef<T>[] = columns.map((c) => ({
        accessorKey: c.key,
        header: ({ column }) => (
          <button
            type="button"
            className={`inline-flex items-center gap-1 hover:text-white ${alignClass(c.align)}`}
            onClick={() => column.toggleSorting(column.getIsSorted() === 'asc')}
          >
            {c.label}
            <ArrowUpDown className="w-3 h-3 opacity-50" />
          </button>
        ),
        cell: (info) => {
          const row = info.row.original;
          const value = info.getValue();
          const content = c.render ? c.render(value, row) : String(value ?? '');
          return <div className={alignClass(c.align)}>{content}</div>;
        },
      }));
      if (rowActions) {
        defs.push({
          id: '_actions',
          header: () => <span className="sr-only">Actions</span>,
          cell: (info) => (
            <div className="flex items-center justify-end gap-1 opacity-0 group-hover:opacity-100 group-focus-within:opacity-100 transition-opacity">
              {rowActions(info.row.original)}
            </div>
          ),
        });
      }
      return defs;
    },
    [columns, rowActions],
  );

  const table = useReactTable({
    data: rows,
    columns: colDefs,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId: getRowId ? (row) => getRowId(row) : undefined,
  });

  const tableRows = table.getRowModel().rows;
  // Expanded detail rows break virtual padding math — disable when used.
  const useVirtual = !renderExpandedRow && tableRows.length > VIRTUALIZE_THRESHOLD;
  const colCount = columns.length + (rowActions ? 1 : 0);

  const virtualizer = useVirtualizer({
    count: useVirtual ? tableRows.length : 0,
    getScrollElement: () => parentRef.current,
    estimateSize: () => rowHeight,
    overscan: 8,
  });

  const virtualRows = useVirtual ? virtualizer.getVirtualItems() : [];
  const paddingTop = virtualRows.length > 0 ? virtualRows[0].start : 0;
  const paddingBottom =
    virtualRows.length > 0
      ? virtualizer.getTotalSize() - virtualRows[virtualRows.length - 1].end
      : 0;

  const exportCols = columns as unknown as DataTableColumn<Record<string, unknown>>[];
  const exportRows = rows as unknown as Record<string, unknown>[];

  const onXlsx = async () => {
    setExportingXlsx(true);
    try {
      await downloadXlsx(exportFilename, exportCols, exportRows);
    } finally {
      setExportingXlsx(false);
    }
  };

  const renderRow = (row: (typeof tableRows)[number]) => {
    const original = row.original;
    const extra = getRowClassName?.(original) || '';
    const rowKey = getRowId ? getRowId(original) : row.id;
    const isExpanded = Boolean(renderExpandedRow && expandedRowIds?.has(rowKey));
    return (
      <Fragment key={rowKey}>
        <tr
          className={`group border-b border-surface-800/80 hover:bg-surface-700/30 ${onRowClick ? 'cursor-pointer' : ''} ${extra}`}
          style={{ height: rowHeight }}
          onClick={onRowClick ? () => onRowClick(original) : undefined}
          tabIndex={onRowClick ? 0 : undefined}
          onKeyDown={
            onRowClick
              ? (e) => {
                  if (e.key === 'Enter' || e.key === ' ') {
                    e.preventDefault();
                    onRowClick(original);
                  }
                }
              : undefined
          }
        >
          {row.getVisibleCells().map((cell) => (
            <td key={cell.id} className="px-3 py-1.5 text-surface-200 whitespace-nowrap">
              {flexRender(cell.column.columnDef.cell, cell.getContext())}
            </td>
          ))}
        </tr>
        {isExpanded && (
          <tr key={`${row.id}-expanded`} className="border-b border-surface-700/10">
            <td colSpan={colCount} className="px-4 py-3 bg-surface-850/50 whitespace-normal">
              {renderExpandedRow!(original)}
            </td>
          </tr>
        )}
      </Fragment>
    );
  };

  return (
    <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl my-2 overflow-hidden">
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-700/50 gap-2">
        <h4 className="text-sm font-semibold text-white flex items-center gap-2 min-w-0">
          {title && <div className="w-0.5 h-3.5 bg-deloitte-green rounded-full shrink-0" />}
          <span className="truncate">{title || t('table.title')}</span>
        </h4>
        {!hideExport && (
          <div className="flex items-center gap-1.5 shrink-0">
            <button
              type="button"
              onClick={() => downloadCsv(exportFilename, exportCols, exportRows)}
              className="inline-flex items-center gap-1.5 text-xs text-surface-300 hover:text-white px-2 py-1 rounded-md border border-surface-600 hover:border-deloitte-green/40"
              aria-label={t('table.exportCsv')}
            >
              <Download className="w-3.5 h-3.5" />
              CSV
            </button>
            <button
              type="button"
              onClick={onXlsx}
              disabled={exportingXlsx}
              className="inline-flex items-center gap-1.5 text-xs text-surface-300 hover:text-white px-2 py-1 rounded-md border border-surface-600 hover:border-deloitte-green/40 disabled:opacity-50"
              aria-label={t('table.exportXlsx')}
            >
              <FileSpreadsheet className="w-3.5 h-3.5" />
              {exportingXlsx ? '…' : 'XLSX'}
            </button>
          </div>
        )}
      </div>
      <div ref={parentRef} className="overflow-auto" style={{ maxHeight }}>
        <table className="w-full text-xs" style={{ fontSize: 12 }}>
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
            {useVirtual ? (
              <>
                {paddingTop > 0 && (
                  <tr>
                    <td colSpan={colCount} style={{ height: paddingTop, padding: 0, border: 0 }} />
                  </tr>
                )}
                {virtualRows.map((vRow) => renderRow(tableRows[vRow.index]))}
                {paddingBottom > 0 && (
                  <tr>
                    <td colSpan={colCount} style={{ height: paddingBottom, padding: 0, border: 0 }} />
                  </tr>
                )}
              </>
            ) : (
              tableRows.map((row) => renderRow(row))
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
