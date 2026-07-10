import { useState } from 'react';
import {
  BarChart, Bar, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, Cell,
} from 'recharts';
import {
  TrendingUp, TrendingDown, DollarSign, Shield, Clock, FileText,
  Download, Loader2, AlertTriangle, Target, Activity,
  ChevronDown, ChevronUp, CheckCircle, XCircle, Eye,
  ArrowUpRight, ArrowDownRight, Zap, Users, GitBranch,
  AlertOctagon, Lightbulb, ChevronRight,
} from 'lucide-react';
import { usePanelStore } from '../../store/panelStore';

const COLORS = {
  green: '#86BC25',
  teal: '#0076A8',
  tealLight: '#00A3E0',
  red: '#E84855',
  amber: '#FFB547',
  coolGray: '#97999B',
};

function formatCurrency(val: number): string {
  if (Math.abs(val) >= 1_000_000_000) return `$${(val / 1_000_000_000).toFixed(1)}B`;
  if (Math.abs(val) >= 1_000_000) return `$${(val / 1_000_000).toFixed(1)}M`;
  if (Math.abs(val) >= 1_000) return `$${(val / 1_000).toFixed(0)}K`;
  return `$${val.toFixed(0)}`;
}

function formatPct(val: number): string {
  return `${val > 0 ? '+' : ''}${val.toFixed(1)}%`;
}

// ─── Types ─────────────────────────────────────────
interface PriorityItem {
  line_item_name: string;
  line_item_id: number;
  category: string;
  business_unit: string;
  avg_p50: number;
  total_p50: number;
  materiality_pct: number;
  forecast_range: { low: number; high: number };
  risk_score: number;
  ai_recommendation: string | null;
  ai_reasoning: string | null;
  priority_reason: string;
  urgency: string;
  is_reviewed: boolean;
  is_overridden: boolean;
  last_actual: number | null;
}

interface Insight {
  type: string;
  title: string;
  detail: string;
  action: string;
}

interface OverrideItem {
  line_item: string;
  category: string;
  period: string;
  original: number;
  override: number;
  delta: number;
  delta_pct: number;
  reason: string;
}

interface ExecData {
  version: Record<string, any>;
  empty?: boolean;
  position: {
    total_p50: number;
    total_p10: number;
    total_p90: number;
    downside_risk: number;
    upside_opportunity: number;
    prior_total: number | null;
    delta_vs_prior: number | null;
    delta_pct: number | null;
    override_count: number;
  };
  review_progress: {
    total_items: number;
    reviewed: number;
    approved: number;
    pending: number;
    flagged: number;
    overridden: number;
    pct_complete: number;
  };
  priority_items: PriorityItem[];
  risk_opportunity: { category: string; forecast: number; downside_risk: number; upside_opportunity: number; range_pct: number }[];
  insights: Insight[];
  override_summary: OverrideItem[];
  driver_summary: { total_submissions: number; approved: number; pending: number; late: number; business_units: string[] };
  bridge_data: any[];
  bridge_narrative: string[];
  accuracy: Record<string, any>;
  time_series: any[];
}

