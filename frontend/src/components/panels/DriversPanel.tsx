import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity, AlertTriangle, Link2, Loader2, Plus, RefreshCw, Upload,
} from 'lucide-react';
import {
  createDriver,
  createDriverLink,
  listDriverLinks,
  listDrivers,
  promoteDriverLink,
  uploadDriversFile,
  type Driver,
  type DriverLink,
} from '../../api/drivers';
import { useI18n } from '../../i18n/useI18n';
import { toast } from '../../store/toastStore';
import { DataTable, type DataTableColumn } from '../ui/DataTable';

const DRIVER_TYPES = [
  'volume', 'price', 'headcount', 'macro', 'index', 'other',
];

export function DriversPanel() {
  const { t } = useI18n();
  const [drivers, setDrivers] = useState<Driver[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<number | null>(null);
  const [links, setLinks] = useState<DriverLink[]>([]);
  const [linksLoading, setLinksLoading] = useState(false);
  const [creating, setCreating] = useState(false);
  const [uploading, setUploading] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const [form, setForm] = useState({
    key: '',
    name: '',
    driver_type: 'other',
    unit: '',
    business_unit: '',
  });
  const [linkForm, setLinkForm] = useState({
    line_item_id: '',
    relation: 'level',
    lag: '0',
    coefficient: '',
  });

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const rows = await listDrivers({ include_freshness: true });
      setDrivers(rows);
      if (selectedId && !rows.some((d) => d.id === selectedId)) {
        setSelectedId(null);
        setLinks([]);
      }
    } catch (e: any) {
      toast.error(e?.message || t('drivers.error.load'));
    } finally {
      setLoading(false);
    }
  }, [selectedId, t]);

  useEffect(() => {
    void load();
  }, [load]);

  const loadLinks = useCallback(async (driverId: number) => {
    setLinksLoading(true);
    try {
      setLinks(await listDriverLinks(driverId));
    } catch (e: any) {
      toast.error(e?.message || t('drivers.error.links'));
      setLinks([]);
    } finally {
      setLinksLoading(false);
    }
  }, [t]);

  useEffect(() => {
    if (selectedId != null) void loadLinks(selectedId);
  }, [selectedId, loadLinks]);

  const selected = useMemo(
    () => drivers.find((d) => d.id === selectedId) ?? null,
    [drivers, selectedId],
  );

  const handleCreate = async () => {
    if (!form.key.trim() || !form.name.trim()) {
      toast.error(t('drivers.error.keyName'));
      return;
    }
    setCreating(true);
    try {
      const created = await createDriver({
        key: form.key.trim(),
        name: form.name.trim(),
        driver_type: form.driver_type,
        unit: form.unit.trim() || undefined,
        business_unit: form.business_unit.trim() || undefined,
        source: 'manual',
      });
      toast.success(t('drivers.success.created', { key: created.key }));
      setForm({ key: '', name: '', driver_type: 'other', unit: '', business_unit: '' });
      await load();
      setSelectedId(created.id);
    } catch (e: any) {
      toast.error(e?.message || t('drivers.error.create'));
    } finally {
      setCreating(false);
    }
  };

  const handleUpload = async (file: File) => {
    setUploading(true);
    try {
      const result = await uploadDriversFile(file);
      toast.success(result?.message || t('drivers.success.uploaded'));
      await load();
    } catch (e: any) {
      toast.error(e?.message || t('drivers.error.upload'));
    } finally {
      setUploading(false);
      if (fileRef.current) fileRef.current.value = '';
    }
  };

  const handleCreateLink = async () => {
    if (!selected) return;
    const lineItemId = Number(linkForm.line_item_id);
    if (!Number.isFinite(lineItemId) || lineItemId <= 0) {
      toast.error(t('drivers.error.lineItem'));
      return;
    }
    try {
      await createDriverLink({
        driver_id: selected.id,
        line_item_id: lineItemId,
        relation: linkForm.relation,
        lag: Number(linkForm.lag) || 0,
        coefficient: linkForm.coefficient ? Number(linkForm.coefficient) : undefined,
        status: 'candidate',
      });
      toast.success(t('drivers.success.link'));
      setLinkForm({ line_item_id: '', relation: 'level', lag: '0', coefficient: '' });
      await loadLinks(selected.id);
    } catch (e: any) {
      toast.error(e?.message || t('drivers.error.link'));
    }
  };

  const handlePromote = async (linkId: number) => {
    try {
      await promoteDriverLink(linkId);
      toast.success(t('drivers.success.promote'));
      if (selected) await loadLinks(selected.id);
    } catch (e: any) {
      toast.error(e?.message || t('drivers.error.promote'));
    }
  };

  const columns = useMemo<DataTableColumn<Driver & Record<string, unknown>>[]>(
    () => [
      {
        key: 'key',
        label: t('drivers.col.key'),
        render: (_, row) => (
          <button
            type="button"
            className="text-left text-deloitte-green hover:underline font-mono text-xs"
            onClick={() => setSelectedId(row.id)}
          >
            {row.key}
          </button>
        ),
      },
      { key: 'name', label: t('drivers.col.name') },
      { key: 'driver_type', label: t('drivers.col.type') },
      {
        key: 'business_unit',
        label: t('drivers.col.bu'),
        render: (v) => String(v ?? '—'),
      },
      {
        key: 'freshness',
        label: t('drivers.col.freshness'),
        render: (_v, row) => {
          const f = row.freshness;
          if (!f) return <span className="text-surface-500">—</span>;
          return (
            <span
              className={`inline-flex items-center gap-1 text-xs ${
                f.stale ? 'text-amber-400' : 'text-deloitte-green'
              }`}
            >
              {f.stale ? <AlertTriangle className="w-3 h-3" /> : <Activity className="w-3 h-3" />}
              {f.last_period || t('drivers.freshness.noData')}
              {f.stale ? ` ${t('drivers.freshness.stale')}` : ''}
            </span>
          );
        },
      },
    ],
    [t],
  );

  return (
    <div className="space-y-4">
      <div className="flex items-center gap-2 flex-wrap">
        <button
          type="button"
          onClick={() => void load()}
          className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg border border-surface-600 text-surface-300 hover:border-deloitte-green/40"
        >
          <RefreshCw className="w-3.5 h-3.5" />
          {t('drivers.refresh')}
        </button>
        <button
          type="button"
          onClick={() => fileRef.current?.click()}
          disabled={uploading}
          className="inline-flex items-center gap-1.5 text-xs px-2.5 py-1.5 rounded-lg bg-deloitte-green/15 text-deloitte-green border border-deloitte-green/25 hover:bg-deloitte-green/25 disabled:opacity-50"
        >
          {uploading ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Upload className="w-3.5 h-3.5" />}
          {t('drivers.upload')}
        </button>
        <input
          ref={fileRef}
          type="file"
          accept=".csv,.xlsx,.xls"
          className="hidden"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) void handleUpload(f);
          }}
        />
        <span className="text-xs text-surface-500 ml-auto">
          {t('drivers.count', { count: drivers.length })}
        </span>
      </div>

      <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3 space-y-2">
        <div className="flex items-center gap-1.5 text-xs font-semibold text-white">
          <Plus className="w-3.5 h-3.5 text-deloitte-green" />
          {t('drivers.createTitle')}
        </div>
        <div className="grid grid-cols-2 gap-2">
          <input
            placeholder={t('drivers.keyPlaceholder')}
            value={form.key}
            onChange={(e) => setForm((s) => ({ ...s, key: e.target.value }))}
            className="text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
          />
          <input
            placeholder={t('drivers.namePlaceholder')}
            value={form.name}
            onChange={(e) => setForm((s) => ({ ...s, name: e.target.value }))}
            className="text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
          />
          <select
            value={form.driver_type}
            onChange={(e) => setForm((s) => ({ ...s, driver_type: e.target.value }))}
            className="text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
          >
            {DRIVER_TYPES.map((dt) => (
              <option key={dt} value={dt}>{dt}</option>
            ))}
          </select>
          <input
            placeholder={t('drivers.unitPlaceholder')}
            value={form.unit}
            onChange={(e) => setForm((s) => ({ ...s, unit: e.target.value }))}
            className="text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
          />
          <input
            placeholder={t('drivers.buPlaceholder')}
            value={form.business_unit}
            onChange={(e) => setForm((s) => ({ ...s, business_unit: e.target.value }))}
            className="text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200 col-span-2"
          />
        </div>
        <button
          type="button"
          onClick={() => void handleCreate()}
          disabled={creating}
          className="text-xs font-medium px-3 py-1.5 rounded-lg bg-deloitte-green text-white hover:bg-deloitte-green/90 disabled:opacity-50"
        >
          {creating ? t('drivers.creating') : t('drivers.create')}
        </button>
      </div>

      {loading ? (
        <div className="flex justify-center py-8">
          <Loader2 className="w-5 h-5 animate-spin text-deloitte-green" />
        </div>
      ) : drivers.length === 0 ? (
        <p className="text-sm text-surface-500 text-center py-8">
          {t('drivers.empty')}
        </p>
      ) : (
        <DataTable
          title={t('drivers.tableTitle')}
          columns={columns}
          rows={drivers as Array<Driver & Record<string, unknown>>}
          getRowId={(r) => String(r.id)}
          maxHeight={320}
          rowHeight={44}
        />
      )}

      {selected && (
        <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3 space-y-3">
          <div className="flex items-center gap-2">
            <Link2 className="w-4 h-4 text-cyan-400" />
            <span className="text-sm font-semibold text-white">
              {t('drivers.linksTitle', { name: selected.name })}
            </span>
            <span className="text-xs text-surface-500 font-mono">{selected.key}</span>
          </div>

          <div className="grid grid-cols-2 gap-2">
            <input
              placeholder={t('drivers.lineItemId')}
              value={linkForm.line_item_id}
              onChange={(e) => setLinkForm((s) => ({ ...s, line_item_id: e.target.value }))}
              className="text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
            />
            <select
              value={linkForm.relation}
              onChange={(e) => setLinkForm((s) => ({ ...s, relation: e.target.value }))}
              className="text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
            >
              <option value="level">level</option>
              <option value="quantity">quantity</option>
              <option value="unit_price">unit_price</option>
              <option value="elasticity">elasticity</option>
            </select>
            <input
              placeholder={t('drivers.lag')}
              value={linkForm.lag}
              onChange={(e) => setLinkForm((s) => ({ ...s, lag: e.target.value }))}
              className="text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
            />
            <input
              placeholder={t('drivers.coefficient')}
              value={linkForm.coefficient}
              onChange={(e) => setLinkForm((s) => ({ ...s, coefficient: e.target.value }))}
              className="text-xs bg-surface-900 border border-surface-700 rounded-lg px-2 py-1.5 text-surface-200"
            />
          </div>
          <button
            type="button"
            onClick={() => void handleCreateLink()}
            className="text-xs font-medium px-3 py-1.5 rounded-lg border border-cyan-500/30 text-cyan-400 hover:bg-cyan-500/10"
          >
            {t('drivers.assertLink')}
          </button>

          {linksLoading ? (
            <Loader2 className="w-4 h-4 animate-spin text-surface-500" />
          ) : links.length === 0 ? (
            <p className="text-xs text-surface-500">{t('drivers.noLinks')}</p>
          ) : (
            <ul className="space-y-1.5">
              {links.map((l) => (
                <li
                  key={l.id}
                  className="flex items-center justify-between gap-2 text-xs bg-surface-900/60 rounded-lg px-2.5 py-2 border border-surface-700/40"
                >
                  <span className="text-surface-300">
                    line {l.line_item_id} · {l.relation} · lag {l.lag}
                    {l.coefficient != null ? ` · β=${l.coefficient}` : ''}
                  </span>
                  <span className="flex items-center gap-2">
                    <span
                      className={`px-1.5 py-0.5 rounded ${
                        l.status === 'active'
                          ? 'bg-deloitte-green/15 text-deloitte-green'
                          : 'bg-amber-500/15 text-amber-400'
                      }`}
                    >
                      {l.status}
                    </span>
                    {l.status !== 'active' && (
                      <button
                        type="button"
                        onClick={() => void handlePromote(l.id)}
                        className="text-deloitte-green hover:underline"
                      >
                        {t('drivers.promote')}
                      </button>
                    )}
                  </span>
                </li>
              ))}
            </ul>
          )}
        </div>
      )}
    </div>
  );
}
