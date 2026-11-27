/** Chart of accounts and wiring the standard dependencies. */
import { useEffect, useState } from 'react';
import { apiGet, apiPost } from '../../api/client';
import { type AdminTabProps } from './AdminConsoleShared';

export function AdminCoaTab({ onError, onMessage }: AdminTabProps) {
  const [coa, setCoa] = useState<any>(null);

  const load = async () => {
    try {
      setCoa(await apiGet('/admin/coa'));
    } catch (e: any) {
      onError(e.message || 'Failed to load');
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const wireCoa = async () => {
    try {
      const res = await apiPost<any>('/admin/coa/wire-standard');
      onMessage(`Wired ${res.dependencies_created} dependencies`);
      load();
    } catch (e: any) {
      onError(e.message);
    }
  };

  if (!coa) return null;

  return (
    <div className="space-y-4">
      <button
        type="button"
        onClick={wireCoa}
        className="px-3 py-1.5 text-xs bg-deloitte-green text-white font-semibold rounded-lg"
      >
        Wire standard P&L dependencies
      </button>
      <p className="text-xs text-surface-500">
        {coa.line_items?.length || 0} line items · {coa.dependencies?.length || 0} dependencies
      </p>
      <div className="max-h-96 overflow-auto border border-surface-700/40 rounded-xl">
        <table className="w-full text-xs">
          <thead className="bg-surface-900 text-surface-500 sticky top-0">
            <tr>
              <th className="px-3 py-2 text-left">Code</th>
              <th className="px-3 py-2 text-left">Name</th>
              <th className="px-3 py-2 text-left">Category</th>
              <th className="px-3 py-2 text-left">Calculated</th>
            </tr>
          </thead>
          <tbody>
            {(coa.line_items || []).map((li: any) => (
              <tr key={li.id} className="border-t border-surface-700/40">
                <td className="px-3 py-1.5">{li.account_code}</td>
                <td className="px-3 py-1.5">{li.name}</td>
                <td className="px-3 py-1.5 text-surface-400">{li.category}</td>
                <td className="px-3 py-1.5">{li.is_calculated ? 'Yes' : ''}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}
