import { useState, useCallback, useMemo } from 'react';
import {
  Send, Clock, CheckCircle, AlertTriangle,
  ChevronDown, ChevronUp, Plus, RefreshCw,
  Sparkles, TrendingUp, Filter, Download,
} from 'lucide-react';
import { apiPost } from '../../api/client';
import { Tabs } from '../ui/Tabs';
import { downloadCsv } from '../ui/DataTable';
import { usePanelStore } from '../../store/panelStore';

interface LineItemInput {
  id: number;
  name: string;
  category: string;
  account_code: string;
  model_suggested_value?: number;
  model_type?: string;
  confidence_score?: number;
  last_actual?: number;
}

interface Props {
  data: {
    panel_type: string;
    title: string;
    data: {
      version: Record<string, any>;
      inputs: any[];
      form_configs: any[];
      available_line_items: LineItemInput[];
      total_count: number;
    };
  };
}

function formatCurrency(val: number): string {
  if (Math.abs(val) >= 1_000_000) return `$${(val / 1_000_000).toFixed(1)}M`;
  if (Math.abs(val) >= 1_000) return `$${(val / 1_000).toFixed(0)}K`;
  return `$${val.toFixed(0)}`;
}

export function DriverInputPanel({ data }: Props) {
  const { version, inputs, form_configs, available_line_items } = data.data;
  const [activeTab, setActiveTab] = useState<'submit' | 'history'>('submit');
  const [formValues, setFormValues] = useState<Record<string, string>>({});
  const [submitting, setSubmitting] = useState(false);
  const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
  const [categoryFilter, setCategoryFilter] = useState<string>('all');
  const [useModelDefaults, setUseModelDefaults] = useState(false);

  // Get unique categories
  const categories = useMemo(() => {
    const cats = new Set(available_line_items.map((li) => li.category));
    return ['all', ...Array.from(cats).sort()];
  }, [available_line_items]);

  // Filter line items by category
  const filteredLineItems = useMemo(() => {
    if (categoryFilter === 'all') return available_line_items;
    return available_line_items.filter((li) => li.category === categoryFilter);
  }, [available_line_items, categoryFilter]);

  const handleFieldChange = useCallback((fieldName: string, value: string) => {
    setFormValues(prev => ({ ...prev, [fieldName]: value }));
  }, []);

  const handlePopulateFromModel = useCallback(() => {
    const defaults: Record<string, string> = {};
    available_line_items.forEach((li) => {
      if (li.model_suggested_value !== undefined && li.model_suggested_value !== null) {
        defaults[`li_${li.id}`] = String(li.model_suggested_value);
      }
    });
    setFormValues(prev => ({ ...prev, ...defaults }));
    setUseModelDefaults(true);
  }, [available_line_items]);

  const handleSubmit = useCallback(async () => {
    const entries = Object.entries(formValues).filter(([k, v]) => k !== '_notes' && v.trim());
    if (entries.length === 0) {
      setMessage({ type: 'error', text: 'Please fill in at least one field' });
      return;
    }
    setSubmitting(true);
    setMessage(null);
    try {
      // Build submission payload
      const values: Record<string, { value: number; source: string }> = {};
      for (const [key, val] of entries) {
        const liId = key.replace('li_', '');
        values[liId] = {
          value: parseFloat(val),
          source: useModelDefaults ? 'model_adjusted' : 'manual',
        };
      }
      await apiPost('/panel/driver-inputs/submit', {
        version_id: version.id,
        values,
        notes: formValues['_notes'] || '',
      });
      setMessage({ type: 'success', text: `${entries.length} driver inputs submitted successfully` });
      setFormValues({});
      setUseModelDefaults(false);
    } catch (err: any) {
      setMessage({ type: 'error', text: err.message || 'Submission failed' });
    } finally {
      setSubmitting(false);
    }
  }, [formValues, version.id, useModelDefaults]);

  const lineItemColumns = [
    { key: 'name', label: 'Line Item' },
    { key: 'category', label: 'Category' },
    { key: 'account_code', label: 'Account Code' },
    { key: 'model_suggested_value', label: 'Model Suggested Value' },
    { key: 'model_type', label: 'Model Type' },
    { key: 'confidence_score', label: 'Confidence Score' },
    { key: 'last_actual', label: 'Last Actual' },
  ];

  const historyColumns = [
    { key: 'business_unit', label: 'Business Unit' },
    { key: 'status', label: 'Status' },
    { key: 'is_late', label: 'Late' },
    { key: 'submitted_at', label: 'Submitted At' },
    { key: 'review_comments', label: 'Review Comments' },
  ];

  const handleExport = () => {
    const filename = `drivers_${version?.name || 'export'}`;
    if (activeTab === 'history') {
      downloadCsv(
        filename,
        historyColumns,
        inputs.map((inp: any) => ({
          business_unit: inp.business_unit,
          status: inp.status,
          is_late: inp.is_late ? 'yes' : 'no',
          submitted_at: inp.submitted_at || '',
          review_comments: inp.review_comments || '',
        })) as Record<string, unknown>[],
      );
    } else {
      downloadCsv(filename, lineItemColumns, available_line_items as unknown as Record<string, unknown>[]);
    }
  };

  return (
    <div className="space-y-4">
      {/* Header */}
      <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3">
        <div className="flex items-center justify-between">
          <div>
            <h4 className="text-xs font-semibold text-white">{version.name}</h4>
            <p className="text-xs text-surface-500 mt-0.5">{version.status} • {inputs.length} submissions</p>
          </div>
          <div className="flex items-center gap-1.5">
            <button
              type="button"
              onClick={() => usePanelStore.getState().openPanel('approvals', { version_id: version.id })}
              className={`px-2 py-1 text-xs font-medium rounded-md border cursor-pointer hover:opacity-90 ${
                version.status === 'draft' ? 'bg-blue-500/10 border-blue-500/20 text-blue-400'
                : version.status === 'in_review' ? 'bg-amber-500/10 border-amber-500/20 text-amber-400'
                : 'bg-deloitte-green/10 border-deloitte-green/20 text-deloitte-green'
              }`}
              title="Open approvals"
            >
              {version.status}
            </button>
            <button
              type="button"
              onClick={handleExport}
              className="inline-flex items-center gap-1.5 text-xs text-surface-300 hover:text-white px-2 py-1 rounded-md border border-surface-600 hover:border-deloitte-green/40"
              title="Export drivers CSV"
            >
              <Download className="w-3.5 h-3.5" />
              CSV
            </button>
          </div>
        </div>
      </div>

      {/* Tabs */}
      <Tabs
        tabs={[
          { id: 'submit', label: 'Submit Inputs' },
          { id: 'history', label: 'Submission History' },
        ]}
        value={activeTab}
        onChange={setActiveTab}
      />

      {message && (
        <div className={`px-3 py-2 rounded-lg text-xs flex items-center gap-2 ${
          message.type === 'success' ? 'bg-deloitte-green/10 border border-deloitte-green/20 text-deloitte-green' : 'bg-red-500/10 border border-red-500/20 text-red-400'
        }`}>
          {message.type === 'success' ? <CheckCircle className="w-3.5 h-3.5" /> : <AlertTriangle className="w-3.5 h-3.5" />}
          {message.text}
        </div>
      )}

      {activeTab === 'submit' && (
        <div className="space-y-3">
          {/* Controls bar */}
          <div className="flex items-center justify-between gap-2">
            <div className="flex items-center gap-2">
              <Filter className="w-3.5 h-3.5 text-surface-500" />
              <select
                value={categoryFilter}
                onChange={(e) => setCategoryFilter(e.target.value)}
                className="px-2.5 py-1 bg-surface-800/60 border border-surface-700/50 rounded-md text-xs text-white focus:outline-none focus:border-deloitte-green/50 transition-all"
              >
                {categories.map((cat) => (
                  <option key={cat} value={cat}>{cat === 'all' ? 'All Categories' : cat}</option>
                ))}
              </select>
              <span className="text-xs text-surface-500">{filteredLineItems.length} items</span>
            </div>
            <button
              onClick={handlePopulateFromModel}
              className="flex items-center gap-1 px-2.5 py-1 bg-deloitte-teal/10 border border-deloitte-teal/20 text-deloitte-teal-light text-xs font-medium rounded-md hover:bg-deloitte-teal/20 transition-all"
            >
              <Sparkles className="w-3 h-3" />
              Use Model Suggestions
            </button>
          </div>

          {/* Available line items as input fields */}
          <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl overflow-hidden">
            <div className="px-4 py-2.5 border-b border-surface-700/50">
              <h4 className="text-xs font-semibold text-white">Driver Assumptions</h4>
              <p className="text-xs text-surface-500 mt-0.5">Enter your BU assumptions for each driver. Model suggestions shown for reference.</p>
            </div>
            <div className="max-h-[350px] overflow-y-auto p-3 space-y-2">
              {filteredLineItems.slice(0, 30).map((li: LineItemInput) => (
                <div key={li.id} className="bg-surface-800/40 border border-surface-700/30 rounded-lg p-3">
                  <div className="flex items-center justify-between mb-1.5">
                    <div>
                      <span className="text-xs font-medium text-surface-200">{li.name}</span>
                      <span className="ml-2 text-xs text-surface-500">{li.category}</span>
                    </div>
                    <span className="text-xs text-surface-500 font-mono">{li.account_code}</span>
                  </div>
                  <div className="flex items-center gap-2">
                    <div className="flex-1">
                      <input
                        type="number"
                        placeholder="Enter value..."
                        value={formValues[`li_${li.id}`] || ''}
                        onChange={(e) => handleFieldChange(`li_${li.id}`, e.target.value)}
                        className="w-full px-2.5 py-1.5 bg-surface-900/60 border border-surface-700/50 rounded-md text-xs text-white placeholder:text-surface-600 focus:outline-none focus:border-deloitte-green/50 focus:ring-1 focus:ring-deloitte-green/20 transition-all"
                      />
                    </div>
                    <div className="text-right min-w-[100px]">
                      {li.model_suggested_value !== undefined && li.model_suggested_value !== null ? (
                        <div>
                          <div className="text-xs text-surface-500">
                            Model: <span className="text-deloitte-teal-light font-mono font-medium">{formatCurrency(li.model_suggested_value)}</span>
                          </div>
                          <div className="flex items-center gap-1 justify-end">
                            {li.model_type && (
                              <span className="text-xs text-surface-500 bg-surface-700/40 px-1 py-0.5 rounded">{li.model_type}</span>
                            )}
                            {li.confidence_score !== undefined && (
                              <span className={`text-xs px-1 py-0.5 rounded font-medium ${
                                li.confidence_score >= 70 ? 'bg-deloitte-green/15 text-deloitte-green'
                                : li.confidence_score >= 50 ? 'bg-amber-500/15 text-amber-400'
                                : 'bg-red-500/15 text-red-400'
                              }`}>{Math.round(li.confidence_score)}</span>
                            )}
                          </div>
                        </div>
                      ) : (
                        <span className="text-xs text-surface-600">No model data</span>
                      )}
                    </div>
                  </div>
                  {li.last_actual !== undefined && li.last_actual !== null && (
                    <div className="mt-1 flex items-center gap-1 text-xs text-surface-500">
                      <TrendingUp className="w-2.5 h-2.5" />
                      Last actual: <span className="font-mono text-surface-400">{formatCurrency(li.last_actual)}</span>
                    </div>
                  )}
                </div>
              ))}
              {filteredLineItems.length === 0 && (
                <p className="text-surface-500 text-xs text-center py-6">No line items available for input</p>
              )}
              {filteredLineItems.length > 30 && (
                <p className="text-surface-500 text-xs text-center py-2">
                  Showing first 30 of {filteredLineItems.length} items. Use category filter to narrow down.
                </p>
              )}
            </div>
          </div>

          {/* Notes & submit */}
          <div className="flex items-center gap-2">
            <input
              type="text"
              placeholder="Add notes (optional)..."
              value={formValues['_notes'] || ''}
              onChange={(e) => handleFieldChange('_notes', e.target.value)}
              className="flex-1 px-3 py-2 bg-surface-800/60 border border-surface-700/50 rounded-lg text-xs text-white placeholder:text-surface-600 focus:outline-none focus:border-deloitte-green/50 transition-all"
            />
            <button
              onClick={handleSubmit}
              disabled={submitting}
              className="px-4 py-2 bg-deloitte-green/20 hover:bg-deloitte-green/30 border border-deloitte-green/30 text-deloitte-green text-xs font-medium rounded-lg transition-all flex items-center gap-1.5 disabled:opacity-50"
            >
              {submitting ? <RefreshCw className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
              Submit
            </button>
          </div>
        </div>
      )}

      {activeTab === 'history' && (
        <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl overflow-hidden">
          <div className="max-h-[400px] overflow-y-auto">
            {inputs.length > 0 ? (
              <div className="space-y-2 p-3">
                {inputs.map((inp: any, i: number) => (
                  <div key={i} className="bg-surface-800/40 border border-surface-700/30 rounded-lg p-3">
                    <div className="flex items-center justify-between mb-2">
                      <span className="text-xs font-medium text-white">{inp.business_unit}</span>
                      <StatusBadge status={inp.status} isLate={inp.is_late} />
                    </div>
                    <div className="flex items-center justify-between text-xs text-surface-500">
                      <span className="flex items-center gap-1">
                        <Clock className="w-3 h-3" />
                        {inp.submitted_at ? new Date(inp.submitted_at).toLocaleDateString() : 'N/A'}
                      </span>
                      {inp.review_comments && (
                        <span className="text-surface-400 italic max-w-[150px] truncate">{inp.review_comments}</span>
                      )}
                    </div>
                    {inp.values && typeof inp.values === 'object' && (
                      <div className="mt-2 pt-2 border-t border-surface-700/30">
                        {Object.entries(inp.values).slice(0, 3).map(([key, val]: [string, any]) => (
                          <div key={key} className="flex justify-between text-xs py-0.5">
                            <span className="text-surface-400">{key}</span>
                            <span className="text-surface-300 font-mono">{typeof val === 'object' ? val?.value || JSON.stringify(val) : val}</span>
                          </div>
                        ))}
                        {Object.keys(inp.values).length > 3 && (
                          <p className="text-xs text-surface-500 mt-1">+{Object.keys(inp.values).length - 3} more fields</p>
                        )}
                      </div>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-surface-500 text-xs text-center py-8">No submissions yet</p>
            )}
          </div>
        </div>
      )}
    </div>
  );
}

function StatusBadge({ status, isLate }: { status: string; isLate: boolean }) {
  const styles =
    status === 'approved' ? 'bg-deloitte-green/15 text-deloitte-green border-deloitte-green/20'
    : status === 'rejected' ? 'bg-red-500/15 text-red-400 border-red-500/20'
    : status === 'submitted' ? 'bg-blue-500/15 text-blue-400 border-blue-500/20'
    : 'bg-surface-700 text-surface-400 border-surface-600';

  return (
    <div className="flex items-center gap-1">
      {isLate && (
        <span className="px-1.5 py-0.5 text-xs font-medium rounded bg-red-500/15 text-red-400 border border-red-500/20">LATE</span>
      )}
      <span className={`px-1.5 py-0.5 text-xs font-medium rounded border ${styles}`}>{status}</span>
    </div>
  );
}
