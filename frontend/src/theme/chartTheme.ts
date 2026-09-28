/** Shared chart color tokens — replaces duplicated constants across panels. */
export const chartTheme = {
  colors: {
    primary: '#0E6E5C',
    secondary: '#3D6E8A',
    tertiary: '#8B8880',
    danger: '#B23B2E',
    warning: '#B8792E',
    muted: '#726F68',
    band: 'rgba(14, 110, 92, 0.15)',
    grid: 'rgba(33, 31, 28, 0.08)',
    axis: '#8B8880',
  },
  series: ['#0E6E5C', '#3D6E8A', '#B8792E', '#B23B2E', '#1D6F7A', '#6B4C9A', '#8B8880', '#A3641F'],
  fontSize: 12,
  tooltip: {
    contentStyle: {
      background: 'var(--rf-bg-elevated)',
      border: '1px solid var(--rf-border)',
      borderRadius: 8,
      fontSize: 12,
      color: 'var(--rf-text)',
    },
  },
  axis: {
    fill: 'var(--rf-chart-axis)',
  },
  grid: 'var(--rf-chart-grid)',
} as const;
