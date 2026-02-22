import {
  BarChart, Bar, LineChart, Line, AreaChart, Area,
  XAxis, YAxis, CartesianGrid, Tooltip, Legend,
  ResponsiveContainer, Cell, ReferenceLine,
  ComposedChart, PieChart, Pie, Scatter, ScatterChart,
} from 'recharts';

// Deloitte palette
const COLORS = {
  green: '#86BC25',
  greenDark: '#046A38',
  greenLight: '#C4D600',
  teal: '#0076A8',
  tealLight: '#00A3E0',
  blue: '#012169',
  blueLight: '#0097A9',
  warmGray: '#53565A',
  coolGray: '#97999B',
  lightGray: '#D0D0CE',
  red: '#E84855',
  amber: '#FFB547',
};

const SERIES_COLORS = [
  COLORS.green, COLORS.teal, COLORS.tealLight,
  COLORS.greenLight, COLORS.blueLight, COLORS.amber,
];

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
  } = data;

  const labelFor = (key: string) => y_labels?.[key] || key.replace(/_/g, ' ');

  return (
    <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-4 my-2">
      {title && (
        <h4 className="text-sm font-semibold text-white mb-3 flex items-center gap-2">
          <div className="w-0.5 h-3.5 bg-deloitte-green rounded-full" />
          {title}
        </h4>
      )}

      <ResponsiveContainer width="100%" height={height}>
        {chart_type === 'bar' ? (
          <BarChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {show_legend && <Legend wrapperStyle={{ fontSize: 11, color: '#97999B' }} />}
            {y_keys.map((key, i) => (
              <Bar key={key} dataKey={key} name={labelFor(key)} fill={SERIES_COLORS[i % SERIES_COLORS.length]} radius={[3, 3, 0, 0]} />
            ))}
            {reference_line && <ReferenceLine y={reference_line.y} stroke={COLORS.amber} strokeDasharray="5 5" label={{ value: reference_line.label, fill: COLORS.amber, fontSize: 10 }} />}
          </BarChart>

        ) : chart_type === 'stacked_bar' ? (
          <BarChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {show_legend && <Legend wrapperStyle={{ fontSize: 11, color: '#97999B' }} />}
            {y_keys.map((key, i) => (
              <Bar key={key} dataKey={key} name={labelFor(key)} stackId="a" fill={SERIES_COLORS[i % SERIES_COLORS.length]} />
            ))}
          </BarChart>

        ) : chart_type === 'line' ? (
          <LineChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {show_legend && <Legend wrapperStyle={{ fontSize: 11, color: '#97999B' }} />}
            {y_keys.map((key, i) => (
              <Line key={key} type="monotone" dataKey={key} name={labelFor(key)} stroke={SERIES_COLORS[i % SERIES_COLORS.length]} strokeWidth={2} dot={{ r: 3 }} activeDot={{ r: 5 }} />
            ))}
            {reference_line && <ReferenceLine y={reference_line.y} stroke={COLORS.amber} strokeDasharray="5 5" />}
          </LineChart>

        ) : chart_type === 'area' ? (
          <AreaChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <defs>
              {y_keys.map((key, i) => (
                <linearGradient key={key} id={`grad-${key}`} x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor={SERIES_COLORS[i % SERIES_COLORS.length]} stopOpacity={0.3} />
                  <stop offset="95%" stopColor={SERIES_COLORS[i % SERIES_COLORS.length]} stopOpacity={0} />
                </linearGradient>
              ))}
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {show_legend && <Legend wrapperStyle={{ fontSize: 11, color: '#97999B' }} />}
            {y_keys.map((key, i) => (
              <Area key={key} type="monotone" dataKey={key} name={labelFor(key)} stroke={SERIES_COLORS[i % SERIES_COLORS.length]} fill={`url(#grad-${key})`} strokeWidth={2} />
            ))}
          </AreaChart>

        ) : chart_type === 'confidence' ? (
          <AreaChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <defs>
              <linearGradient id="gradCI" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={COLORS.teal} stopOpacity={0.2} />
                <stop offset="95%" stopColor={COLORS.teal} stopOpacity={0} />
              </linearGradient>
            </defs>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {show_legend && <Legend wrapperStyle={{ fontSize: 11, color: '#97999B' }} />}
            <Area type="monotone" dataKey="p90" name="P90 (Upside)" stroke={COLORS.teal} fill="url(#gradCI)" strokeWidth={1} strokeDasharray="4 4" />
            <Area type="monotone" dataKey="p10" name="P10 (Downside)" stroke={COLORS.teal} fill="url(#gradCI)" strokeWidth={1} strokeDasharray="4 4" />
            <Line type="monotone" dataKey="p50" name="P50 (Forecast)" stroke={COLORS.green} strokeWidth={2.5} dot={{ r: 4, fill: COLORS.green }} />
            {chartData[0]?.actual !== undefined && (
              <Line type="monotone" dataKey="actual" name="Actual" stroke={COLORS.amber} strokeWidth={2} dot={{ r: 3 }} />
            )}
          </AreaChart>

        ) : chart_type === 'bridge' ? (
          <BarChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
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
            >
              {chartData.map((_: any, i: number) => (
                <Cell key={i} fill={SERIES_COLORS[i % SERIES_COLORS.length]} />
              ))}
            </Pie>
            <Tooltip content={<CustomTooltip format={format} />} />
            {show_legend && <Legend wrapperStyle={{ fontSize: 11, color: '#97999B' }} />}
          </PieChart>

        ) : chart_type === 'combo' ? (
          <ComposedChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {show_legend && <Legend wrapperStyle={{ fontSize: 11, color: '#97999B' }} />}
            {y_keys.slice(0, 1).map((key) => (
              <Bar key={key} dataKey={key} name={labelFor(key)} fill={COLORS.green} radius={[3, 3, 0, 0]} />
            ))}
            {y_keys.slice(1).map((key, i) => (
              <Line key={key} type="monotone" dataKey={key} name={labelFor(key)} stroke={SERIES_COLORS[(i + 1) % SERIES_COLORS.length]} strokeWidth={2} dot={{ r: 3 }} />
            ))}
          </ComposedChart>

        ) : chart_type === 'variance' ? (
          /* Variance chart: shows positive/negative deviations with color coding */
          <ComposedChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {show_legend && <Legend wrapperStyle={{ fontSize: 11, color: '#97999B' }} />}
            <ReferenceLine y={0} stroke={COLORS.coolGray} strokeDasharray="3 3" />
            {y_keys.map((key) => (
              <Bar key={key} dataKey={key} name={labelFor(key)} radius={[3, 3, 0, 0]}>
                {chartData.map((entry: any, i: number) => (
                  <Cell key={i} fill={entry[key] >= 0 ? COLORS.green : COLORS.red} />
                ))}
              </Bar>
            ))}
            {reference_line && (
              <ReferenceLine y={reference_line.y} stroke={COLORS.amber} strokeDasharray="5 5"
                label={{ value: reference_line.label, fill: COLORS.amber, fontSize: 10 }} />
            )}
          </ComposedChart>

        ) : chart_type === 'grouped_bar' ? (
          /* Grouped bar: side-by-side bars (forecast vs actual, etc.) */
          <BarChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {show_legend && <Legend wrapperStyle={{ fontSize: 11, color: '#97999B' }} />}
            {y_keys.map((key, i) => (
              <Bar key={key} dataKey={key} name={labelFor(key)} fill={SERIES_COLORS[i % SERIES_COLORS.length]} radius={[3, 3, 0, 0]} barSize={20} />
            ))}
            {reference_line && <ReferenceLine y={reference_line.y} stroke={COLORS.amber} strokeDasharray="5 5" label={{ value: reference_line.label, fill: COLORS.amber, fontSize: 10 }} />}
          </BarChart>

        ) : chart_type === 'scatter' ? (
          /* Scatter plot: forecast vs actual correlation */
          <ComposedChart data={chartData} margin={{ top: 5, right: 10, left: 10, bottom: 5 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={y_keys[0] || x_key} tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} name={labelFor(y_keys[0] || x_key)} />
            <YAxis dataKey={y_keys[1]} tick={{ fill: '#97999B', fontSize: 11 }} axisLine={{ stroke: '#3a3d42' }} tickFormatter={(v) => formatValue(v, format)} name={labelFor(y_keys[1])} />
            <Tooltip content={<CustomTooltip format={format} />} />
            {show_legend && <Legend wrapperStyle={{ fontSize: 11, color: '#97999B' }} />}
            <Scatter name={labelFor(y_keys[1] || 'values')} fill={COLORS.teal} />
          </ComposedChart>

        ) : (
          <BarChart data={chartData}>
            <CartesianGrid strokeDasharray="3 3" stroke="#2a2d32" />
            <XAxis dataKey={x_key} tick={{ fill: '#97999B', fontSize: 11 }} />
            <YAxis tick={{ fill: '#97999B', fontSize: 11 }} />
            <Tooltip />
            {y_keys.map((key, i) => (
              <Bar key={key} dataKey={key} fill={SERIES_COLORS[i % SERIES_COLORS.length]} />
            ))}
          </BarChart>
        )}
      </ResponsiveContainer>
    </div>
  );
}
