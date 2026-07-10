/** Shared chart color tokens — replaces duplicated constants across panels. */
export const chartTheme = {
  colors: {
    primary: '#86BC25',
    secondary: '#00A3E0',
    tertiary: '#BBBCBC',
    danger: '#F07178',
    warning: '#E6C07B',
    muted: '#5C6370',
    band: 'rgba(134, 188, 37, 0.15)',
    grid: 'rgba(255,255,255,0.06)',
    axis: '#9CA3AF',
  },
  series: ['#86BC25', '#00A3E0', '#E6C07B', '#C678DD', '#56B6C2', '#E06C75', '#61AFEF', '#98C379'],
  fontSize: 12,
  tooltip: {
    contentStyle: {
      background: '#1a1d23',
      border: '1px solid #2d323c',
      borderRadius: 8,
      fontSize: 12,
    },
  },
} as const;
