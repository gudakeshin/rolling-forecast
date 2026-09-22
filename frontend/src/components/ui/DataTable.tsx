import {
  Fragment,
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
} from 'react';
import {
  flexRender,
  getCoreRowModel,
  getSortedRowModel,
  useReactTable,
  type ColumnDef,
  type SortingState,
} from '@tanstack/react-table';
import { useVirtualizer } from '@tanstack/react-virtual';
import { Download, FileSpreadsheet, ArrowUpDown, Search, Copy, X } from 'lucide-react';
import { t } from '../../i18n';

export interface DataTableColumn<T extends Record<string, unknown> = Record<string, unknown>> {
  key: string;
  label: string;
  align?: 'left' | 'right' | 'center';
  render?: (value: unknown, row: T) => ReactNode;
  /** Sticky to the left edge while the row scrolls horizontally. */
  pinned?: boolean;
  /** Rendered width in px. Required on pinned columns to compute sticky offsets. */
  width?: number;
  /** Show a sum of this column in the footer. */
  total?: boolean;
  /** Override how this cell is serialized for CSV/XLSX/clipboard. */
  exportValue?: (value: unknown, row: T) => string | number | null;
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
  /** Free-text filter across all column values. */
  searchable?: boolean;
  searchPlaceholder?: string;
  /** Checkbox column + a bulk action bar rendered from the selected rows. */
  selectable?: boolean;
  renderBulkActions?: (selected: T[], clear: () => void) => ReactNode;
  /** Footer row summing every column marked `total`. */
  showTotals?: boolean;
}

const DEFAULT_PINNED_WIDTH = 200;

function cellText(col: DataTableColumn, row: Record<string, unknown>): string {
  const raw = row[col.key];
  const value = col.exportValue ? col.exportValue(raw, row) : raw;
  return value == null ? '' : String(value);
}