// ─── Forecast Position Hero ────────────────────────
function ForecastHero({ position, version }: { position: ExecData['position']; version: Record<string, any> }) {
  const hasPrior = position.prior_total !== null;
  const deltaPositive = (position.delta_vs_prior ?? 0) >= 0;

  return (
    <div className="bg-gradient-to-br from-surface-800 to-surface-800/60 border border-surface-700/50 rounded-xl p-4">
      <div className="flex items-start justify-between">
        <div>
          <div className="text-xs text-surface-500 uppercase tracking-wider font-semibold mb-1">
            Total Forecast — {version.name}
          </div>
          <div className="text-2xl font-bold text-white tracking-tight">
            {formatCurrency(position.total_p50)}
          </div>
          <div className="text-xs text-surface-500 mt-0.5">
            Range: {formatCurrency(position.total_p10)} – {formatCurrency(position.total_p90)}
          </div>
        </div>
        {hasPrior && position.delta_vs_prior !== null && (
          <div className={`text-right px-3 py-1.5 rounded-lg ${deltaPositive ? 'bg-green-500/10 border border-green-500/20' : 'bg-red-500/10 border border-red-500/20'}`}>
            <div className={`text-sm font-bold flex items-center gap-1 ${deltaPositive ? 'text-green-400' : 'text-red-400'}`}>
              {deltaPositive ? <ArrowUpRight className="w-3.5 h-3.5" /> : <ArrowDownRight className="w-3.5 h-3.5" />}
              {formatCurrency(Math.abs(position.delta_vs_prior))}
            </div>
            <div className="text-xs text-surface-500">
              {formatPct(position.delta_pct ?? 0)} vs prior
            </div>
          </div>
        )}
      </div>

      {/* Risk envelope bar */}
      <div className="mt-3 flex items-center gap-2">
        <div className="flex-1">
          <div className="flex items-center justify-between text-xs text-surface-500 mb-1">
            <span className="flex items-center gap-1">
              <TrendingDown className="w-2.5 h-2.5 text-red-400" /> Downside: {formatCurrency(position.downside_risk)}
            </span>
            <span className="flex items-center gap-1">
              Upside: {formatCurrency(position.upside_opportunity)} <TrendingUp className="w-2.5 h-2.5 text-green-400" />
            </span>
          </div>
          <div className="h-2 bg-surface-700 rounded-full overflow-hidden flex">
            <div className="bg-red-400/60 rounded-l-full" style={{ width: `${position.downside_risk / (position.downside_risk + position.upside_opportunity + 1) * 100}%` }} />
            <div className="bg-surface-500/40 flex-1" />
            <div className="bg-green-400/60 rounded-r-full" style={{ width: `${position.upside_opportunity / (position.downside_risk + position.upside_opportunity + 1) * 100}%` }} />
          </div>
        </div>
      </div>
    </div>
  );
}

// ─── Review Progress Bar ───────────────────────────
function ReviewProgress({ progress }: { progress: ExecData['review_progress'] }) {
  const openPanel = usePanelStore((s) => s.openPanel);

  return (
    <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3">
      <div className="flex items-center justify-between mb-2">
        <h4 className="text-xs text-surface-500 uppercase tracking-wider font-semibold flex items-center gap-1">
          <Shield className="w-3 h-3" /> Review Cycle Progress
        </h4>
        <span className="text-xs font-bold text-white">{progress.pct_complete}%</span>
      </div>

      {/* Progress bar */}
      <div className="h-3 bg-surface-700 rounded-full overflow-hidden flex mb-2">
        <div
          className="bg-deloitte-green rounded-l-full transition-all duration-500"
          style={{ width: `${(progress.approved / Math.max(progress.total_items, 1)) * 100}%` }}
          title={`${progress.approved} approved`}
        />
        <div
          className="bg-amber-400/70"
          style={{ width: `${((progress.reviewed - progress.approved) / Math.max(progress.total_items, 1)) * 100}%` }}
          title={`${progress.reviewed - progress.approved} reviewed (not approved)`}
        />
      </div>

      <div className="grid grid-cols-4 gap-2 text-center">
        <div>
          <div className="text-xs font-bold text-deloitte-green">{progress.approved}</div>
          <div className="text-[8px] text-surface-500">Approved</div>
        </div>
        <div>
          <div className="text-xs font-bold text-amber-400">{progress.pending}</div>
          <div className="text-[8px] text-surface-500">Pending</div>
        </div>
        <div>
          <div className="text-xs font-bold text-red-400">{progress.flagged}</div>
          <div className="text-[8px] text-surface-500">Flagged</div>
        </div>
        <div>
          <div className="text-xs font-bold text-cyan-400">{progress.overridden}</div>
          <div className="text-[8px] text-surface-500">Overridden</div>
        </div>
      </div>

      {progress.pending > 0 && (
        <button
          onClick={() => openPanel('review_dashboard', { version_id: '' })}
          className="mt-2 w-full text-xs text-deloitte-green hover:text-white flex items-center justify-center gap-1 py-1 rounded border border-deloitte-green/20 hover:bg-deloitte-green/10 transition-colors"
        >
          Open Review Dashboard <ChevronRight className="w-3 h-3" />
        </button>
      )}
    </div>
  );
}

