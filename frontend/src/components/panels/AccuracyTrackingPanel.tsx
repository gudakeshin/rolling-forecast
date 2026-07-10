import { useState } from 'react';
import {
  BarChart, Bar, LineChart, Line, ComposedChart,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine, Cell,
} from 'recharts';
import { Target, TrendingDown, BarChart3, AlertCircle, ArrowUpRight, ArrowDownRight } from 'lucide-react';

const COLORS = {
  green: '#86BC25',
  teal: '#0076A8',
  tealLight: '#00A3E0',
  red: '#E84855',
  amber: '#FFB547',
  coolGray: '#97999B',
};

interface Props {
  data: {
    panel_type: string;
    title: string;
    data: {
      overall: {
        avg_mape: number;
        median_mape: number;
        avg_bias: number;
        bias_dollar?: number;
        bias_direction?: string;
        hit_rate: number;
        total_comparisons: number;
      };
      model_performance: any[];
      category_accuracy: any[];
      mape_trend: any[];
      top_deviations: any[];
      bias_trend?: any[];
      items: any[];
    };
  };
}

function formatPct(val: number): string {
  return `${val.toFixed(1)}%`;
}

function formatCurrency(val: number): string {
  if (Math.abs(val) >= 1_000_000) return `$${(val / 1_000_000).toFixed(1)}M`;
  if (Math.abs(val) >= 1_000) return `$${(val / 1_000).toFixed(0)}K`;
  return `$${val.toFixed(0)}`;
}

const ChartTooltip = ({ active, payload, label }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-surface-800 border border-surface-600 rounded-lg px-3 py-2 shadow-xl">
      <p className="text-xs text-surface-400 mb-1">{label}</p>
      {payload.map((p: any, i: number) => (
        <p key={i} className="text-xs font-medium" style={{ color: p.color }}>
          {p.name}: {p.value}%
        </p>
      ))}
    </div>
  );
};

