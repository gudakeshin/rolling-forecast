import { useMemo, useState } from 'react';
import {
  BarChart, Bar, LineChart, Line, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, Cell, ReferenceLine,
  ComposedChart, PieChart, Pie, Scatter, Brush,
} from 'recharts';
import { usePanelStore } from '../../../store/panelStore';
import { chartTheme } from '../../../theme/chartTheme';
import { DataTable } from '../../ui/DataTable';

const COLORS = {
  green: chartTheme.colors.primary,
  greenDark: '#046A38',
  greenLight: '#C4D600',
  teal: '#0076A8',
  tealLight: chartTheme.colors.secondary,
  blue: '#012169',
  blueLight: '#0097A9',
  warmGray: '#53565A',
  coolGray: chartTheme.colors.tertiary,
  lightGray: '#D0D0CE',
  red: chartTheme.colors.danger,
  amber: chartTheme.colors.warning,
};

const SERIES_COLORS = [...chartTheme.series];

type ChartType =
  | 'bar' | 'line' | 'area' | 'bridge' | 'confidence'
  | 'pie' | 'stacked_bar' | 'combo' | 'variance'
  | 'grouped_bar' | 'scatter';

interface ChartData {
  chart_type: ChartType;
  title?: string;
  data: Record<string, any>[];
  x_key: string;
  y_keys: string[];
  y_labels?: Record<string, string>;
  reference_line?: { y: number; label: string };
  show_legend?: boolean;
  format?: 'currency' | 'percent' | 'number';
  height?: number;
  enable_brush?: boolean;
  drill_panel?: string;
}

interface Props {
  data: ChartData;
}

function formatValue(val: number, fmt?: string): string {
  if (fmt === 'currency') {
    if (Math.abs(val) >= 1_000_000) return `$${(val / 1_000_000).toFixed(1)}M`;
    if (Math.abs(val) >= 1_000) return `$${(val / 1_000).toFixed(0)}K`;
    return `$${val.toFixed(0)}`;
  }
  if (fmt === 'percent') return `${val.toFixed(1)}%`;
  if (Math.abs(val) >= 1_000_000) return `${(val / 1_000_000).toFixed(1)}M`;
  if (Math.abs(val) >= 1_000) return `${(val / 1_000).toFixed(0)}K`;
  return val.toFixed(0);
}

const CustomTooltip = ({ active, payload, label, format }: any) => {
  if (!active || !payload?.length) return null;
  return (
    <div className="bg-surface-800 border border-surface-600 rounded-lg px-3 py-2 shadow-xl">
      <p className="text-xs text-surface-400 mb-1">{label}</p>
      {payload.map((p: any, i: number) => (
        <p key={i} className="text-xs font-medium" style={{ color: p.color }}>
          {p.name}: {formatValue(p.value, format)}
        </p>
      ))}
    </div>
  );
};

