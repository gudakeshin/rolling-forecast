import { describe, it, expect, vi, beforeEach } from 'vitest';
import { render, screen, within, fireEvent, waitFor } from '@testing-library/react';
import { axe } from 'vitest-axe';
import { DataTable, type DataTableColumn } from './DataTable';

interface Row extends Record<string, unknown> {
  id: string;
  account: string;
  bu: string;
  jan: number;
  feb: number;
}

const COLUMNS: DataTableColumn<Row>[] = [
  { key: 'account', label: 'Account', pinned: true, width: 180 },
  { key: 'bu', label: 'Business Unit' },
  { key: 'jan', label: 'Jan', align: 'right', total: true },
  { key: 'feb', label: 'Feb', align: 'right', total: true },
];

const ROWS: Row[] = [
  { id: 'r1', account: 'Product Revenue', bu: 'EMEA', jan: 100, feb: 110 },
  { id: 'r2', account: 'Services Revenue', bu: 'AMER', jan: 50, feb: 55 },
  { id: 'r3', account: 'Support Revenue', bu: 'EMEA', jan: 25, feb: 20 },
];

const renderTable = (props: Partial<React.ComponentProps<typeof DataTable<Row>>> = {}) =>
  render(
    <DataTable<Row>
      title="Forecast"
      columns={COLUMNS}
      rows={ROWS}
      getRowId={(r) => r.id}
      {...props}
    />,
  );

