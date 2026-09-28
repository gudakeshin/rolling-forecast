import { useState } from 'react';
import {
  BarChart, Bar, LineChart, Line, ComposedChart,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, ReferenceLine, Cell,
} from 'recharts';
import { Target, TrendingDown, BarChart3, AlertCircle, ArrowUpRight, ArrowDownRight, RefreshCw, Loader2, ExternalLink } from 'lucide-react';
import { Tabs } from '../ui/Tabs';
import { DataTable, type DataTableColumn } from '../ui/DataTable';
import { rescoreForecasts } from '../../api/dashboard';
import { usePanelStore } from '../../store/panelStore';
import { toast } from '../../store/toastStore';
import { chartTheme } from '../../theme/chartTheme';

const COLORS = {
  green: chartTheme.colors.primary,
  teal: chartTheme.colors.secondary,
  tealLight: '#5B8AA6',
  red: chartTheme.colors.danger,
  amber: chartTheme.colors.warning,
  coolGray: chartTheme.colors.tertiary,
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
        published_wape?: number | null;
        model_wape?: number | null;
        naive_wape?: number | null;
        seasonal_naive_wape?: number | null;
        fva_vs_naive?: number | null;
        fva_vs_seasonal_naive?: number | null;
      };
      model_performance: any[];
      category_accuracy: any[];
      mape_trend: any[];
      top_deviations: any[];
      bias_trend?: any[];
      items: any[];
      version?: { id: string; name: string };
    };
  };
  onRefresh?: () => void;
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

