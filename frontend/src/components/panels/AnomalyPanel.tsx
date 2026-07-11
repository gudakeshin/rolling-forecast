import { useState, useMemo, useCallback } from 'react';
import {
  LineChart, Line, AreaChart, Area,
  XAxis, YAxis, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell,
} from 'recharts';
import {
  AlertTriangle, AlertOctagon, Info, Shield,
  ChevronDown, ChevronUp, Filter, TrendingUp,
  TrendingDown, Activity, Zap, Search, Eye, EyeOff,
  ArrowRight, Edit3, Save, X, Loader2,
  MessageSquare, GitBranch, Clock, Target,
  ChevronRight, BarChart3, Clipboard,
} from 'lucide-react';
import { apiPost } from '../../api/client';
import { usePanelStore } from '../../store/panelStore';

// ─── Design tokens ─────────────────────────────────
const COLORS = {
  green: '#86BC25',
  amber: '#FFB547',
  red: '#E84855',
  teal: '#0076A8',
  coolGray: '#97999B',
  blue: '#62B5E5',
};

const SEVERITY_COLORS: Record<string, string> = {
  critical: COLORS.red,
  warning: COLORS.amber,
  info: COLORS.teal,
};

const SEVERITY_BG: Record<string, string> = {
  critical: 'bg-red-500/10 border-red-500/30 text-red-400',
  warning: 'bg-amber-500/10 border-amber-500/30 text-amber-400',
  info: 'bg-sky-500/10 border-sky-500/30 text-sky-400',
};

const TYPE_LABELS: Record<string, string> = {
  forecast_jump: 'Forecast vs Actuals',
  forecast_outlier: 'Forecast Outlier',
  actuals_outlier: 'Actuals Outlier',
  trend_break: 'Trend Break',
  high_uncertainty: 'High Uncertainty',
};

// ─── Types ─────────────────────────────────────────
interface Finding {
  type: string;
  severity: string;
  score: number;
  headline: string;
  detail: string;
  period: string;
  value: number;
  expected: number;
}

interface AnomalyAction {
  type: string;
  label: string;
  detail: string;
}

interface SparklinePoint {
  period: string;
  actual?: number;
  forecast?: number;
}

interface AnomalyItem {
  id: string;
  line_item_id: number;
  line_item_name: string;
  account_code: string;
  category: string;
  business_unit: string;
  materiality: string;
  materiality_pct: number;
  worst_severity: string;
  composite_score: number;
  findings: Finding[];
  actions: AnomalyAction[];
  sparkline: SparklinePoint[];
  total_p50: number;
  avg_p50: number;
  model_type: string;
  is_dismissed: boolean;
}

interface TypeChartItem {
  type: string;
  count: number;
}

interface CategoryChartItem {
  category: string;
  critical: number;
  warning: number;
  info: number;
  total_value: number;
}

interface AnomalyData {
  version: { id: string; name: string; status: string };
  anomalies: AnomalyItem[];
  summary: {
    total_anomalies: number;
    critical_count: number;
    warning_count: number;
    info_count: number;
    critical_value: number;
    warning_value: number;
    total_line_items: number;
  };
  category_chart: CategoryChartItem[];
  type_chart: TypeChartItem[];
  available_categories: string[];
}

// ─── Utility ───────────────────────────────────────
function formatCurrency(v: number): string {
  if (Math.abs(v) >= 1_000_000) return `$${(v / 1_000_000).toFixed(1)}M`;
  if (Math.abs(v) >= 1_000) return `$${(v / 1_000).toFixed(0)}K`;
  return `$${v.toFixed(0)}`;
}

function SeverityIcon({ severity }: { severity: string }) {
  if (severity === 'critical') return <AlertOctagon className="w-4 h-4 text-red-400" />;
  if (severity === 'warning') return <AlertTriangle className="w-4 h-4 text-amber-400" />;
  return <Info className="w-4 h-4 text-sky-400" />;
}

function MaterialityPill({ level, pct }: { level: string; pct: number }) {
  const bg = level === 'high' ? 'bg-red-500/15 text-red-300' :
    level === 'medium' ? 'bg-amber-500/15 text-amber-300' :
      'bg-surface-700 text-surface-400';
  return (
    <span className={`text-xs px-1.5 py-0.5 rounded-full font-medium ${bg}`}>
      {pct.toFixed(1)}%
    </span>
  );
}

