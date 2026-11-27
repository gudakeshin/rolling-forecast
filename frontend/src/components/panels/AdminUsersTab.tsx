/** Users: role, company and active flag per user. */
import { Pencil } from 'lucide-react';
import { useEffect, useState } from 'react';
import { apiGet, apiPatch } from '../../api/client';
import { toast } from '../../store/toastStore';
import { useWorkspaceStore } from '../../store/workspaceStore';
import type { User } from '../../types/auth';
import { BusinessUnit, type AdminTabProps } from './AdminConsoleShared';

export function AdminUsersTab({ onError }: AdminTabProps) {
  const hydrateWorkspace = useWorkspaceStore((s) => s.hydrate);
  const [users, setUsers] = useState<User[]>([]);
  const [roles, setRoles] = useState<any[]>([]);
  const [businessUnits, setBusinessUnits] = useState<BusinessUnit[]>([]);
  const [editingUserId, setEditingUserId] = useState<string | null>(null);
  const [editRoleName, setEditRoleName] = useState('');
  const [editBusinessUnit, setEditBusinessUnit] = useState('');
  const [editActive, setEditActive] = useState(true);
  const [savingUserId, setSavingUserId] = useState<string | null>(null);

  const load = async () => {
    try {
      const [userRows, roleRows, buRows] = await Promise.all([
        apiGet<User[]>('/admin/users'),
        apiGet<any[]>('/admin/roles'),
        apiGet<BusinessUnit[]>('/admin/business-units'),
      ]);
      setUsers(userRows);
      setRoles(roleRows);
      setBusinessUnits(buRows);
    } catch (e: any) {
      onError(e.message || 'Failed to load');
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startEditUser = (u: User) => {
    setEditingUserId(u.id);
    setEditRoleName(u.role_name);
    setEditBusinessUnit(u.business_unit || '');
    setEditActive(u.is_active);
  };

  const cancelEditUser = () => setEditingUserId(null);

  const saveUser = async (u: User) => {
    setSavingUserId(u.id);
    onError('');
    try {
      await apiPatch(`/admin/users/${u.id}`, {
        role_name: editRoleName,
        business_unit: editBusinessUnit,
        is_active: editActive,
      });
      toast.success(`Updated ${u.username}`);
      setEditingUserId(null);
      await load();
      // A BU reassignment can affect who is a cross-company caller, and
      // switching a company off the roster of BUs a switcher should offer.
      void hydrateWorkspace();
    } catch (e: any) {
      onError(e.message || 'Failed to update user');
      toast.error(e.message || 'Failed to update user');
    } finally {
      setSavingUserId(null);
    }
  };

  return (
    <table className="w-full text-sm">
      <thead className="text-surface-500 text-left">
        <tr>
          <th className="py-2">Username</th>
          <th>Email</th>
          <th>Role</th>
          <th>Company</th>
          <th>Active</th>
          <th>Actions</th>
        </tr>
      </thead>
      <tbody>
        {users.map((u) => {
          const editing = editingUserId === u.id;
          return (
            <tr key={u.id} className="border-t border-surface-700/40">
              <td className="py-2">{u.username}</td>
              <td className="text-surface-400">{u.email}</td>
              <td>
                {editing ? (
                  <select
                    value={editRoleName}
                    onChange={(e) => setEditRoleName(e.target.value)}
                    className="bg-surface-800 border border-surface-600 rounded-lg px-2 py-1 text-xs"
                  >
                    {roles.map((r) => (
                      <option key={r.name} value={r.name}>{r.name}</option>
                    ))}
                  </select>
                ) : (
                  u.role_name
                )}
              </td>
              <td className="text-surface-400">
                {editing ? (
                  <select
                    value={editBusinessUnit}
                    onChange={(e) => setEditBusinessUnit(e.target.value)}
                    className="bg-surface-800 border border-surface-600 rounded-lg px-2 py-1 text-xs"
                  >
                    <option value="">— None —</option>
                    {businessUnits.map((bu) => (
                      <option key={bu.id} value={bu.name}>{bu.name}</option>
                    ))}
                  </select>
                ) : (
                  u.business_unit || '—'
                )}
              </td>
              <td>
                {editing ? (
                  <input
                    type="checkbox"
                    checked={editActive}
                    onChange={(e) => setEditActive(e.target.checked)}
                    aria-label={`${u.username} active`}
                  />
                ) : (
                  u.is_active ? 'Yes' : 'No'
                )}
              </td>
              <td>
                {editing ? (
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => saveUser(u)}
                      disabled={savingUserId === u.id}
                      className="text-xs px-2 py-1 bg-deloitte-green text-white font-semibold rounded-lg disabled:opacity-50"
                    >
                      Save
                    </button>
                    <button
                      type="button"
                      onClick={cancelEditUser}
                      className="text-xs px-2 py-1 bg-surface-800 border border-surface-700 text-surface-300 rounded-lg"
                    >
                      Cancel
                    </button>
                  </div>
                ) : (
                  <button
                    type="button"
                    onClick={() => startEditUser(u)}
                    className="text-surface-400 hover:text-white"
                    aria-label={`Edit ${u.username}`}
                  >
                    <Pencil className="w-3.5 h-3.5" />
                  </button>
                )}
              </td>
            </tr>
          );
        })}
      </tbody>
    </table>
  );
}