export function ChartRenderer({ data }: Props) {
  const {
    chart_type, title, data: chartData, x_key, y_keys,
    y_labels, reference_line, show_legend = true, format, height = 280,
    enable_brush = true, drill_panel = 'forecast_table',
  } = data;

  const openPanel = usePanelStore((s) => s.openPanel);
  const [hidden, setHidden] = useState<Set<string>>(new Set());
  const [viewAsTable, setViewAsTable] = useState(false);

  const labelFor = (key: string) => y_labels?.[key] || key.replace(/_/g, ' ');
  const visibleKeys = useMemo(
    () => y_keys.filter((k) => !hidden.has(k)),
    [y_keys, hidden],
  );

  const tableColumns = useMemo(
    () => [
      { key: x_key, label: x_key },
      ...y_keys.map((k) => ({ key: k, label: labelFor(k) })),
    ],
    [x_key, y_keys, y_labels],
  );

  const onLegendClick = (e: any) => {
    const key = e?.dataKey;
    if (!key) return;
    setHidden((prev) => {
      const next = new Set(prev);
      if (next.has(key)) next.delete(key);
      else next.add(key);
      return next;
    });
  };

  const onPointClick = (payload: any) => {
    const point = payload?.activePayload?.[0]?.payload || payload;
    if (!point) return;
    const period = point[x_key];
    openPanel(drill_panel, {
      period: period != null ? String(period) : undefined,
      chart_title: title,
    });
  };

  const brush = enable_brush && chartData.length > 6 ? (
    <Brush dataKey={x_key} height={18} stroke={COLORS.teal} travellerWidth={8} />
  ) : null;

  const commonLegend = show_legend ? (
    <Legend
      wrapperStyle={{ fontSize: 12, color: '#97999B', cursor: 'pointer' }}
      onClick={onLegendClick}
    />
  ) : null;

  return (
    <div
      className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-4 my-2"
      role="img"
      aria-label={title ? `Chart: ${title}` : `Chart of type ${chart_type}`}
    >
      <div className="flex items-center justify-between gap-2 mb-3">
        {title ? (
          <h4 className="text-sm font-semibold text-white flex items-center gap-2">
            <div className="w-0.5 h-3.5 bg-deloitte-green rounded-full" />
            {title}
          </h4>
        ) : (
          <span />
        )}
        <button
          type="button"
          className="text-xs text-surface-400 hover:text-white underline-offset-2 hover:underline"
          onClick={() => setViewAsTable((v) => !v)}
          aria-pressed={viewAsTable}
        >
          {viewAsTable ? 'View as chart' : 'View as table'}
        </button>
      </div>

      {viewAsTable ? (
        <DataTable columns={tableColumns} rows={chartData as Record<string, unknown>[]} />
      ) : (
      <ResponsiveContainer width="100%" height={height}>
        {chart_type === 'bar' ? (
          <BarChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }} onClick={onPointClick}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {commonLegend}
            {visibleKeys.map((key, i) => (
              <Bar key={key} dataKey={key} name={labelFor(key)} fill={SERIES_COLORS[i % SERIES_COLORS.length]} radius={[3, 3, 0, 0]} />
            ))}
            {reference_line && <ReferenceLine y={reference_line.y} stroke={COLORS.amber} strokeDasharray="5 5" label={{ value: reference_line.label, fill: COLORS.amber, fontSize: 12 }} />}
            {brush}
          </BarChart>

        ) : chart_type === 'stacked_bar' ? (
          <BarChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }} onClick={onPointClick}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {commonLegend}
            {visibleKeys.map((key, i) => (
              <Bar key={key} dataKey={key} name={labelFor(key)} stackId="a" fill={SERIES_COLORS[i % SERIES_COLORS.length]} />
            ))}
            {brush}
          </BarChart>

        ) : chart_type === 'line' ? (
          <LineChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }} onClick={onPointClick}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {commonLegend}
            {visibleKeys.map((key, i) => (
              <Line key={key} type="monotone" dataKey={key} name={labelFor(key)} stroke={SERIES_COLORS[i % SERIES_COLORS.length]} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
            ))}
            {reference_line && <ReferenceLine y={reference_line.y} stroke={COLORS.amber} strokeDasharray="5 5" />}
            {brush}
          </LineChart>

        ) : chart_type === 'area' ? (
          <AreaChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }} onClick={onPointClick}>
            <defs>
              {visibleKeys.map((key, i) => (
                <linearGradient key={key} id={`grad-${key}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={SERIES_COLORS[i % SERIES_COLORS.length]} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={SERIES_COLORS[i % SERIES_COLORS.length]} stopOpacity={0} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {commonLegend}
            {visibleKeys.map((key, i) => (
              <Area key={key} type="monotone" dataKey={key} name={labelFor(key)} stroke={SERIES_COLORS[i % SERIES_COLORS.length]} fill={`url(#grad-${key})`} strokeWidth={2} />
            ))}
            {brush}
          </AreaChart>

        ) : chart_type === 'confidence' ? (
          <AreaChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }} onClick={onPointClick}>
            <defs>
              <linearGradient id="gradCI" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={COLORS.teal} stopOpacity={0.2} />
                <stop offset="95%" stopColor={COLORS.teal} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {commonLegend}
            <Area type="monotone" dataKey="p90" name="P90 (Upside)" stroke={COLORS.teal} fill="url(#gradCI)" strokeWidth={1} strokeDasharray="4 4" />
            <Area type="monotone" dataKey="p10" name="P10 (Downside)" stroke={COLORS.teal} fill="url(#gradCI)" strokeWidth={1} strokeDasharray="4 4" />
            <Line type="monotone" dataKey="p50" name="P50 (Forecast)" stroke={COLORS.green} strokeWidth={2.5} dot={{ r: 4, fill: COLORS.green }} />
            {chartData[0]?.actual !== undefined && (
              <Line type="monotone" dataKey="actual" name="Actual" stroke={COLORS.amber} strokeWidth={2} dot={{ r: 3 }} />
            )}
            {brush}
          </AreaChart>

        ) : chart_type === 'bridge' ? (
          <BarChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }} onClick={onPointClick}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            <Bar dataKey="invisible" stackId="bridge" fill="transparent" />
            <Bar dataKey="value" stackId="bridge" radius={[3, 3, 0, 0]}>
              {chartData.map((entry: any, i: number) => (
                <Cell
                  key={i}
                  fill={
                    entry.is_total ? COLORS.teal
                    : entry.value >= 0 ? COLORS.green
                    : COLORS.red
                  }
                />
              ))}
            </Bar>
          </BarChart>

        ) : chart_type === 'pie' ? (
          <PieChart>
            <Pie
              data={chartData}
              cx="50%"
              cy="50%"
              innerRadius={60}
              outerRadius={90}
              dataKey={y_keys[0]}
              nameKey={x_key}
              paddingAngle={3}
              label={({ name, percent }: any) => `${name} ${(percent * 100).toFixed(0)}%`}
              onClick={(entry: any) => openPanel(drill_panel, { category: entry?.name, chart_title: title })}
            >
              {chartData.map((_: any, i: number) => (
                <Cell key={i} fill={SERIES_COLORS[i % SERIES_COLORS.length]} />
              ))}
            </Pie>
            <Tooltip content={<CustomTooltip format={format} />} />
            {commonLegend}
          </PieChart>

        ) : chart_type === 'combo' ? (
          <ComposedChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }} onClick={onPointClick}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {commonLegend}
            {visibleKeys.slice(0, 1).map((key) => (
              <Bar key={key} dataKey={key} name={labelFor(key)} fill={COLORS.green} radius={[3, 3, 0, 0]} />
            ))}
            {visibleKeys.slice(1).map((key, i) => (
              <Line key={key} type="monotone" dataKey={key} name={labelFor(key)} stroke={SERIES_COLORS[(i + 1) % SERIES_COLORS.length]} strokeWidth={2} dot={{ r: 3 }} />
            ))}
            {brush}
          </ComposedChart>

        ) : chart_type === 'variance' ? (
          <ComposedChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }} onClick={onPointClick}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {commonLegend}
            <ReferenceLine y={0} stroke={COLORS.coolGray} strokeDasharray="3 3" />
            {visibleKeys.map((key) => (
              <Bar key={key} dataKey={key} name={labelFor(key)} radius={[3, 3, 0, 0]}>
                {chartData.map((entry: any, i: number) => (
                  <Cell key={i} fill={entry[key] >= 0 ? COLORS.green : COLORS.red} />
                ))}
              </Bar>
            ))}
            {reference_line && (
              <ReferenceLine y={reference_line.y} stroke={COLORS.amber} strokeDasharray="5 5"
                label={{ value: reference_line.label, fill: COLORS.amber, fontSize: 12 }} />
            )}
            {brush}
          </ComposedChart>

        ) : chart_type === 'grouped_bar' ? (
          <BarChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }} onClick={onPointClick}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {commonLegend}
            {visibleKeys.map((key, i) => (
              <Bar key={key} dataKey={key} name={labelFor(key)} fill={SERIES_COLORS[i % SERIES_COLORS.length]} radius={[3, 3, 0, 0]} barSize={20} />
            ))}
            {reference_line && <ReferenceLine y={reference_line.y} stroke={COLORS.amber} strokeDasharray="5 5" label={{ value: reference_line.label, fill: COLORS.amber, fontSize: 12 }} />}
            {brush}
          </BarChart>

        ) : chart_type === 'scatter' ? (
          <ComposedChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={y_keys[0] || x_key} tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} name={labelFor(y_keys[0] || x_key)} />
            <YAxis dataKey={y_keys[1]} tick={{ fill: '#97999B', fontSize: 12 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} name={labelFor(y_keys[1])} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {commonLegend}
            <Scatter name={labelFor(y_keys[1] || 'values')} fill={COLORS.teal} />
          </ComposedChart>

        ) : (
          <BarChart data={chartData} onClick={onPointClick}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 12 }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 12 }} />
            <Tooltip />
            {visibleKeys.map((key, i) => (
              <Bar key={key} dataKey={key} fill={SERIES_COLORS[i % SERIES_COLORS.length]} />
            ))}
            {brush}
          </BarChart>
        )}
      </ResponsiveContainer>
      )}
      {!viewAsTable && (
        <p className="text-xs text-surface-500 mt-1">
          Click legend to toggle series · drag brush to zoom · click a point to drill
        </p>
      )}
    </div>
  );
}