function FindingTypeBadge({ type }: { type: string }) {
  const label = TYPE_LABELS[type] || type;
  return (
    <span className="text-xs px-1.5 py-0.5 rounded bg-surface-700/80 text-surface-300 font-mono">
      {label}
    </span>
  );
}

// ─── Summary Header ────────────────────────────────
function AnomalySummary({ summary, typeChart }: { summary: AnomalyData['summary']; typeChart: TypeChartItem[] }) {
  return (
    <div className="mb-4 space-y-3">
      {/* KPI row */}
      <div className="grid grid-cols-4 gap-2">
        <div className="bg-surface-800 rounded-lg p-3 border border-surface-700/50">
          <div className="text-xs text-surface-500 uppercase tracking-wider mb-1">Total Issues</div>
          <div className="text-xl font-bold text-white">{summary.total_anomalies}</div>
          <div className="text-xs text-surface-500">across {summary.total_line_items} line items</div>
        </div>
        <div className="bg-surface-800 rounded-lg p-3 border border-red-500/20">
          <div className="text-xs text-red-400 uppercase tracking-wider mb-1">Critical</div>
          <div className="text-xl font-bold text-red-400">{summary.critical_count}</div>
          <div className="text-xs text-surface-500">{formatCurrency(summary.critical_value)} at risk</div>
        </div>
        <div className="bg-surface-800 rounded-lg p-3 border border-amber-500/20">
          <div className="text-xs text-amber-400 uppercase tracking-wider mb-1">Warning</div>
          <div className="text-xl font-bold text-amber-400">{summary.warning_count}</div>
          <div className="text-xs text-surface-500">{formatCurrency(summary.warning_value)} to review</div>
        </div>
        <div className="bg-surface-800 rounded-lg p-3 border border-sky-500/20">
          <div className="text-xs text-sky-400 uppercase tracking-wider mb-1">Info</div>
          <div className="text-xl font-bold text-sky-400">{summary.info_count}</div>
          <div className="text-xs text-surface-500">low priority</div>
        </div>
      </div>

      {/* Issue types breakdown */}
      {typeChart.length > 0 && (
        <div className="bg-surface-800 rounded-lg p-3 border border-surface-700/50">
          <div className="text-xs text-surface-500 uppercase tracking-wider mb-2">Issue Type Distribution</div>
          <div className="space-y-1.5">
            {typeChart.map((t) => {
              const maxCount = Math.max(...typeChart.map(x => x.count));
              const pct = maxCount > 0 ? (t.count / maxCount) * 100 : 0;
              return (
                <div key={t.type} className="flex items-center gap-2">
                  <div className="text-xs text-surface-400 w-32 truncate">{t.type}</div>
                  <div className="flex-1 h-2 bg-surface-700 rounded-full overflow-hidden">
                    <div
                      className="h-full rounded-full bg-deloitte-green/70"
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                  <span className="text-xs text-surface-400 w-6 text-right">{t.count}</span>
                </div>
              );
            })}
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Filters bar ───────────────────────────────────
interface FilterState {
  severity: string;
  category: string;
  type: string;
  materiality: string;
  search: string;
  showDismissed: boolean;
}

function FilterBar({
  filters,
  onChange,
  categories,
  totalCount,
  filteredCount,
}: {
  filters: FilterState;
  onChange: (f: FilterState) => void;
  categories: string[];
  totalCount: number;
  filteredCount: number;
}) {
  return (
    <div className="mb-3 space-y-2">
      {/* Search + toggle */}
      <div className="flex items-center gap-2">
        <div className="flex-1 relative">
          <Search className="absolute left-2 top-1/2 -translate-y-1/2 w-3.5 h-3.5 text-surface-500" />
          <input
            type="text"
            placeholder="Search line items..."
            value={filters.search}
            onChange={(e) => onChange({ ...filters, search: e.target.value })}
            className="w-full pl-7 pr-3 py-1.5 text-xs bg-surface-800 border border-surface-700 rounded-lg text-white placeholder-surface-500 focus:border-deloitte-green/50 focus:outline-none"
          />
        </div>
        <button
          onClick={() => onChange({ ...filters, showDismissed: !filters.showDismissed })}
          className={`flex items-center gap-1 px-2 py-1.5 text-xs rounded-lg border transition-colors ${filters.showDismissed
            ? 'bg-surface-700 border-surface-600 text-white'
            : 'bg-surface-800 border-surface-700 text-surface-500 hover:text-white'
            }`}
        >
          {filters.showDismissed ? <Eye className="w-3 h-3" /> : <EyeOff className="w-3 h-3" />}
          Dismissed
        </button>
      </div>

      {/* Filter chips */}
      <div className="flex flex-wrap gap-1.5">
        <FilterChip
          label="All"
          active={filters.severity === 'all'}
          onClick={() => onChange({ ...filters, severity: 'all' })}
        />
        <FilterChip
          label="Critical"
          active={filters.severity === 'critical'}
          onClick={() => onChange({ ...filters, severity: 'critical' })}
          color="text-red-400"
        />
        <FilterChip
          label="Warning"
          active={filters.severity === 'warning'}
          onClick={() => onChange({ ...filters, severity: 'warning' })}
          color="text-amber-400"
        />
        <FilterChip
          label="Info"
          active={filters.severity === 'info'}
          onClick={() => onChange({ ...filters, severity: 'info' })}
          color="text-sky-400"
        />

        <div className="w-px h-5 bg-surface-700 mx-1 self-center" />

        <select
          value={filters.category}
          onChange={(e) => onChange({ ...filters, category: e.target.value })}
          className="text-xs bg-surface-800 border border-surface-700 rounded-md px-2 py-1 text-surface-300 focus:outline-none focus:border-deloitte-green/50"
        >
          <option value="all">All Categories</option>
          {categories.map(c => <option key={c} value={c}>{c}</option>)}
        </select>

        <select
          value={filters.materiality}
          onChange={(e) => onChange({ ...filters, materiality: e.target.value })}
          className="text-xs bg-surface-800 border border-surface-700 rounded-md px-2 py-1 text-surface-300 focus:outline-none focus:border-deloitte-green/50"
        >
          <option value="all">All Materiality</option>
          <option value="high">High Impact</option>
          <option value="medium">Medium Impact</option>
          <option value="low">Low Impact</option>
        </select>

        <select
          value={filters.type}
          onChange={(e) => onChange({ ...filters, type: e.target.value })}
          className="text-xs bg-surface-800 border border-surface-700 rounded-md px-2 py-1 text-surface-300 focus:outline-none focus:border-deloitte-green/50"
        >
          <option value="all">All Types</option>
          <option value="forecast_jump">Forecast vs Actuals</option>
          <option value="forecast_outlier">Forecast Outlier</option>
          <option value="actuals_outlier">Actuals Outlier</option>
          <option value="trend_break">Trend Break</option>
          <option value="high_uncertainty">High Uncertainty</option>
        </select>
      </div>

      {filteredCount < totalCount && (
        <div className="text-xs text-surface-500">
          Showing {filteredCount} of {totalCount} anomalies
        </div>
      )}
    </div>
  );
}

function FilterChip({ label, active, onClick, color }: { label: string; active: boolean; onClick: () => void; color?: string }) {
  return (
    <button
      type="button"
      onClick={onClick}
      className={`text-xs px-2 py-1 rounded-full border transition-colors font-medium ${active
        ? 'bg-deloitte-green/15 border-deloitte-green/40 text-deloitte-green'
        : `bg-surface-800 border-surface-700 ${color || 'text-surface-400'} hover:border-surface-600`
        }`}
    >
      {label}
    </button>
  );
}

// ─── Sparkline component ───────────────────────────
function MiniSparkline({ data }: { data: SparklinePoint[] }) {
  if (!data || data.length === 0) return null;
  return (
    <div className="h-10 w-full">
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 2, right: 2, bottom: 0, left: 2 }}>
          <defs>
            <linearGradient id="anomSparkActual" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={COLORS.coolGray} stopOpacity={0.3} />
              <stop offset="95%" stopColor={COLORS.coolGray} stopOpacity={0} />
            </linearGradient>
            <linearGradient id="anomSparkForecast" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor={COLORS.teal} stopOpacity={0.3} />
              <stop offset="95%" stopColor={COLORS.teal} stopOpacity={0} />
            </linearGradient>
          </defs>
          <Area type="monotone" dataKey="actual" stroke={COLORS.coolGray} fill="url(#anomSparkActual)" strokeWidth={1.5} dot={false} />
          <Area type="monotone" dataKey="forecast" stroke={COLORS.teal} fill="url(#anomSparkForecast)" strokeWidth={1.5} strokeDasharray="3 3" dot={false} />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  );
}

// ─── Anomaly Item Row ──────────────────────────────
function AnomalyRow({
  item,
  isExpanded,
  onToggle,
  onDismiss,
  onOverride,
}: {
  item: AnomalyItem;
  isExpanded: boolean;
  onToggle: () => void;
  onDismiss: (id: string) => void;
  onOverride: (item: AnomalyItem) => void;
}) {
  const [editMode, setEditMode] = useState(false);
  const [editValue, setEditValue] = useState('');
  const [editReason, setEditReason] = useState('');
  const [isSaving, setIsSaving] = useState(false);
  const [overrideApplied, setOverrideApplied] = useState(false);

  const topFinding = item.findings[0];
  const actionCopied = useCallback((text: string) => {
    navigator.clipboard.writeText(text);
  }, []);

  const handleSaveOverride = async () => {
    if (!editValue || editReason.length < 10) return;
    setIsSaving(true);
    try {
      await apiPost('/panel/inline-override', {
        result_id: item.id,
        new_value: parseFloat(editValue),
        reason: editReason,
        apply_to: 'all',
      });
      setOverrideApplied(true);
      setEditMode(false);
    } catch (err) {
      console.error('Override failed:', err);
    } finally {
      setIsSaving(false);
    }
  };

  return (
    <div className={`border rounded-lg transition-colors ${item.is_dismissed
      ? 'bg-surface-800/40 border-surface-700/30 opacity-60'
      : `bg-surface-800 ${isExpanded ? 'border-surface-600' : 'border-surface-700/50 hover:border-surface-600'}`
      }`}>
      {/* Compact row */}
      <button
        onClick={onToggle}
        className="w-full flex items-center gap-3 px-3 py-2.5 text-left"
      >
        {/* Severity indicator */}
        <div className="flex-shrink-0">
          <SeverityIcon severity={item.worst_severity} />
        </div>

        {/* Name + headline */}
        <div className="flex-1 min-w-0">
          <div className="flex items-center gap-1.5">
            <span className="text-xs font-medium text-white truncate">{item.line_item_name}</span>
            <MaterialityPill level={item.materiality} pct={item.materiality_pct} />
          </div>
          <div className="text-xs text-surface-400 truncate mt-0.5">
            {topFinding.headline}
          </div>
        </div>

        {/* Sparkline */}
        <div className="w-20 flex-shrink-0 hidden sm:block">
          <MiniSparkline data={item.sparkline} />
        </div>

        {/* Value */}
        <div className="text-right flex-shrink-0 w-16">
          <div className={`text-xs font-mono ${overrideApplied ? 'text-cyan-400' : 'text-white'}`}>
            {formatCurrency(item.avg_p50)}
          </div>
          <div className="text-xs text-surface-500">{item.category}</div>
        </div>

        {/* Expand icon */}
        <div className="flex-shrink-0 text-surface-500">
          {isExpanded ? <ChevronUp className="w-3.5 h-3.5" /> : <ChevronDown className="w-3.5 h-3.5" />}
        </div>
      </button>

      {/* Expanded detail */}
      {isExpanded && (
        <div className="px-3 pb-3 border-t border-surface-700/50 mt-0">
          {/* Findings */}
          <div className="mt-3 space-y-2">
            <div className="text-xs text-surface-500 uppercase tracking-wider font-semibold flex items-center gap-1">
              <Zap className="w-3 h-3" /> AI Findings
            </div>
            {item.findings.map((f, i) => (
              <div key={i} className={`p-2.5 rounded-lg border ${SEVERITY_BG[f.severity]} bg-opacity-50`}>
                <div className="flex items-center gap-2 mb-1">
                  <SeverityIcon severity={f.severity} />
                  <span className="text-xs font-medium">{f.headline}</span>
                  <FindingTypeBadge type={f.type} />
                </div>
                <p className="text-xs opacity-80 leading-relaxed">{f.detail}</p>
                <div className="flex items-center gap-3 mt-1.5 text-xs opacity-60">
                  <span>Period: {f.period}</span>
                  <span>Value: {formatCurrency(f.value)}</span>
                  <span>Expected: {formatCurrency(f.expected)}</span>
                  <span>Score: {f.score.toFixed(1)}</span>
                </div>
              </div>
            ))}
          </div>

          {/* Sparkline full */}
          {item.sparkline.length > 0 && (
            <div className="mt-3">
              <div className="text-xs text-surface-500 uppercase tracking-wider font-semibold flex items-center gap-1 mb-1">
                <Activity className="w-3 h-3" /> Trend Context
              </div>
              <div className="h-24 bg-surface-900/50 rounded-lg p-2">
                <ResponsiveContainer width="100%" height="100%">
                  <AreaChart data={item.sparkline} margin={{ top: 4, right: 4, bottom: 4, left: 4 }}>
                    <defs>
                      <linearGradient id={`anomGradActual-${item.id}`} x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor={COLORS.coolGray} stopOpacity={0.4} />
                        <stop offset="95%" stopColor={COLORS.coolGray} stopOpacity={0} />
                      </linearGradient>
                      <linearGradient id={`anomGradForecast-${item.id}`} x1="0" y1="0" x2="0" y2="1">
                        <stop offset="5%" stopColor={COLORS.teal} stopOpacity={0.4} />
                        <stop offset="95%" stopColor={COLORS.teal} stopOpacity={0} />
                      </linearGradient>
                    </defs>
                    <XAxis dataKey="period" tick={{ fontSize: 12, fill: '#666' }} tickLine={false} axisLine={false} />
                    <YAxis tick={{ fontSize: 12, fill: '#666' }} tickLine={false} axisLine={false} width={40} tickFormatter={(v) => formatCurrency(v)} />
                    <Tooltip
                      contentStyle={{ background: '#1a1a2e', border: '1px solid #333', borderRadius: 8, fontSize: 12 }}
                      labelStyle={{ color: '#fff', marginBottom: 4 }}
                      formatter={(v: number, name: string) => [formatCurrency(v), name === 'actual' ? 'Actual' : 'Forecast']}
                    />
                    <Area type="monotone" dataKey="actual" stroke={COLORS.coolGray} fill={`url(#anomGradActual-${item.id})`} strokeWidth={1.5} dot={false} />
                    <Area type="monotone" dataKey="forecast" stroke={COLORS.teal} fill={`url(#anomGradForecast-${item.id})`} strokeWidth={1.5} strokeDasharray="4 3" dot={false} />
                  </AreaChart>
                </ResponsiveContainer>
              </div>
              <div className="flex items-center gap-4 mt-1 text-xs text-surface-500">
                <span className="flex items-center gap-1">
                  <span className="w-3 h-0.5 bg-surface-400 rounded-full inline-block" /> Actuals
                </span>
                <span className="flex items-center gap-1">
                  <span className="w-3 h-0.5 bg-sky-500 rounded-full inline-block border-dashed" /> Forecast
                </span>
              </div>
            </div>
          )}

          {/* Inline override */}
          <div className="mt-3">
            <div className="text-xs text-surface-500 uppercase tracking-wider font-semibold flex items-center gap-1 mb-1.5">
              <Edit3 className="w-3 h-3" /> Override Forecast
            </div>
            {!editMode ? (
              <button
                onClick={() => {
                  setEditValue(item.avg_p50.toFixed(2));
                  setEditReason('');
                  setEditMode(true);
                }}
                className="flex items-center gap-1.5 text-xs text-deloitte-green hover:text-deloitte-green/80 transition-colors"
              >
                <Edit3 className="w-3 h-3" />
                Adjust the forecast value for this line item
              </button>
            ) : (
              <div className="space-y-1.5">
                <div className="flex items-center gap-2">
                  <input
                    type="number"
                    value={editValue}
                    onChange={(e) => setEditValue(e.target.value)}
                    className="w-28 px-2 py-1 text-xs bg-surface-900 border border-surface-600 rounded text-white font-mono focus:border-deloitte-green focus:outline-none"
                  />
                  <input
                    type="text"
                    value={editReason}
                    onChange={(e) => setEditReason(e.target.value)}
                    placeholder="Reason for adjustment (min 10 chars)"
                    className="flex-1 px-2 py-1 text-xs bg-surface-900 border border-surface-600 rounded text-white placeholder-surface-600 focus:border-deloitte-green focus:outline-none"
                  />
                </div>
                <div className="flex items-center gap-2">
                  <button
                    onClick={handleSaveOverride}
                    disabled={isSaving || !editValue || editReason.length < 10}
                    className="flex items-center gap-1 px-2 py-1 text-xs bg-deloitte-green text-surface-900 rounded font-medium hover:bg-deloitte-green/80 disabled:opacity-40 disabled:cursor-not-allowed transition-colors"
                  >
                    {isSaving ? <Loader2 className="w-3 h-3 animate-spin" /> : <Save className="w-3 h-3" />}
                    Save Override
                  </button>
                  <button
                    onClick={() => setEditMode(false)}
                    className="flex items-center gap-1 px-2 py-1 text-xs text-surface-400 hover:text-white rounded border border-surface-700 hover:border-surface-600 transition-colors"
                  >
                    <X className="w-3 h-3" /> Cancel
                  </button>
                  {editReason.length > 0 && editReason.length < 10 && (
                    <span className="text-xs text-amber-400">{10 - editReason.length} more chars</span>
                  )}
                </div>
              </div>
            )}
          </div>

          {/* Recommended actions */}
          <div className="mt-3">
            <div className="text-xs text-surface-500 uppercase tracking-wider font-semibold flex items-center gap-1 mb-1.5">
              <Target className="w-3 h-3" /> Recommended Actions
            </div>
            <div className="space-y-1">
              {item.actions.map((action, i) => (
                <ActionButton
                  key={i}
                  action={action}
                  item={item}
                  onDismiss={onDismiss}
                  onOverride={onOverride}
                  onCopy={actionCopied}
                />
              ))}
            </div>
          </div>

          {/* Meta info */}
          <div className="mt-3 flex flex-wrap items-center gap-3 text-xs text-surface-500">
            <span className="flex items-center gap-1"><BarChart3 className="w-3 h-3" /> Model: {item.model_type || 'auto'}</span>
            <span className="flex items-center gap-1"><Clock className="w-3 h-3" /> {item.account_code}</span>
            {item.business_unit && (
              <span className="flex items-center gap-1"><GitBranch className="w-3 h-3" /> BU: {item.business_unit}</span>
            )}
            <span>Composite score: {item.composite_score}</span>
          </div>
        </div>
      )}
    </div>
  );
}

// ─── Action Buttons ────────────────────────────────
function ActionButton({
  action,
  item,
  onDismiss,
  onOverride,
  onCopy,
}: {
  action: AnomalyAction;
  item: AnomalyItem;
  onDismiss: (id: string) => void;
  onOverride: (item: AnomalyItem) => void;
  onCopy: (text: string) => void;
}) {
  const openPanel = usePanelStore((s) => s.openPanel);

  const handleClick = async () => {
    switch (action.type) {
      case 'dismiss':
        onDismiss(item.id);
        return;
      case 'override':
        onOverride(item);
        return;
      case 'confirm_zero':
        onDismiss(item.id);
        return;
      case 'investigate':
        onCopy(`Investigate anomaly for ${item.line_item_name}: ${action.detail}`);
        openPanel('anomaly_dashboard', {});
        return;
      default:
        break;
    }

    const { dispatchRecommendedAction } = await import('../../utils/recommendedActions');
    await dispatchRecommendedAction(
      { type: action.type, label: action.label, detail: action.detail },
      { openPanel, lineItemName: item.line_item_name },
    );
  };

  const iconMap: Record<string, any> = {
    investigate: Search,
    override: Edit3,
    driver_input: MessageSquare,
    dismiss: EyeOff,
    upload_data: ArrowRight,
    confirm_zero: Target,
  };

  const colorMap: Record<string, string> = {
    investigate: 'text-sky-400 border-sky-500/30 hover:bg-sky-500/10',
    override: 'text-amber-400 border-amber-500/30 hover:bg-amber-500/10',
    driver_input: 'text-deloitte-green border-deloitte-green/30 hover:bg-deloitte-green/10',
    dismiss: 'text-surface-500 border-surface-700 hover:bg-surface-700/50',
    upload_data: 'text-violet-400 border-violet-500/30 hover:bg-violet-500/10',
    confirm_zero: 'text-deloitte-green border-deloitte-green/30 hover:bg-deloitte-green/10',
  };

  const Icon = iconMap[action.type] || ArrowRight;
  const colorClass = colorMap[action.type] || 'text-surface-400 border-surface-700 hover:bg-surface-700/50';

  return (
    <button
      onClick={handleClick}
      className={`w-full flex items-center gap-2 px-2.5 py-2 rounded-lg border text-left transition-colors ${colorClass}`}
    >
      <Icon className="w-3.5 h-3.5 flex-shrink-0" />
      <div className="flex-1 min-w-0">
        <div className="text-xs font-medium">{action.label}</div>
        <div className="text-xs opacity-60 truncate">{action.detail}</div>
      </div>
      <ChevronRight className="w-3 h-3 opacity-40 flex-shrink-0" />
    </button>
  );
}

// ─── Main Panel ────────────────────────────────────
export function AnomalyPanel({ data }: { data: any }) {
  const panelData: AnomalyData = data?.data || data;
  const anomalies = panelData?.anomalies || [];
  const summary = panelData?.summary || {
    total_anomalies: 0, critical_count: 0, warning_count: 0, info_count: 0,
    critical_value: 0, warning_value: 0, total_line_items: 0,
  };
  const typeChart = panelData?.type_chart || [];
  const categories = panelData?.available_categories || [];

  const [expandedId, setExpandedId] = useState<string | null>(null);
  const [dismissedIds, setDismissedIds] = useState<Set<string>>(new Set());
  const [filters, setFilters] = useState<FilterState>({
    severity: 'all',
    category: 'all',
    type: 'all',
    materiality: 'all',
    search: '',
    showDismissed: false,
  });

  const filteredAnomalies = useMemo(() => {
    return anomalies.filter((a: AnomalyItem) => {
      if (!filters.showDismissed && dismissedIds.has(a.id)) return false;
      if (filters.severity !== 'all' && a.worst_severity !== filters.severity) return false;
      if (filters.category !== 'all' && a.category !== filters.category) return false;
      if (filters.materiality !== 'all' && a.materiality !== filters.materiality) return false;
      if (filters.type !== 'all') {
        const hasType = a.findings.some((f: Finding) => f.type === filters.type);
        if (!hasType) return false;
      }
      if (filters.search) {
        const q = filters.search.toLowerCase();
        const match = a.line_item_name.toLowerCase().includes(q) ||
          a.category.toLowerCase().includes(q) ||
          a.account_code.toLowerCase().includes(q) ||
          (a.business_unit || '').toLowerCase().includes(q);
        if (!match) return false;
      }
      return true;
    });
  }, [anomalies, filters, dismissedIds]);

  const handleDismiss = useCallback((id: string) => {
    setDismissedIds(prev => {
      const next = new Set(prev);
      next.add(id);
      return next;
    });
    setExpandedId(null);
  }, []);

  const handleOverride = useCallback((item: AnomalyItem) => {
    setExpandedId(item.id);
  }, []);

  if (anomalies.length === 0) {
    return (
      <div className="flex flex-col items-center justify-center h-48 gap-3 text-center">
        <Shield className="w-10 h-10 text-deloitte-green/40" />
        <div>
          <p className="text-sm text-surface-300 font-medium">No anomalies detected</p>
          <p className="text-xs text-surface-500 mt-1">All line items are within expected ranges.</p>
        </div>
      </div>
    );
  }

  return (
    <div className="space-y-0">
      {/* Summary */}
      <AnomalySummary summary={summary} typeChart={typeChart} />

      {/* Filters */}
      <FilterBar
        filters={filters}
        onChange={setFilters}
        categories={categories}
        totalCount={anomalies.length}
        filteredCount={filteredAnomalies.length}
      />

      {/* Anomaly list */}
      <div className="space-y-2">
        {filteredAnomalies.map((item: AnomalyItem) => (
          <AnomalyRow
            key={item.id}
            item={{ ...item, is_dismissed: dismissedIds.has(item.id) }}
            isExpanded={expandedId === item.id}
            onToggle={() => setExpandedId(expandedId === item.id ? null : item.id)}
            onDismiss={handleDismiss}
            onOverride={handleOverride}
          />
        ))}

        {filteredAnomalies.length === 0 && (
          <div className="text-center py-8 text-surface-500 text-xs">
            <Filter className="w-6 h-6 mx-auto mb-2 opacity-40" />
            No anomalies match your current filters.
          </div>
        )}
      </div>

      {/* Footer note */}
      <div className="mt-4 text-xs text-surface-600 text-center">
        Anomalies are ranked by composite score (severity x materiality).
        Dismiss items you've reviewed to focus on what remains.
      </div>
    </div>
  );
}
