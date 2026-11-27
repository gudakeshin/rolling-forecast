/** The last 50 audit events. */
import { useEffect, useState } from 'react';
import { apiGet } from '../../api/client';
import { type AdminTabProps } from './AdminConsoleShared';

export function AdminAuditTab({ onError }: AdminTabProps) {
  const [audit, setAudit] = useState<any>(null);

  const load = async () => {
    try {
      setAudit(await apiGet('/admin/audit?limit=50'));
    } catch (e: any) {
      onError(e.message || 'Failed to load');
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  if (!audit) return null;

  return (
    <div className="space-y-2 max-h-[70vh] overflow-auto">
      {(audit.events || []).map((e: any) => (
        <div key={e.id} className="bg-surface-900 border border-surface-700/40 rounded-lg px-3 py-2 text-xs">
          <div className="flex justify-between gap-2">
            <span className="text-deloitte-green font-mono">{e.action}</span>
            <span className="text-surface-500">{e.timestamp}</span>
          </div>
          <div className="text-surface-400 mt-0.5">
            {e.actor_username || 'system'} · {e.entity_type}/{e.entity_id || '—'}
          </div>
        </div>
      ))}
    </div>
  );
}
