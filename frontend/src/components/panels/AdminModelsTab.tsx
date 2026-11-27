/** Model presets: candidate models, default horizon, activation. */
import { Pencil, Trash2 } from 'lucide-react';
import { useEffect, useState } from 'react';
import {
  createModelPreset, deactivateModelPreset, getAvailableModels, listModelPresets,
  updateModelPreset, type AvailableModel, type ModelPreset,
} from '../../api/modelPresets';
import { confirmDialog } from '../../store/confirmStore';
import { toast } from '../../store/toastStore';
import { emptyPresetForm, type AdminTabProps } from './AdminConsoleShared';

export function AdminModelsTab({ onError }: AdminTabProps) {
  const [modelPresets, setModelPresets] = useState<ModelPreset[]>([]);
  const [availableModels, setAvailableModels] = useState<AvailableModel[]>([]);
  const [presetForm, setPresetForm] = useState(emptyPresetForm);
  const [editingPresetId, setEditingPresetId] = useState<string | null>(null);
  const [savingPreset, setSavingPreset] = useState(false);

  const load = async () => {
    try {
      const [presets, models] = await Promise.all([
        listModelPresets(true),
        getAvailableModels(),
      ]);
      setModelPresets(presets);
      setAvailableModels(models);
    } catch (e: any) {
      onError(e.message || 'Failed to load');
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const startEditPreset = (p: ModelPreset) => {
    setEditingPresetId(p.id);
    setPresetForm({
      name: p.name,
      description: p.description || '',
      model_type: p.model_type,
      candidate_models: p.candidate_models || [],
      default_horizon_months: p.default_horizon_months ? String(p.default_horizon_months) : '',
    });
  };

  const cancelPresetEdit = () => {
    setEditingPresetId(null);
    setPresetForm(emptyPresetForm);
  };

  const savePreset = async () => {
    if (!presetForm.name.trim()) {
      onError('Preset name is required');
      return;
    }
    setSavingPreset(true);
    onError('');
    try {
      const body = {
        name: presetForm.name.trim(),
        description: presetForm.description.trim() || null,
        model_type: presetForm.model_type,
        candidate_models:
          presetForm.model_type === 'auto' && presetForm.candidate_models.length
            ? presetForm.candidate_models
            : null,
        default_horizon_months: presetForm.default_horizon_months
          ? Number(presetForm.default_horizon_months)
          : null,
      };
      if (editingPresetId) {
        await updateModelPreset(editingPresetId, body);
        toast.success(`Updated preset "${body.name}"`);
      } else {
        await createModelPreset(body);
        toast.success(`Created preset "${body.name}"`);
      }
      cancelPresetEdit();
      load();
    } catch (e: any) {
      onError(e.message || 'Failed to save model preset');
      toast.error(e.message || 'Failed to save model preset');
    } finally {
      setSavingPreset(false);
    }
  };

  const handleDeactivatePreset = async (p: ModelPreset) => {
    const ok = await confirmDialog(`Deactivate model preset "${p.name}"?`, {
      title: 'Deactivate preset',
      confirmLabel: 'Deactivate',
      danger: true,
    });
    if (!ok) return;
    try {
      await deactivateModelPreset(p.id);
      toast.success(`Deactivated "${p.name}"`);
      load();
    } catch (e: any) {
      toast.error(e.message || 'Failed to deactivate preset');
    }
  };

  const toggleCandidateModel = (name: string) => {
    setPresetForm((prev) => ({
      ...prev,
      candidate_models: prev.candidate_models.includes(name)
        ? prev.candidate_models.filter((m) => m !== name)
        : [...prev.candidate_models, name],
    }));
  };

  return (
    <div className="space-y-6">
      <div className="bg-surface-900 border border-surface-700/50 rounded-xl p-4 space-y-3">
        <h3 className="text-sm font-semibold">
          {editingPresetId ? 'Edit model preset' : 'New model preset'}
        </h3>
        <p className="text-xs text-surface-500">
          A saved shortcut for generate_baseline's model_type/models_to_test — pin one
          algorithm for every line item, or restrict the auto-selection pool.
        </p>
        <div className="grid grid-cols-1 md:grid-cols-2 gap-2">
          <input
            value={presetForm.name}
            onChange={(e) => setPresetForm({ ...presetForm, name: e.target.value })}
            placeholder="Name (e.g. Conservative)"
            className="bg-surface-800 border border-surface-600 rounded-lg px-3 py-1.5 text-sm"
          />
          <input
            value={presetForm.description}
            onChange={(e) => setPresetForm({ ...presetForm, description: e.target.value })}
            placeholder="Description (optional)"
            className="bg-surface-800 border border-surface-600 rounded-lg px-3 py-1.5 text-sm"
          />
          <select
            value={presetForm.model_type}
            onChange={(e) =>
              setPresetForm({ ...presetForm, model_type: e.target.value, candidate_models: [] })
            }
            className="bg-surface-800 border border-surface-600 rounded-lg px-3 py-1.5 text-sm"
          >
            <option value="auto">auto (best per line item)</option>
            {availableModels.map((m) => (
              <option key={m.name} value={m.name}>
                {m.display_label || m.name}
              </option>
            ))}
          </select>
          <input
            type="number"
            min={1}
            max={60}
            value={presetForm.default_horizon_months}
            onChange={(e) =>
              setPresetForm({ ...presetForm, default_horizon_months: e.target.value })
            }
            placeholder="Default horizon (months, optional)"
            className="bg-surface-800 border border-surface-600 rounded-lg px-3 py-1.5 text-sm"
          />
        </div>
        {presetForm.model_type === 'auto' && (
          <div>
            <p className="text-xs text-surface-500 mb-1.5">
              Restrict candidate pool (optional — leave empty to consider all registered models)
            </p>
            <div className="flex flex-wrap gap-1.5">
              {availableModels
                .filter((m) => m.auto_selectable && !m.is_benchmark)
                .map((m) => (
                  <button
                    key={m.name}
                    type="button"
                    onClick={() => toggleCandidateModel(m.name)}
                    className={`px-2 py-1 text-xs rounded-lg border transition-colors ${
                      presetForm.candidate_models.includes(m.name)
                        ? 'bg-deloitte-green/12 border-deloitte-green/30 text-deloitte-green font-semibold'
                        : 'bg-surface-800 border-surface-700 text-surface-400'
                    }`}
                  >
                    {m.display_label || m.name}
                  </button>
                ))}
            </div>
          </div>
        )}
        <div className="flex gap-2">
          <button
            type="button"
            onClick={savePreset}
            disabled={savingPreset}
            className="px-3 py-1.5 text-xs bg-deloitte-green text-white font-semibold rounded-lg disabled:opacity-50"
          >
            {editingPresetId ? 'Save changes' : 'Create preset'}
          </button>
          {editingPresetId && (
            <button
              type="button"
              onClick={cancelPresetEdit}
              className="px-3 py-1.5 text-xs bg-surface-800 border border-surface-700 text-surface-300 rounded-lg"
            >
              Cancel
            </button>
          )}
        </div>
      </div>

      <div className="max-h-96 overflow-auto border border-surface-700/40 rounded-xl">
        <table className="w-full text-xs">
          <thead className="bg-surface-900 text-surface-500 sticky top-0">
            <tr>
              <th className="px-3 py-2 text-left">Name</th>
              <th className="px-3 py-2 text-left">Model type</th>
              <th className="px-3 py-2 text-left">Candidate pool</th>
              <th className="px-3 py-2 text-left">Active</th>
              <th className="px-3 py-2 text-left">Actions</th>
            </tr>
          </thead>
          <tbody>
            {modelPresets.map((p) => (
              <tr key={p.id} className="border-t border-surface-700/40">
                <td className="px-3 py-1.5 font-medium">{p.name}</td>
                <td className="px-3 py-1.5 text-surface-400">{p.model_type}</td>
                <td className="px-3 py-1.5 text-surface-400">
                  {p.candidate_models?.length ? p.candidate_models.join(', ') : '—'}
                </td>
                <td className="px-3 py-1.5">{p.is_active ? 'Yes' : 'No'}</td>
                <td className="px-3 py-1.5">
                  <div className="flex gap-2">
                    <button
                      type="button"
                      onClick={() => startEditPreset(p)}
                      className="text-surface-400 hover:text-white"
                      aria-label={`Edit ${p.name}`}
                    >
                      <Pencil className="w-3.5 h-3.5" />
                    </button>
                    {p.is_active && (
                      <button
                        type="button"
                        onClick={() => handleDeactivatePreset(p)}
                        className="text-surface-400 hover:text-red-400"
                        aria-label={`Deactivate ${p.name}`}
                      >
                        <Trash2 className="w-3.5 h-3.5" />
                      </button>
                    )}
                  </div>
                </td>
              </tr>
            ))}
            {!modelPresets.length && (
              <tr>
                <td colSpan={5} className="px-3 py-4 text-surface-500 text-center">
                  No model presets yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