export function AccuracyTrackingPanel({ data, onRefresh }: Props) {
  const { overall, model_performance, category_accuracy, mape_trend, top_deviations, bias_trend, items } = data.data;
  const [activeTab, setActiveTab] = useState<'overview' | 'models' | 'deviations'>('overview');
  const [rescoring, setRescoring] = useState(false);
  const openPanel = usePanelStore((s) => s.openPanel);
  const panelVersionId = usePanelStore((s) => s.panelParams.version_id);
  const versionId = (data.data as any).version?.id || panelVersionId;

  const handleRescore = async () => {
    if (!versionId) {
      toast.error('No version id for rescore');
      return;
    }
    setRescoring(true);
    try {
      await rescoreForecasts(versionId);
      toast.success('Rescore complete');
      onRefresh?.();
    } catch (e: any) {
      toast.error(e.message || 'Rescore failed');
    } finally {
      setRescoring(false);
    }
  };

  const deviationRows = (top_deviations?.length ? top_deviations : (items || [])) as Record<string, unknown>[];
  const deviationColumns: DataTableColumn<Record<string, unknown>>[] = [
    {
      key: 'line_item_name',
      label: 'Item',
      render: (_v, row) => (
        <div>
          <div className="text-surface-300 truncate max-w-[140px]">{String(row.line_item_name ?? '')}</div>
          <div className="text-surface-500 text-xs">
            {String(row.period ?? '')} • {String(row.model_type ?? '')}
          </div>
        </div>
      ),
    },
    {
      key: 'forecast',
      label: 'Forecast',
      align: 'right',
      render: (v) => formatCurrency(Number(v) || 0),
    },
    {
      key: 'actual',
      label: 'Actual',
      align: 'right',
      render: (v) => formatCurrency(Number(v) || 0),
    },
    {
      key: 'mape',
      label: 'MAPE',
      align: 'right',
      render: (v) => {
        const mape = Number(v) || 0;
        return (
          <span
            className={`font-mono font-medium ${
              mape > 20 ? 'text-red-400' : mape > 10 ? 'text-amber-400' : 'text-deloitte-green'
            }`}
          >
            {formatPct(mape)}
          </span>
        );
      },
    },
    {
      key: 'bias',
      label: 'Bias',
      align: 'right',
      render: (v) => {
        const bias = Number(v) || 0;
        return (
          <span className={`font-mono ${bias > 0 ? 'text-amber-400' : 'text-blue-400'}`}>
            {bias > 0 ? '+' : ''}
            {formatPct(bias)}
          </span>
        );
      },
    },
  ];

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end gap-1.5">
        <button
          type="button"
          onClick={handleRescore}
          disabled={rescoring || !versionId}
          className="inline-flex items-center gap-1.5 px-2.5 py-1.5 bg-surface-800 border border-surface-600 text-surface-300 text-xs rounded-lg hover:text-white disabled:opacity-50"
        >
          {rescoring ? <Loader2 className="w-3 h-3 animate-spin" /> : <RefreshCw className="w-3 h-3" />}
          Rescore this version
        </button>
      </div>
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
          <div className="text-xs text-surface-500 uppercase tracking-wider font-semibold">
            Bias ({overall.bias_direction === 'over' ? 'Over' : overall.bias_direction === 'under' ? 'Under' : 'Neutral'})
          </div>
          {overall.bias_dollar !== undefined && (
            <div className="text-xs text-surface-600 mt-0.5">
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

      {(overall.published_wape != null || overall.fva_vs_naive != null) && (
        <div className="grid grid-cols-2 sm:grid-cols-4 gap-2">
          <KPI
            label="Published WAPE"
            value={overall.published_wape != null ? formatPct(overall.published_wape) : '—'}
            color="text-white"
            icon={Target}
          />
          <KPI
            label="Model WAPE"
            value={overall.model_wape != null ? formatPct(overall.model_wape) : '—'}
            color="text-deloitte-teal-light"
            icon={BarChart3}
          />
          <KPI
            label="Naive WAPE"
            value={overall.naive_wape != null ? formatPct(overall.naive_wape) : '—'}
            color="text-surface-300"
            icon={AlertCircle}
          />
          <KPI
            label="FVA vs Naive"
            value={
              overall.fva_vs_naive != null
                ? `${overall.fva_vs_naive > 0 ? '+' : ''}${formatPct(overall.fva_vs_naive)}`
                : '—'
            }
            color={
              overall.fva_vs_naive == null
                ? 'text-surface-400'
                : overall.fva_vs_naive > 0
                  ? 'text-deloitte-green'
                  : 'text-red-400'
            }
            icon={overall.fva_vs_naive != null && overall.fva_vs_naive < 0 ? ArrowDownRight : ArrowUpRight}
          />
        </div>
      )}

      {/* Tabs */}
      <Tabs
        tabs={[
          { id: 'overview', label: 'Trend & Categories' },
          { id: 'models', label: 'Model Compare' },
          { id: 'deviations', label: 'Top Deviations' },
        ]}
        value={activeTab}
        onChange={setActiveTab}
      />

      {activeTab === 'overview' && (
        <div className="space-y-3">
          {/* MAPE trend */}
          <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-4">
            <h4 className="text-xs font-semibold text-surface-400 uppercase tracking-wider mb-3">MAPE Trend Across Versions</h4>
            {mape_trend.length > 1 ? (
              <ResponsiveContainer width="100%" height={180}>
                <LineChart data={mape_trend} margin={{ top: 5, right: 10, left: 0, bottom: 5 }}>
                  <CartesianGrid strokeDasharray="3 3" stroke={chartTheme.grid} />
                  <XAxis dataKey="version" tick={{ fill: chartTheme.axis.fill, fontSize: 12 }} />
                  <YAxis tick={{ fill: chartTheme.axis.fill, fontSize: 12 }} unit="%" />
                  <Tooltip content={<ChartTooltip />} />
                  <ReferenceLine y={5} stroke={COLORS.green} strokeDasharray="5 5" label={{ value: 'Target 5%', fill: COLORS.green, fontSize: 12 }} />
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
                  <CartesianGrid strokeDasharray="3 3" stroke={chartTheme.grid} />
                  <XAxis dataKey="version" tick={{ fill: chartTheme.axis.fill, fontSize: 12 }} />
                  <YAxis tick={{ fill: chartTheme.axis.fill, fontSize: 12 }} unit="%" />
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
                  <CartesianGrid strokeDasharray="3 3" stroke={chartTheme.grid} />
                  <XAxis dataKey="category" tick={{ fill: chartTheme.axis.fill, fontSize: 12 }} />
                  <YAxis tick={{ fill: chartTheme.axis.fill, fontSize: 12 }} unit="%" />
                  <Tooltip content={<ChartTooltip />} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
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
                  <CartesianGrid strokeDasharray="3 3" stroke={chartTheme.grid} />
                  <XAxis dataKey="model" tick={{ fill: chartTheme.axis.fill, fontSize: 12 }} />
                  <YAxis tick={{ fill: chartTheme.axis.fill, fontSize: 12 }} unit="%" />
                  <Tooltip content={<ChartTooltip />} />
                  <Legend wrapperStyle={{ fontSize: 12 }} />
                  <Bar dataKey="avg_mape" name="Avg MAPE" fill={COLORS.green} radius={[3, 3, 0, 0]} />
                  <Bar dataKey="best_mape" name="Best MAPE" fill={COLORS.teal} radius={[3, 3, 0, 0]} />
                  <Bar dataKey="worst_mape" name="Worst MAPE" fill={COLORS.red} radius={[3, 3, 0, 0]} />
                </BarChart>
              </ResponsiveContainer>

              <DataTable
                title="Model metrics"
                columns={[
                  { key: 'model', label: 'Model' },
                  {
                    key: 'count',
                    label: 'Forecasts',
                    align: 'right',
                    render: (v) => String(v ?? 0),
                  },
                  {
                    key: 'avg_mape',
                    label: 'Avg MAPE',
                    align: 'right',
                    render: (v) => {
                      const mape = Number(v) || 0;
                      return (
                        <span className={mape > 10 ? 'text-red-400' : 'text-deloitte-green'}>
                          {formatPct(mape)}
                        </span>
                      );
                    },
                  },
                  {
                    key: 'best_mape',
                    label: 'Best',
                    align: 'right',
                    render: (v) => formatPct(Number(v) || 0),
                  },
                  {
                    key: 'worst_mape',
                    label: 'Worst',
                    align: 'right',
                    render: (v) => formatPct(Number(v) || 0),
                  },
                ]}
                rows={model_performance as Record<string, unknown>[]}
                maxHeight={220}
                exportFilename={`model_performance_${versionId || 'export'}`}
                getRowId={(row) => String(row.model ?? '')}
              />
            </>
          ) : (
            <p className="text-surface-500 text-xs text-center py-6">No model performance data</p>
          )}
        </div>
      )}

      {activeTab === 'deviations' && (
        <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl overflow-hidden p-2">
          <h4 className="text-xs font-semibold text-white flex items-center gap-1.5 px-2 py-1.5 mb-1">
            <AlertCircle className="w-3.5 h-3.5 text-red-400" />
            Top {top_deviations.length} Largest Deviations
          </h4>
          <DataTable
            columns={deviationColumns}
            rows={deviationRows}
            maxHeight={300}
            exportFilename={`accuracy_${versionId || 'export'}`}
            getRowClassName={() => 'hover:bg-red-500/5'}
            rowActions={(row) =>
              versionId && row.line_item_id != null ? (
                <button
                  type="button"
                  title="Review item"
                  onClick={(e) => {
                    e.stopPropagation();
                    openPanel('review_dashboard', {
                      version_id: versionId,
                      focus_line_item_id: row.line_item_id as number,
                    });
                  }}
                  className="text-surface-400 hover:text-deloitte-green"
                >
                  <ExternalLink className="w-3 h-3" />
                </button>
              ) : null
            }
          />
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
      <div className="text-xs text-surface-500 uppercase tracking-wider font-semibold">{label}</div>
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