describe('DataTable', () => {
  it('renders every row and column', () => {
    renderTable();
    expect(screen.getByText('Product Revenue')).toBeInTheDocument();
    expect(screen.getByText('Support Revenue')).toBeInTheDocument();
    expect(screen.getByRole('columnheader', { name: /Business Unit/ })).toBeInTheDocument();
  });

  it('has no detectable a11y violations', async () => {
    const { container } = renderTable({ searchable: true, selectable: true, showTotals: true });
    expect(await axe(container)).toHaveNoViolations();
  });

  describe('column pinning', () => {
    it('sticks the pinned column to the left so it survives horizontal scroll', () => {
      renderTable();
      const header = screen.getByRole('columnheader', { name: /Account/ });
      expect(header).toHaveStyle({ position: 'sticky', left: '0px' });
    });

    it('offsets pinned columns by the checkbox gutter when selectable', () => {
      renderTable({ selectable: true });
      const header = screen.getByRole('columnheader', { name: /Account/ });
      // 36px checkbox column, so the first pinned data column starts after it.
      expect(header).toHaveStyle({ left: '36px' });
    });

    it('leaves unpinned columns in normal flow', () => {
      renderTable();
      const header = screen.getByRole('columnheader', { name: /Business Unit/ });
      expect(header).not.toHaveStyle({ position: 'sticky' });
    });
  });

  describe('search', () => {
    it('filters rows across all columns', () => {
      renderTable({ searchable: true });
      fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'AMER' } });
      expect(screen.getByText('Services Revenue')).toBeInTheDocument();
      expect(screen.queryByText('Product Revenue')).not.toBeInTheDocument();
    });

    it('announces how many rows survived the filter', () => {
      renderTable({ searchable: true });
      fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'EMEA' } });
      expect(screen.getByText('Showing 2 of 3')).toBeInTheDocument();
    });
  });

  describe('selection', () => {
    it('selects and clears individual rows', () => {
      renderTable({ selectable: true });
      fireEvent.click(screen.getByLabelText('Select r1'));
      expect(screen.getByText('1 selected')).toBeInTheDocument();
      fireEvent.click(screen.getByText('Clear'));
      expect(screen.queryByText('1 selected')).not.toBeInTheDocument();
    });

    it('select-all covers every visible row', () => {
      renderTable({ selectable: true });
      fireEvent.click(screen.getByLabelText('Select all rows'));
      expect(screen.getByText('3 selected')).toBeInTheDocument();
    });

    it('hands the selected rows to the bulk action bar', () => {
      const onBulk = vi.fn();
      renderTable({
        selectable: true,
        renderBulkActions: (selected) => (
          <button type="button" onClick={() => onBulk(selected)}>
            Approve
          </button>
        ),
      });
      fireEvent.click(screen.getByLabelText('Select r1'));
      fireEvent.click(screen.getByLabelText('Select r3'));
      fireEvent.click(screen.getByText('Approve'));
      expect(onBulk).toHaveBeenCalledWith([
        expect.objectContaining({ id: 'r1' }),
        expect.objectContaining({ id: 'r3' }),
      ]);
    });

    it('drops selections for rows a filter has hidden', () => {
      // A bulk action must never reach a row the analyst can no longer see.
      renderTable({ selectable: true, searchable: true });
      fireEvent.click(screen.getByLabelText('Select r2')); // Services / AMER
      expect(screen.getByText('1 selected')).toBeInTheDocument();
      fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'EMEA' } });
      expect(screen.queryByText('1 selected')).not.toBeInTheDocument();
    });

    it('does not trigger onRowClick when ticking the checkbox', () => {
      const onRowClick = vi.fn();
      renderTable({ selectable: true, onRowClick });
      fireEvent.click(screen.getByLabelText('Select r1'));
      expect(onRowClick).not.toHaveBeenCalled();
    });
  });

  describe('totals', () => {
    it('sums only the columns marked total, over the filtered set', () => {
      renderTable({ showTotals: true, searchable: true });
      const footer = screen.getByRole('table').querySelector('tfoot')!;
      expect(within(footer).getByText('175')).toBeInTheDocument(); // jan
      expect(within(footer).getByText('185')).toBeInTheDocument(); // feb

      fireEvent.change(screen.getByRole('searchbox'), { target: { value: 'EMEA' } });
      expect(within(footer).getByText('125')).toBeInTheDocument(); // 100 + 25
    });

    it('is absent unless asked for', () => {
      renderTable();
      expect(screen.getByRole('table').querySelector('tfoot')).toBeNull();
    });
  });

  describe('copy to clipboard', () => {
    const writeText = vi.fn().mockResolvedValue(undefined);

    beforeEach(() => {
      writeText.mockClear();
      Object.assign(navigator, { clipboard: { writeText } });
    });

    it('copies the whole filtered set as TSV when nothing is selected', async () => {
      renderTable();
      fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
      await waitFor(() => expect(writeText).toHaveBeenCalled());
      const payload = writeText.mock.calls[0][0] as string;
      expect(payload.split('\n')[0]).toBe('Account\tBusiness Unit\tJan\tFeb');
      expect(payload.split('\n')).toHaveLength(4); // header + 3 rows
    });

    it('copies only the selection when there is one', async () => {
      renderTable({ selectable: true });
      fireEvent.click(screen.getByLabelText('Select r2'));
      fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
      await waitFor(() => expect(writeText).toHaveBeenCalled());
      const payload = writeText.mock.calls[0][0] as string;
      expect(payload).toContain('Services Revenue');
      expect(payload).not.toContain('Product Revenue');
    });

    it('survives a clipboard rejection without throwing', async () => {
      writeText.mockRejectedValueOnce(new Error('denied'));
      renderTable();
      fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
      await waitFor(() => expect(writeText).toHaveBeenCalled());
      expect(screen.getByText('Product Revenue')).toBeInTheDocument();
    });
  });

  describe('virtualization', () => {
    const many: Row[] = Array.from({ length: 120 }, (_, i) => ({
      id: `v${i}`,
      account: `Line ${i}`,
      bu: 'EMEA',
      jan: i,
      feb: i,
    }));

    it('windows large tables instead of rendering every row', () => {
      render(
        <DataTable<Row> columns={COLUMNS} rows={many} getRowId={(r) => r.id} />,
      );
      expect(screen.getByRole('table').querySelectorAll('tbody tr').length).toBeLessThan(120);
    });

    it('still windows when expandable rows exist but none are open', () => {
      // Previously any renderExpandedRow prop disabled virtualization outright,
      // which switched it off for the two largest grids in the app.
      render(
        <DataTable<Row>
          columns={COLUMNS}
          rows={many}
          getRowId={(r) => r.id}
          expandedRowIds={new Set()}
          renderExpandedRow={(r) => <div>detail {String(r.id)}</div>}
        />,
      );
      expect(screen.getByRole('table').querySelectorAll('tbody tr').length).toBeLessThan(120);
    });

    it('falls back to full render while a row is expanded, and shows the detail', () => {
      render(
        <DataTable<Row>
          columns={COLUMNS}
          rows={many}
          getRowId={(r) => r.id}
          expandedRowIds={new Set(['v3'])}
          renderExpandedRow={(r) => <div>detail {String(r.id)}</div>}
        />,
      );
      expect(screen.getByText('detail v3')).toBeInTheDocument();
    });
  });

  it('uses exportValue for copy when a column overrides its serialization', async () => {
    const writeText = vi.fn().mockResolvedValue(undefined);
    Object.assign(navigator, { clipboard: { writeText } });
    render(
      <DataTable<Row>
        columns={[
          { key: 'account', label: 'Account' },
          { key: 'jan', label: 'Jan', render: () => <b>rich</b>, exportValue: (v) => `$${v}` },
        ]}
        rows={[ROWS[0]]}
        getRowId={(r) => r.id}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Copy' }));
    await waitFor(() => expect(writeText).toHaveBeenCalled());
    expect(writeText.mock.calls[0][0]).toContain('$100');
  });
});