function toCsv(columns: DataTableColumn[], rows: Record<string, unknown>[]): string {
  const escape = (s: string) => (/[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s);
  const header = columns.map((c) => escape(c.label)).join(',');
  const body = rows
    .map((r) => columns.map((c) => escape(cellText(c, r))).join(','))
    .join('\n');
  return `${header}\n${body}`;
}

/** Tab-separated, which is what Excel and Sheets accept from the clipboard. */
function toTsv(columns: DataTableColumn[], rows: Record<string, unknown>[]): string {
  const clean = (s: string) => s.replace(/[\t\r\n]+/g, ' ');
  const header = columns.map((c) => clean(c.label)).join('\t');
  const body = rows.map((r) => columns.map((c) => clean(cellText(c, r))).join('\t')).join('\n');
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
      const raw = row[c.key];
      const v = c.exportValue ? c.exportValue(raw, row) : raw;
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
  searchable = false,
  searchPlaceholder,
  selectable = false,
  renderBulkActions,
  showTotals = false,
}: Props<T>) {
  const [sorting, setSorting] = useState<SortingState>([]);
  const [exportingXlsx, setExportingXlsx] = useState(false);
  const [query, setQuery] = useState('');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());
  const [copied, setCopied] = useState(false);
  const parentRef = useRef<HTMLDivElement>(null);

  const rowKey = useCallback(
    (row: T, index: number) => (getRowId ? getRowId(row) : String(index)),
    [getRowId],
  );

  // Free-text filter runs over the same text the export produces, so what the
  // analyst searched is what they get when they hit CSV.
  const filteredRows = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return rows;
    return rows.filter((row) =>
      columns.some((c) =>
        cellText(c as DataTableColumn, row as Record<string, unknown>)
          .toLowerCase()
          .includes(q),
      ),
    );
  }, [rows, columns, query]);

  // Drop selections whose rows are no longer present (filtered out, or the
  // panel reloaded) so a bulk action can never hit a row nobody can see.
  useEffect(() => {
    if (!selectable) return;
    setSelectedIds((prev) => {
      if (prev.size === 0) return prev;
      const visible = new Set(filteredRows.map((r, i) => rowKey(r, i)));
      const next = new Set([...prev].filter((id) => visible.has(id)));
      return next.size === prev.size ? prev : next;
    });
  }, [filteredRows, selectable, rowKey]);

  const pinnedOffsets = useMemo(() => {
    const offsets = new Map<string, number>();
    let running = selectable ? 36 : 0;
    for (const c of columns) {
      if (!c.pinned) continue;
      offsets.set(c.key, running);
      running += c.width ?? DEFAULT_PINNED_WIDTH;
    }
    return offsets;
  }, [columns, selectable]);

  const lastPinnedKey = useMemo(() => {
    const pinned = columns.filter((c) => c.pinned);
    return pinned.length ? pinned[pinned.length - 1].key : null;
  }, [columns]);

  const pinnedStyle = useCallback(
    (col: DataTableColumn<T>, isHeader: boolean): React.CSSProperties | undefined => {
      if (!col.pinned) return col.width ? { width: col.width } : undefined;
      return {
        position: 'sticky',
        left: pinnedOffsets.get(col.key) ?? 0,
        width: col.width ?? DEFAULT_PINNED_WIDTH,
        // Header cells are already sticky-top at z-10; pinned headers must win
        // against both scroll axes.
        zIndex: isHeader ? 20 : 5,
      };
    },
    [pinnedOffsets],
  );

  const pinnedClass = (col: DataTableColumn<T>, base: string) =>
    col.pinned
      ? `${base} bg-surface-800 ${col.key === lastPinnedKey ? 'border-r border-surface-700' : ''}`
      : base;

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
    data: filteredRows,
    columns: colDefs,
    state: { sorting },
    onSortingChange: setSorting,
    getCoreRowModel: getCoreRowModel(),
    getSortedRowModel: getSortedRowModel(),
    getRowId: getRowId ? (row) => getRowId(row) : undefined,
  });

  const tableRows = table.getRowModel().rows;
  // Expanded rows used to disable virtualization wholesale, which switched it
  // off for the forecast table and review dashboard -- the two largest grids in
  // the app. Only bail out while a row is actually expanded.
  const hasExpandedRow = Boolean(renderExpandedRow && (expandedRowIds?.size ?? 0) > 0);
  const useVirtual = !hasExpandedRow && tableRows.length > VIRTUALIZE_THRESHOLD;
  const colCount = columns.length + (rowActions ? 1 : 0) + (selectable ? 1 : 0);

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
  const exportRows = filteredRows as unknown as Record<string, unknown>[];

  const selectedRows = useMemo(
    () => (selectable ? filteredRows.filter((r, i) => selectedIds.has(rowKey(r, i))) : []),
    [selectable, filteredRows, selectedIds, rowKey],
  );

  const allVisibleSelected =
    selectable && filteredRows.length > 0 && selectedIds.size === filteredRows.length;

  const toggleAll = () =>
    setSelectedIds(
      allVisibleSelected ? new Set() : new Set(filteredRows.map((r, i) => rowKey(r, i))),
    );

  const toggleOne = (id: string) =>
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(id)) next.delete(id);
      else next.add(id);
      return next;
    });

  const totals = useMemo(() => {
    if (!showTotals) return null;
    const out: Record<string, number> = {};
    for (const c of columns) {
      if (!c.total) continue;
      out[c.key] = filteredRows.reduce((sum, row) => {
        const v = row[c.key];
        return typeof v === 'number' && Number.isFinite(v) ? sum + v : sum;
      }, 0);
    }
    return out;
  }, [showTotals, columns, filteredRows]);

  const onCopy = async () => {
    const source = selectedRows.length ? selectedRows : filteredRows;
    try {
      await navigator.clipboard.writeText(
        toTsv(exportCols, source as unknown as Record<string, unknown>[]),
      );
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      /* clipboard unavailable (insecure context / denied) -- stay silent */
    }
  };

  const onXlsx = async () => {
    setExportingXlsx(true);
    try {
      await downloadXlsx(exportFilename, exportCols, exportRows);
    } finally {
      setExportingXlsx(false);
    }
  };

  const renderRow = (row: (typeof tableRows)[number], index: number) => {
    const original = row.original;
    const extra = getRowClassName?.(original) || '';
    const key = getRowId ? getRowId(original) : row.id;
    const isExpanded = Boolean(renderExpandedRow && expandedRowIds?.has(key));
    const isSelected = selectable && selectedIds.has(rowKey(original, index));
    return (
      <Fragment key={key}>
        <tr
          className={`group border-b border-surface-800/80 hover:bg-surface-700/30 ${onRowClick ? 'cursor-pointer' : ''} ${isSelected ? 'bg-deloitte-green/10' : ''} ${extra}`}
          style={{ height: rowHeight }}
          onClick={onRowClick ? () => onRowClick(original) : undefined}
          tabIndex={onRowClick ? 0 : undefined}
          data-selected={isSelected || undefined}
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
          {selectable && (
            <td
              className="px-2 bg-surface-800"
              style={{ position: 'sticky', left: 0, zIndex: 5, width: 36 }}
              onClick={(e) => e.stopPropagation()}
            >
              <input
                type="checkbox"
                checked={isSelected}
                onChange={() => toggleOne(rowKey(original, index))}
                aria-label={`Select ${key}`}
                className="accent-deloitte-green"
              />
            </td>
          )}
          {row.getVisibleCells().map((cell, cellIndex) => {
            const col = columns[cellIndex];
            return (
              <td
                key={cell.id}
                className={pinnedClass(
                  col ?? ({} as DataTableColumn<T>),
                  'px-3 py-1.5 text-surface-200 whitespace-nowrap',
                )}
                style={col ? pinnedStyle(col, false) : undefined}
              >
                {flexRender(cell.column.columnDef.cell, cell.getContext())}
              </td>
            );
          })}
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
      <div className="flex items-center justify-between px-3 py-2 border-b border-surface-700/50 gap-2 flex-wrap">
        <h4 className="text-sm font-semibold text-white flex items-center gap-2 min-w-0">
          {title && <div className="w-0.5 h-3.5 bg-deloitte-green rounded-full shrink-0" />}
          <span className="truncate">{title || t('table.title')}</span>
        </h4>
        <div className="flex items-center gap-1.5 shrink-0">
          {searchable && (
            <div className="relative">
              <Search
                className="w-3.5 h-3.5 absolute left-2 top-1/2 -translate-y-1/2 text-surface-500"
                aria-hidden="true"
              />
              <input
                type="search"
                value={query}
                onChange={(e) => setQuery(e.target.value)}
                placeholder={searchPlaceholder || t('table.search')}
                aria-label={searchPlaceholder || t('table.search')}
                className="pl-7 pr-2 py-1 w-40 text-xs bg-surface-900 border border-surface-600 rounded-md text-surface-200 placeholder-surface-500 focus:outline-none focus:border-deloitte-green/50"
              />
            </div>
          )}
          {!hideExport && (
            <>
              <button
                type="button"
                onClick={onCopy}
                className="inline-flex items-center gap-1.5 text-xs text-surface-300 hover:text-white px-2 py-1 rounded-md border border-surface-600 hover:border-deloitte-green/40"
                aria-label={t('table.copy')}
                title={t('table.copyHint')}
              >
                <Copy className="w-3.5 h-3.5" />
                {copied ? t('table.copied') : t('table.copy')}
              </button>
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
            </>
          )}
        </div>
      </div>

      {selectable && selectedRows.length > 0 && (
        <div
          className="flex items-center gap-2 px-3 py-2 bg-deloitte-green/10 border-b border-deloitte-green/20 flex-wrap"
          role="region"
          aria-label={t('table.bulkActions')}
        >
          <span className="text-xs font-semibold text-deloitte-green">
            {t('table.selectedCount', { count: String(selectedRows.length) })}
          </span>
          {renderBulkActions?.(selectedRows, () => setSelectedIds(new Set()))}
          <button
            type="button"
            onClick={() => setSelectedIds(new Set())}
            className="ml-auto inline-flex items-center gap-1 text-xs text-surface-400 hover:text-white"
          >
            <X className="w-3 h-3" />
            {t('table.clearSelection')}
          </button>
        </div>
      )}

      {searchable && query && (
        <div className="px-3 py-1 text-xs text-surface-400" aria-live="polite">
          {t('table.filteredCount', {
            shown: String(filteredRows.length),
            total: String(rows.length),
          })}
        </div>
      )}

      <div ref={parentRef} className="overflow-auto" style={{ maxHeight }}>
        <table className="w-full text-xs" style={{ fontSize: 12 }}>
          <thead className="sticky top-0 bg-surface-800 z-10">
            {table.getHeaderGroups().map((hg) => (
              <tr key={hg.id} className="border-b border-surface-700">
                {selectable && (
                  <th
                    className="px-2 bg-surface-800"
                    style={{ position: 'sticky', left: 0, zIndex: 20, width: 36 }}
                  >
                    <input
                      type="checkbox"
                      checked={allVisibleSelected}
                      onChange={toggleAll}
                      aria-label={t('table.selectAll')}
                      className="accent-deloitte-green"
                    />
                  </th>
                )}
                {hg.headers.map((h, headerIndex) => {
                  const col = columns[headerIndex];
                  return (
                    <th
                      key={h.id}
                      className={pinnedClass(
                        col ?? ({} as DataTableColumn<T>),
                        'text-left px-3 py-2 text-surface-400 font-medium whitespace-nowrap',
                      )}
                      style={col ? pinnedStyle(col, true) : undefined}
                    >
                      {h.isPlaceholder ? null : flexRender(h.column.columnDef.header, h.getContext())}
                    </th>
                  );
                })}
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
                {virtualRows.map((vRow) => renderRow(tableRows[vRow.index], vRow.index))}
                {paddingBottom > 0 && (
                  <tr>
                    <td colSpan={colCount} style={{ height: paddingBottom, padding: 0, border: 0 }} />
                  </tr>
                )}
              </>
            ) : (
              tableRows.map((row, i) => renderRow(row, i))
            )}
          </tbody>
          {totals && Object.keys(totals).length > 0 && (
            <tfoot className="sticky bottom-0 bg-surface-800 border-t border-surface-700">
              <tr>
                {selectable && (
                  <td style={{ position: 'sticky', left: 0, zIndex: 5, width: 36 }} className="bg-surface-800" />
                )}
                {columns.map((c, i) => (
                  <td
                    key={c.key}
                    className={pinnedClass(
                      c,
                      `px-3 py-1.5 font-semibold text-surface-100 whitespace-nowrap ${alignClass(c.align)}`,
                    )}
                    style={pinnedStyle(c, false)}
                  >
                    {i === 0 && !(c.key in totals)
                      ? t('table.total')
                      : c.key in totals
                        ? totals[c.key].toLocaleString(undefined, { maximumFractionDigits: 2 })
                        : null}
                  </td>
                ))}
                {rowActions && <td />}
              </tr>
            </tfoot>
          )}
        </table>
      </div>
    </div>
  );
}