// ─── Priority Items ────────────────────────────────
function PriorityActions({ items }: { items: PriorityItem[] }) {
  const [expandedIdx, setExpandedIdx] = useState<number | null>(null);

  if (items.length === 0) {
    return (
      <div className="bg-surface-800/60 border border-deloitte-green/20 rounded-xl p-4 text-center">
        <CheckCircle className="w-6 h-6 text-deloitte-green mx-auto mb-2" />
        <p className="text-xs text-surface-300">All material items have been reviewed.</p>
        <p className="text-xs text-surface-500 mt-1">No priority actions required at this time.</p>
      </div>
    );
  }

  return (
    <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl overflow-hidden">
      <div className="px-3 py-2 border-b border-surface-700/50 flex items-center justify-between">
        <h4 className="text-xs text-surface-500 uppercase tracking-wider font-semibold flex items-center gap-1">
          <AlertTriangle className="w-3 h-3 text-amber-400" /> Items Requiring Your Attention
        </h4>
        <span className="text-xs text-surface-600">{items.length} items</span>
      </div>
      <div className="divide-y divide-surface-700/30 max-h-[280px] overflow-y-auto">
        {items.map((item, i) => {
          const isExpanded = expandedIdx === i;
          const urgencyColor = item.urgency === 'high' ? 'border-l-red-400' : item.urgency === 'medium' ? 'border-l-amber-400' : 'border-l-sky-400';

          return (
            <div key={i} className={`border-l-2 ${urgencyColor}`}>
              <button
                onClick={() => setExpandedIdx(isExpanded ? null : i)}
                className="w-full flex items-center gap-2 px-3 py-2 text-left hover:bg-surface-700/20 transition-colors"
              >
                <div className="flex-shrink-0">
                  {item.urgency === 'high'
                    ? <AlertOctagon className="w-3.5 h-3.5 text-red-400" />
                    : <AlertTriangle className="w-3.5 h-3.5 text-amber-400" />}
                </div>
                <div className="flex-1 min-w-0">
                  <div className="text-xs font-medium text-white truncate">{item.line_item_name}</div>
                  <div className="text-xs text-surface-500 truncate">{item.priority_reason}</div>
                </div>
                <div className="text-right flex-shrink-0">
                  <div className="text-xs font-mono text-white">{formatCurrency(item.avg_p50)}</div>
                  <div className="text-xs text-surface-500">{item.materiality_pct}% of total</div>
                </div>
                {isExpanded ? <ChevronUp className="w-3 h-3 text-surface-500 flex-shrink-0" /> : <ChevronDown className="w-3 h-3 text-surface-500 flex-shrink-0" />}
              </button>

              {isExpanded && (
                <div className="px-3 pb-2.5 space-y-2">
                  {/* Context cards */}
                  <div className="grid grid-cols-3 gap-1.5">
                    <div className="bg-surface-900/50 rounded p-1.5 text-center">
                      <div className="text-xs text-surface-500">Forecast Range</div>
                      <div className="text-xs text-white font-mono">{formatCurrency(item.forecast_range.low)} – {formatCurrency(item.forecast_range.high)}</div>
                    </div>
                    <div className="bg-surface-900/50 rounded p-1.5 text-center">
                      <div className="text-xs text-surface-500">Risk Score</div>
                      <div className={`text-xs font-mono font-bold ${item.risk_score > 60 ? 'text-red-400' : item.risk_score > 30 ? 'text-amber-400' : 'text-deloitte-green'}`}>{item.risk_score}/100</div>
                    </div>
                    <div className="bg-surface-900/50 rounded p-1.5 text-center">
                      <div className="text-xs text-surface-500">Last Actual</div>
                      <div className="text-xs text-white font-mono">{item.last_actual !== null ? formatCurrency(item.last_actual) : '—'}</div>
                    </div>
                  </div>

                  {/* AI reasoning */}
                  {item.ai_reasoning && (
                    <div className="bg-surface-900/40 rounded-lg p-2 border border-surface-700/30">
                      <div className="text-xs text-surface-500 uppercase font-semibold mb-0.5">AI Assessment</div>
                      <p className="text-xs text-surface-300 leading-relaxed">{item.ai_reasoning}</p>
                    </div>
                  )}

                  <div className="flex items-center gap-2 text-xs text-surface-500">
                    <span>{item.category}</span>
                    {item.business_unit && <span>• BU: {item.business_unit}</span>}
                    {item.is_overridden && <span className="text-cyan-400">• Overridden</span>}
                  </div>
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}

// ─── Forward-Looking Insights ──────────────────────
function InsightsSection({ insights }: { insights: Insight[] }) {
  if (insights.length === 0) return null;

  const iconMap: Record<string, any> = {
    risk: AlertTriangle,
    warning: AlertOctagon,
    action: Zap,
    opportunity: Lightbulb,
    info: Activity,
  };
  const colorMap: Record<string, string> = {
    risk: 'border-red-500/30 bg-red-500/5',
    warning: 'border-amber-500/30 bg-amber-500/5',
    action: 'border-sky-500/30 bg-sky-500/5',
    opportunity: 'border-green-500/30 bg-green-500/5',
    info: 'border-surface-600 bg-surface-800/40',
  };
  const iconColorMap: Record<string, string> = {
    risk: 'text-red-400',
    warning: 'text-amber-400',
    action: 'text-sky-400',
    opportunity: 'text-green-400',
    info: 'text-surface-400',
  };

  return (
    <div className="space-y-1.5">
      <h4 className="text-xs text-surface-500 uppercase tracking-wider font-semibold flex items-center gap-1 px-1">
        <Lightbulb className="w-3 h-3" /> Forward-Looking Insights
      </h4>
      {insights.map((insight, i) => {
        const Icon = iconMap[insight.type] || Activity;
        return (
          <div key={i} className={`rounded-lg border p-2.5 ${colorMap[insight.type] || colorMap.info}`}>
            <div className="flex items-start gap-2">
              <Icon className={`w-3.5 h-3.5 mt-0.5 flex-shrink-0 ${iconColorMap[insight.type] || iconColorMap.info}`} />
              <div className="flex-1 min-w-0">
                <div className="text-xs font-semibold text-white">{insight.title}</div>
                <p className="text-xs text-surface-400 leading-relaxed mt-0.5">{insight.detail}</p>
                <div className="text-xs text-deloitte-green mt-1 flex items-center gap-1 font-medium">
                  <ChevronRight className="w-3 h-3" /> {insight.action}
                </div>
              </div>
            </div>
          </div>
        );
      })}
    </div>
  );
}

// ─── Risk / Opportunity Table ──────────────────────
function RiskOpportunityTable({ data }: { data: ExecData['risk_opportunity'] }) {
  if (data.length === 0) return null;
  const maxVal = Math.max(...data.map(d => Math.max(d.downside_risk, d.upside_opportunity)));

  return (
    <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3">
      <h4 className="text-xs text-surface-500 uppercase tracking-wider font-semibold mb-2">
        Risk / Opportunity by Category
      </h4>
      <div className="space-y-1.5">
        {data.map((row, i) => (
          <div key={i} className="flex items-center gap-2">
            <div className="w-20 text-xs text-surface-400 truncate" title={row.category}>{row.category}</div>
            <div className="flex-1 flex items-center gap-0.5">
              {/* Downside bar (right-to-left) */}
              <div className="flex-1 flex justify-end">
                <div
                  className="h-3 bg-red-400/40 rounded-l"
                  style={{ width: `${maxVal > 0 ? (row.downside_risk / maxVal) * 100 : 0}%` }}
                />
              </div>
              <div className="w-px h-4 bg-surface-500/50" />
              {/* Upside bar (left-to-right) */}
              <div className="flex-1">
                <div
                  className="h-3 bg-green-400/40 rounded-r"
                  style={{ width: `${maxVal > 0 ? (row.upside_opportunity / maxVal) * 100 : 0}%` }}
                />
              </div>
            </div>
            <div className="w-12 text-xs text-surface-500 text-right">{row.range_pct}%</div>
          </div>
        ))}
      </div>
      <div className="flex items-center justify-center gap-4 mt-2 text-[8px] text-surface-500">
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-red-400/40" /> Downside risk</span>
        <span className="flex items-center gap-1"><span className="w-2 h-2 rounded-sm bg-green-400/40" /> Upside opportunity</span>
      </div>
    </div>
  );
}

// ─── Override Summary ──────────────────────────────
function OverrideSummary({ overrides }: { overrides: OverrideItem[] }) {
  if (overrides.length === 0) return null;

  return (
    <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl overflow-hidden">
      <div className="px-3 py-2 border-b border-surface-700/50">
        <h4 className="text-xs text-surface-500 uppercase tracking-wider font-semibold flex items-center gap-1">
          <GitBranch className="w-3 h-3" /> Active Manual Adjustments
        </h4>
      </div>
      <div className="divide-y divide-surface-700/20 max-h-[150px] overflow-y-auto">
        {overrides.map((o, i) => (
          <div key={i} className="px-3 py-1.5 flex items-center gap-2">
            <div className="flex-1 min-w-0">
              <div className="text-xs text-surface-300 truncate">{o.line_item}</div>
              <div className="text-xs text-surface-500 truncate">{o.reason}</div>
            </div>
            <div className="text-right flex-shrink-0">
              <div className={`text-xs font-mono ${o.delta > 0 ? 'text-green-400' : 'text-red-400'}`}>
                {o.delta > 0 ? '+' : ''}{formatCurrency(o.delta)}
              </div>
              <div className="text-[8px] text-surface-600">{o.period}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// ─── Main Executive Dashboard ──────────────────────
export function ExecutiveDashboardPanel({ data }: { data: any }) {
  const d: ExecData = data?.data || data;
  const [showBridge, setShowBridge] = useState(false);
  const [isExporting, setIsExporting] = useState(false);

  if (d.empty) {
    return (
      <div className="flex flex-col items-center justify-center h-48 gap-3">
        <DollarSign className="w-10 h-10 text-surface-600" />
        <p className="text-sm text-surface-400">No forecast data available.</p>
      </div>
    );
  }

  const ChartTooltip = ({ active, payload, label }: any) => {
    if (!active || !payload?.length) return null;
    return (
      <div className="bg-surface-800 border border-surface-600 rounded-lg px-3 py-2 shadow-xl">
        <p className="text-xs text-surface-400 mb-1">{label}</p>
        {payload.map((p: any, i: number) => (
          <p key={i} className="text-xs font-medium" style={{ color: p.color }}>
            {p.name}: {formatCurrency(p.value)}
          </p>
        ))}
      </div>
    );
  };

  return (
    <div className="space-y-3">
      {/* 1. Forecast Position Hero */}
      <ForecastHero position={d.position} version={d.version} />

      {/* 2. Review Progress + Accuracy row */}
      <div className="grid grid-cols-2 gap-2">
        <ReviewProgress progress={d.review_progress} />

        {/* Accuracy capsule */}
        <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3">
          <h4 className="text-xs text-surface-500 uppercase tracking-wider font-semibold flex items-center gap-1 mb-2">
            <Target className="w-3 h-3" /> Forecast Accuracy
          </h4>
          {d.accuracy && d.accuracy.comparisons > 0 ? (
            <div className="grid grid-cols-3 gap-2 text-center">
              <div>
                <div className={`text-sm font-bold font-mono ${d.accuracy.avg_mape > 15 ? 'text-red-400' : d.accuracy.avg_mape > 8 ? 'text-amber-400' : 'text-deloitte-green'}`}>
                  {d.accuracy.avg_mape.toFixed(1)}%
                </div>
                <div className="text-[8px] text-surface-500">MAPE</div>
              </div>
              <div>
                <div className={`text-sm font-bold font-mono ${d.accuracy.bias_direction === 'over' ? 'text-amber-400' : d.accuracy.bias_direction === 'under' ? 'text-blue-400' : 'text-deloitte-green'}`}>
                  {d.accuracy.avg_bias > 0 ? '+' : ''}{d.accuracy.avg_bias.toFixed(1)}%
                </div>
                <div className="text-[8px] text-surface-500">
                  Bias ({d.accuracy.bias_direction === 'over' ? 'Over' : d.accuracy.bias_direction === 'under' ? 'Under' : 'OK'})
                </div>
              </div>
              <div>
                <div className={`text-sm font-bold font-mono ${d.accuracy.hit_rate >= 80 ? 'text-deloitte-green' : d.accuracy.hit_rate >= 60 ? 'text-amber-400' : 'text-red-400'}`}>
                  {d.accuracy.hit_rate.toFixed(0)}%
                </div>
                <div className="text-[8px] text-surface-500">Hit Rate</div>
              </div>
            </div>
          ) : (
            <p className="text-xs text-surface-500 text-center py-2">Awaiting actuals overlap</p>
          )}
        </div>
      </div>

      {/* 3. Priority Actions */}
      <PriorityActions items={d.priority_items} />

      {/* 4. Forward-Looking Insights */}
      <InsightsSection insights={d.insights} />

      {/* 5. Risk / Opportunity by Category */}
      <RiskOpportunityTable data={d.risk_opportunity} />

      {/* 6. Override Summary */}
      <OverrideSummary overrides={d.override_summary} />

      {/* 7. Bridge + Trend toggle */}
      <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3">
        <div className="flex items-center gap-1 mb-2">
          <button
            onClick={() => setShowBridge(false)}
            className={`text-xs px-2 py-1 rounded-full transition-colors ${!showBridge ? 'bg-deloitte-green/15 text-deloitte-green border border-deloitte-green/30' : 'text-surface-400 hover:text-white'}`}
          >
            Monthly Trend
          </button>
          <button
            onClick={() => setShowBridge(true)}
            className={`text-xs px-2 py-1 rounded-full transition-colors ${showBridge ? 'bg-deloitte-green/15 text-deloitte-green border border-deloitte-green/30' : 'text-surface-400 hover:text-white'}`}
          >
            Variance Bridge
          </button>
        </div>

        {!showBridge ? (
          <ResponsiveContainer width="100%" height={180}>
            <AreaChart data={d.time_series} margin={{ top: 5, right: 5, left: 5, bottom: 5 }}>
              <defs>
                <linearGradient id="cfoGradP50" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={COLORS.green} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={COLORS.green} stopOpacity={0} />
                </linearGradient>
                <linearGradient id="cfoGradCI" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={COLORS.teal} stopOpacity={0.15} />
                  <stop offset="95%" stopColor={COLORS.teal} stopOpacity={0} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
              <XAxis dataKey="period" tick={{ fill: '#97999B', fontSize: 9 }} />
              <YAxis tick={{ fill: '#97999B', fontSize: 9 }} tickFormatter={(v) => formatCurrency(v)} width={45} />
              <Tooltip content={<ChartTooltip />} />
              <Area type="monotone" dataKey="p90" name="P90 (Upside)" stroke={COLORS.teal} fill="url(#cfoGradCI)" strokeWidth={1} strokeDasharray="4 3" />
              <Area type="monotone" dataKey="p10" name="P10 (Downside)" stroke={COLORS.teal} fill="url(#cfoGradCI)" strokeWidth={1} strokeDasharray="4 3" />
              <Area type="monotone" dataKey="p50" name="Forecast" stroke={COLORS.green} fill="url(#cfoGradP50)" strokeWidth={2.5} />
            </AreaChart>
          </ResponsiveContainer>
        ) : d.bridge_data.length > 0 ? (
          <>
            <ResponsiveContainer width="100%" height={180}>
              <BarChart data={d.bridge_data} margin={{ top: 5, right: 5, left: 5, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
                <XAxis dataKey="name" tick={{ fill: '#97999B', fontSize: 9 }} angle={-15} textAnchor="end" height={40} />
                <YAxis tick={{ fill: '#97999B', fontSize: 9 }} tickFormatter={(v) => formatCurrency(v)} width={45} />
                <Tooltip content={<ChartTooltip />} />
                <Bar dataKey="invisible" stackId="bridge" fill="transparent" />
                <Bar dataKey="value" stackId="bridge" radius={[2, 2, 0, 0]}>
                  {d.bridge_data.map((entry: any, i: number) => (
                    <Cell key={i} fill={entry.is_total ? COLORS.teal : entry.value >= 0 ? COLORS.green : COLORS.red} />
                  ))}
                </Bar>
              </BarChart>
            </ResponsiveContainer>
            {d.bridge_narrative.length > 0 && (
              <div className="mt-1.5 flex flex-wrap gap-1.5">
                {d.bridge_narrative.map((n: string, i: number) => (
                  <span key={i} className="text-xs px-2 py-0.5 bg-surface-700/50 rounded-full text-surface-400">{n}</span>
                ))}
              </div>
            )}
          </>
        ) : (
          <p className="text-surface-500 text-xs text-center py-6">Bridge available after second forecast cycle.</p>
        )}
      </div>

      {/* 8. BU Input Status */}
      {d.driver_summary && d.driver_summary.total_submissions > 0 && (
        <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3">
          <h4 className="text-xs text-surface-500 uppercase tracking-wider font-semibold flex items-center gap-1 mb-2">
            <Users className="w-3 h-3" /> BU Assumptions Status
          </h4>
          <div className="grid grid-cols-4 gap-2 text-center text-xs">
            <div>
              <div className="text-xs font-bold text-white">{d.driver_summary.total_submissions}</div>
              <div className="text-[8px] text-surface-500">Total</div>
            </div>
            <div>
              <div className="text-xs font-bold text-deloitte-green">{d.driver_summary.approved}</div>
              <div className="text-[8px] text-surface-500">Approved</div>
            </div>
            <div>
              <div className="text-xs font-bold text-amber-400">{d.driver_summary.pending}</div>
              <div className="text-[8px] text-surface-500">Pending</div>
            </div>
            <div>
              <div className="text-xs font-bold text-red-400">{d.driver_summary.late}</div>
              <div className="text-[8px] text-surface-500">Overdue</div>
            </div>
          </div>
          {d.driver_summary.business_units.length > 0 && (
            <div className="mt-1.5 flex flex-wrap gap-1">
              {d.driver_summary.business_units.map((bu: string) => (
                <span key={bu} className="text-[8px] px-1.5 py-0.5 bg-surface-700/50 rounded text-surface-400">{bu}</span>
              ))}
            </div>
          )}
        </div>
      )}

      {/* Footer */}
      <div className="flex items-center justify-between px-1 pt-1">
        <div className="flex items-center gap-2 text-xs text-surface-500">
          <span className="flex items-center gap-1">
            <FileText className="w-3 h-3" />
            {d.version.name} • {d.version.status}
          </span>
          {d.version.base_period && (
            <span className="flex items-center gap-1">
              <Clock className="w-3 h-3" />
              Base: {d.version.base_period}
            </span>
          )}
        </div>
        <button
          onClick={async () => {
            if (!d.version?.id) {
              alert('No version id available for export');
              return;
            }
            setIsExporting(true);
            try {
              const token = localStorage.getItem('forecast-auth');
              let authHeader = '';
              try {
                const parsed = token ? JSON.parse(token) : null;
                authHeader = parsed?.state?.token ? `Bearer ${parsed.state.token}` : '';
              } catch { /* ignore */ }
              const res = await fetch(`/api/executive/board-pack/${d.version.id}?format=pptx`, {
                headers: authHeader ? { Authorization: authHeader } : {},
              });
              if (!res.ok) throw new Error('Export failed');
              const blob = await res.blob();
              const url = URL.createObjectURL(blob);
              const a = document.createElement('a');
              a.href = url;
              a.download = `${d.version.name || 'forecast'}_board_pack.pptx`;
              a.click();
              URL.revokeObjectURL(url);
            } catch (e: any) {
              alert(e.message || 'Board pack export failed');
            } finally {
              setIsExporting(false);
            }
          }}
          disabled={isExporting}
          className="flex items-center gap-1 px-2 py-1 bg-surface-800/60 border border-surface-700/50 rounded-lg text-xs text-surface-400 hover:text-white hover:border-deloitte-green/30 transition-all disabled:opacity-50"
        >
          {isExporting ? <Loader2 className="w-3 h-3 animate-spin" /> : <Download className="w-3 h-3" />}
          Export PPT
        </button>
      </div>
    </div>
  );
}
