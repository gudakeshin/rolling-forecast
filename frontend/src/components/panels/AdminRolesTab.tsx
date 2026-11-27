/** Roles: the permission flags of each role. */
import { useEffect, useState } from 'react';
import { apiGet, apiPatch } from '../../api/client';
import { toast } from '../../store/toastStore';
import { ROLE_PERMISSION_KEYS, type AdminTabProps } from './AdminConsoleShared';

export function AdminRolesTab({ onError }: AdminTabProps) {
  const [roles, setRoles] = useState<any[]>([]);
  const [savingRoleName, setSavingRoleName] = useState<string | null>(null);

  const load = async () => {
    try {
      setRoles(await apiGet('/admin/roles'));
    } catch (e: any) {
      onError(e.message || 'Failed to load');
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const toggleRolePermission = (roleName: string, key: string) => {
    setRoles((prev) =>
      prev.map((r) => (r.name === roleName ? { ...r, [key]: !r[key] } : r)),
    );
  };

  const saveRole = async (role: any) => {
    setSavingRoleName(role.name);
    onError('');
    try {
      await apiPatch(`/admin/roles/${role.name}`, {
        can_input: role.can_input,
        can_generate: role.can_generate,
        can_override: role.can_override,
        can_review: role.can_review,
        can_publish: role.can_publish,
        can_admin: role.can_admin,
        can_view_all_bus: role.can_view_all_bus,
        can_manage_drivers: role.can_manage_drivers,
      });
      toast.success(`Updated role "${role.name}"`);
    } catch (e: any) {
      onError(e.message || 'Failed to update role');
      toast.error(e.message || 'Failed to update role');
    } finally {
      setSavingRoleName(null);
    }
  };

  return (
    <div className="space-y-3">
      {roles.map((r) => (
        <div key={r.id} className="bg-surface-900 border border-surface-700/50 rounded-lg p-4">
          <div className="flex items-center justify-between mb-1">
            <div className="font-semibold">{r.name}</div>
            <button
              type="button"
              onClick={() => saveRole(r)}
              disabled={savingRoleName === r.name}
              className="text-xs px-2.5 py-1 bg-deloitte-green text-white font-semibold rounded-lg disabled:opacity-50"
            >
              Save
            </button>
          </div>
          <p className="text-xs text-surface-500 mb-2">{r.description}</p>
          <div className="flex flex-wrap gap-3 text-xs">
            {ROLE_PERMISSION_KEYS.map(({ key, label }) => (
              <label key={key} className="flex items-center gap-1.5 text-surface-300">
                <input
                  type="checkbox"
                  checked={Boolean(r[key])}
                  onChange={() => toggleRolePermission(r.name, key)}
                />
                {label}
              </label>
            ))}
          </div>
        </div>
      ))}
    </div>
  );
}