export function AccuracyTrackingPanel({ data }: Props) {
  const { overall, model_performance, category_accuracy, mape_trend, top_deviations, bias_trend } = data.data;
  const [activeTab, setActiveTab] = useState<'overview' | 'models' | 'deviations'>('overview');

  return (
    <div className="space-y-4">
      {/* KPI cards */}
      <div className="grid grid-cols-4 gap-2">
        <KPI
          label="Avg MAPE"
          value={formatPct(overall.avg_mape)}
          color={overall.avg_mape > 15 ? 'text-red-400' : overall.avg_mape > 8 ? 'text-amber-400' : 'text-deloitte-green'}
          icon={Target}
        />
        <KPI
          label="Median MAPE"
          value={formatPct(overall.median_mape)}
          color="text-deloitte-teal-light"
          icon={BarChart3}
        />
        <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-2.5 text-center">
          <TrendingDown className={`w-3.5 h-3.5 mx-auto mb-1 ${overall.bias_direction === 'over' ? 'text-amber-400' : overall.bias_direction === 'under' ? 'text-blue-400' : 'text-deloitte-green'}`} />
          <div className={`text-sm font-bold ${overall.bias_direction === 'over' ? 'text-amber-400' : overall.bias_direction === 'under' ? 'text-blue-400' : 'text-deloitte-green'}`}>
            {overall.avg_bias > 0 ? '+' : ''}{formatPct(overall.avg_bias)}
          </div>
          <div className="text-[8px] text-surface-500 uppercase tracking-wider font-semibold">
            Bias ({overall.bias_direction === 'over' ? 'Over' : overall.bias_direction === 'under' ? 'Under' : 'Neutral'})
          </div>
          {overall.bias_dollar !== undefined && (
            <div className="text-[8px] text-surface-600 mt-0.5">
              {formatCurrency(overall.bias_dollar)} net
            </div>
          )}
        </div>
        <KPI
          label="Hit Rate"
          value={formatPct(overall.hit_rate)}
          color={overall.hit_rate >= 80 ? 'text-deloitte-green' : 'text-amber-400'}
          icon={Target}
        />
      </div>

      {/* Tabs */}
      <div className="flex gap-1 bg-surface-800/50 rounded-lg p-1">
        {(['overview', 'models', 'deviations'] as const).map((tab) => (
          <button
            key={tab}
            onClick={() => setActiveTab(tab)}
            className={`flex-1 px-3 py-1.5 text-xs font-medium rounded-md transition-all ${
              activeTab === tab
                ? 'bg-deloitte-green/20 text-deloitte-green border border-deloitte-green/30'
                : 'text-surface-400 hover:text-white hover:bg-surface-700/50'
            }`}
          >
            {tab === 'overview' ? 'Trend & Categories' : tab === 'models' ? 'Model Compare' : 'Top Deviations'}
          </button>
        ))}
      </div>

      {activeTab === 'overview' && (
        <div className="space-y-3">
          {/* MAPE trend */}
          <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-4">
            <h4 className="text-xs font-semibold text-surface-400 uppercase tracking-wider mb-3">MAPE Trend Across Versions</h4>
            {mape_trend.length > 1 ? (
              <ResponsiveContainer width="100%" height={180}>
                <LineChart data={mape_trend} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
                  <XAxis dataKey="version" tick={{ fill: '#97999B', fontSize: 10 }} />
                  <YAxis tick={{ fill: '#97999B', fontSize: 10 }} unit="%" />
                  <Tooltip content={<ChartTooltip />} />
                  <ReferenceLine y={5} stroke={COLORS.green} strokeDasharray="5 5" label={{ value: 'Target 5%', fill: COLORS.green, fontSize: 9 }} />
                  <Line type="monotone" dataKey="avg_mape" name="Avg MAPE" stroke={COLORS.teal} strokeWidth={2.5} dot={{ r: 4, fill: COLORS.teal }} />
                </LineChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-surface-500 text-xs text-center py-6">More versions needed for trend visualization</p>
            )}
          </div>

          {/* Bias Trend */}
          {bias_trend && bias_trend.length > 1 && (
            <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-4">
              <h4 className="text-xs font-semibold text-surface-400 uppercase tracking-wider mb-3">
                Bias Detection Trend
                <span className="ml-2 text-xs font-normal text-surface-500">(positive = over-forecast, negative = under-forecast)</span>
              </h4>
              <ResponsiveContainer width="100%" height={150}>
                <ComposedChart data={bias_trend} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
                  <XAxis dataKey="version" tick={{ fill: '#97999B', fontSize: 10 }} />
                  <YAxis tick={{ fill: '#97999B', fontSize: 10 }} unit="%" />
                  <Tooltip content={<ChartTooltip />} />
                  <ReferenceLine y={0} stroke={COLORS.coolGray} strokeDasharray="3 3" />
                  <Bar dataKey="avg_bias" name="Avg Bias" radius={[3, 3, 0, 0]}>
                    {bias_trend.map((entry: any, i: number) => (
                      <Cell key={i} fill={entry.avg_bias >= 0 ? COLORS.amber : COLORS.tealLight} />
                    ))}
                  </Bar>
                </ComposedChart>
              </ResponsiveContainer>
            </div>
          )}

          {/* Category accuracy */}
          <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-4">
            <h4 className="text-xs font-semibold text-surface-400 uppercase tracking-wider mb-3">Accuracy by Category</h4>
            {category_accuracy.length > 0 ? (
              <ResponsiveContainer width="100%" height={180}>
                <BarChart data={category_accuracy} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
                  <XAxis dataKey="category" tick={{ fill: '#97999B', fontSize: 10 }} />
                  <YAxis tick={{ fill: '#97999B', fontSize: 10 }} unit="%" />
                  <Tooltip content={<ChartTooltip />} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  <Bar dataKey="avg_mape" name="MAPE" radius={[3, 3, 0, 0]}>
                    {category_accuracy.map((entry: any, i: number) => (
                      <Cell key={i} fill={entry.avg_mape > 15 ? COLORS.red : entry.avg_mape > 8 ? COLORS.amber : COLORS.green} />
                    ))}
                  </Bar>
                  <Bar dataKey="avg_bias" name="Bias" fill={COLORS.tealLight} radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>
            ) : (
              <p className="text-surface-500 text-xs text-center py-6">No accuracy data available</p>
            )}
          </div>
        </div>
      )}

      {activeTab === 'models' && (
        <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-4">
          <h4 className="text-xs font-semibold text-surface-400 uppercase tracking-wider mb-3">Model Performance Comparison</h4>
          {model_performance.length > 0 ? (
            <>
              <ResponsiveContainer width="100%" height={200}>
                <BarChart data={model_performance} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
                  <XAxis dataKey="model" tick={{ fill: '#97999B', fontSize: 10 }} />
                  <YAxis tick={{ fill: '#97999B', fontSize: 10 }} unit="%" />
                  <Tooltip content={<ChartTooltip />} />
                  <Legend wrapperStyle={{ fontSize: 10 }} />
                  <Bar dataKey="avg_mape" name="Avg MAPE" fill={COLORS.green} radius={[3, 3, 0, 0]} />
                  <Bar dataKey="best_mape" name="Best MAPE" fill={COLORS.teal} radius={[3, 3, 0, 0]} />
                  <Bar dataKey="worst_mape" name="Worst MAPE" fill={COLORS.red} radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>

              <div className="mt-3 space-y-1.5">
                {model_performance.map((m: any, i: number) => (
                  <div key={i} className="flex items-center justify-between text-xs px-2 py-1.5 bg-surface-800/40 rounded">
                    <span className="text-white font-medium">{m.model}</span>
                    <div className="flex items-center gap-3 text-surface-400">
                      <span>{m.count} forecasts</span>
                      <span className={m.avg_mape > 10 ? 'text-red-400' : 'text-deloitte-green'}>
                        {formatPct(m.avg_mape)} MAPE
                      </span>
                    </div>
                  </div>
                ))}
              </div>
            </>
          ) : (
            <p className="text-surface-500 text-xs text-center py-6">No model performance data</p>
          )}
        </div>
      )}

      {activeTab === 'deviations' && (
        <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl overflow-hidden">
          <div className="px-4 py-2.5 border-b border-surface-700/50">
            <h4 className="text-xs font-semibold text-white flex items-center gap-1.5">
              <AlertCircle className="w-3.5 h-3.5 text-red-400" />
              Top {top_deviations.length} Largest Deviations
            </h4>
          </div>
          <div className="max-h-[300px] overflow-y-auto">
            <table className="w-full text-xs">
              <thead className="sticky top-0 bg-surface-800 z-10">
                <tr className="border-b border-surface-700">
                  <th className="px-3 py-2 text-left text-surface-400 font-semibold text-xs uppercase">Item</th>
                  <th className="px-3 py-2 text-right text-surface-400 font-semibold text-xs uppercase">Forecast</th>
                  <th className="px-3 py-2 text-right text-surface-400 font-semibold text-xs uppercase">Actual</th>
                  <th className="px-3 py-2 text-right text-surface-400 font-semibold text-xs uppercase">MAPE</th>
                  <th className="px-3 py-2 text-right text-surface-400 font-semibold text-xs uppercase">Bias</th>
                </tr>
              </thead>
              <tbody>
                {top_deviations.map((item: any, i: number) => (
                  <tr key={i} className="border-b border-surface-700/20 hover:bg-red-500/5 transition-colors">
                    <td className="px-3 py-1.5">
                      <div className="text-surface-300 truncate max-w-[120px]">{item.line_item_name}</div>
                      <div className="text-surface-500 text-xs">{item.period} • {item.model_type}</div>
                    </td>
                    <td className="px-3 py-1.5 text-right text-surface-200 font-mono">{formatCurrency(item.forecast)}</td>
                    <td className="px-3 py-1.5 text-right text-surface-200 font-mono">{formatCurrency(item.actual)}</td>
                    <td className="px-3 py-1.5 text-right">
                      <span className={`font-mono font-medium ${item.mape > 20 ? 'text-red-400' : item.mape > 10 ? 'text-amber-400' : 'text-deloitte-green'}`}>
                        {formatPct(item.mape)}
                      </span>
                    </td>
                    <td className="px-3 py-1.5 text-right">
                      <span className={`font-mono ${item.bias > 0 ? 'text-amber-400' : 'text-blue-400'}`}>
                        {item.bias > 0 ? '+' : ''}{formatPct(item.bias)}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>
      )}

      {/* Footer */}
      <p className="text-xs text-surface-500 text-center">
        Based on {overall.total_comparisons} forecast-vs-actual comparisons
      </p>
    </div>
  );
}

function KPI({ label, value, color, icon: Icon, trend }: { label: string; value: string; color: string; icon: any; trend?: 'up' | 'down' | null }) {
  return (
    <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-2.5 text-center relative">
      <Icon className={`w-3.5 h-3.5 mx-auto mb-1 ${color}`} />
      <div className={`text-sm font-bold ${color}`}>{value}</div>
      <div className="text-[8px] text-surface-500 uppercase tracking-wider font-semibold">{label}</div>
      {trend && (
        <div className="absolute top-1.5 right-1.5">
          {trend === 'up' ? (
            <ArrowUpRight className="w-3 h-3 text-red-400" />
          ) : (
            <ArrowDownRight className="w-3 h-3 text-deloitte-green" />
          )}
        </div>
      )}
    </div>
  );
}
