import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import {
  TrendingUp, Download, ArrowLeft, Shield, AlertTriangle, BarChart3, CheckSquare,
} from 'lucide-react';
import { apiGet } from '../../api/client';
import { useAuthStore } from '../../store/authStore';
import { usePanelStore } from '../../store/panelStore';
import { toast } from '../../store/toastStore';
import { PanelContainer } from '../panels/PanelContainer';
import { DataTable, type DataTableColumn } from '../ui/DataTable';

interface LatestResponse {
  version: {
    id: string;
    name: string;
    status: string;
    horizon_months: number;
    published_at: string | null;
    high_confidence_count: number;
    medium_confidence_count: number;
    low_confidence_count: number;
    override_count: number;
  } | null;
  kpis?: {
    category_totals: { category: string; total: number }[];
    confidence: { high: number; medium: number; low: number };
  };
  message?: string;
}

function formatCurrency(val: number): string {
  if (Math.abs(val) >= 1_000_000) return `$${(val / 1_000_000).toFixed(1)}M`;
  if (Math.abs(val) >= 1_000) return `$${(val / 1_000).toFixed(0)}K`;
  return `$${val.toFixed(0)}`;
}

export function ExecutiveLandingPage() {
  const token = useAuthStore((s) => s.token);
  const openPanel = usePanelStore((s) => s.openPanel);
  const isPanelOpen = usePanelStore((s) => s.isOpen);
  const [data, setData] = useState<LatestResponse | null>(null);
  const [bridge, setBridge] = useState<any>(null);
  const [error, setError] = useState('');
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);

  useEffect(() => {
    (async () => {
      try {
        const latest = await apiGet<LatestResponse>('/executive/latest');
        setData(latest);
        if (latest.version?.id) {
          try {
            const b = await apiGet<any>(`/executive/budget-bridge/${latest.version.id}?page_size=100`);
            setBridge(b);
          } catch {
            toast.error('Budget bridge unavailable');
          }
        }
      } catch (e: any) {
        const message = e.message || 'Failed to load executive view';
        setError(message);
        toast.error(message);
      } finally {
        setLoading(false);
      }
    })();
  }, []);

  const downloadPack = async (format: 'pptx' | 'pdf') => {
    if (!data?.version?.id || !token) return;
    setExporting(true);
    try {
      const res = await fetch(`/api/executive/board-pack/${data.version.id}?format=${format}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!res.ok) throw new Error('Export failed');
      const blob = await res.blob();
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      a.href = url;
      a.download = `${data.version.name}_board_pack.${format}`;
      a.click();
      URL.revokeObjectURL(url);
      toast.success(`Board pack downloaded (${format.toUpperCase()})`);
    } catch (e: any) {
      const message = e.message || 'Export failed';
      setError(message);
      toast.error(message);
    } finally {
      setExporting(false);
    }
  };

  if (loading) {
    return (
      <div className="min-h-screen bg-black flex items-center justify-center text-surface-400">
        Loading executive view…
      </div>
    );
  }

  return (
    <div className="min-h-screen bg-black text-white">
      <header className="h-14 flex items-center justify-between px-6 border-b border-surface-700/50 bg-black/60">
        <div className="flex items-center gap-3">
          <Link to="/" className="text-surface-400 hover:text-white flex items-center gap-1 text-sm">
            <ArrowLeft className="w-4 h-4" /> Chat
          </Link>
          <div className="w-1 h-6 bg-deloitte-green rounded-full" />
          <span className="font-semibold">Executive View</span>
        </div>
        {data?.version && (
          <div className="flex gap-2">
            <button
              onClick={() => openPanel('review_dashboard', { version_id: data.version!.id })}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-surface-800 text-surface-300 border border-surface-700 rounded-lg hover:text-white"
            >
              <Shield className="w-3.5 h-3.5" /> Review dashboard
            </button>
            <button
              onClick={() => openPanel('approvals', { version_id: data.version!.id })}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-surface-800 text-surface-300 border border-surface-700 rounded-lg hover:text-white"
            >
              <CheckSquare className="w-3.5 h-3.5" /> Approvals
            </button>
            <button
              onClick={() => downloadPack('pptx')}
              disabled={exporting}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-deloitte-green/20 text-deloitte-green border border-deloitte-green/30 rounded-lg"
            >
              <Download className="w-3.5 h-3.5" /> PPT
            </button>
            <button
              onClick={() => downloadPack('pdf')}
              disabled={exporting}
              className="flex items-center gap-1.5 px-3 py-1.5 text-xs bg-surface-800 text-surface-300 border border-surface-700 rounded-lg"
            >
              <Download className="w-3.5 h-3.5" /> PDF
            </button>
          </div>
        )}
      </header>

      {isPanelOpen && <PanelContainer />}

      <main className="max-w-5xl mx-auto px-6 py-8 space-y-8">
        {error && (
          <div className="flex items-center gap-2 text-amber-400 text-sm bg-amber-500/10 border border-amber-500/20 rounded-lg p-3">
            <AlertTriangle className="w-4 h-4" /> {error}
          </div>
        )}

        {!data?.version ? (
          <p className="text-surface-400">{data?.message || 'No published forecast yet.'}</p>
        ) : (
          <>
            <section className="space-y-2">
              <div className="text-xs text-deloitte-green uppercase tracking-widest font-semibold">
                Latest {data.version.status} forecast
              </div>
              <h1 className="text-3xl font-bold tracking-tight">{data.version.name}</h1>
              <p className="text-surface-400 text-sm">
                {data.version.horizon_months}-month horizon
                {data.version.published_at ? ` · Published ${new Date(data.version.published_at).toLocaleDateString()}` : ''}
              </p>
            </section>

            <section className="grid grid-cols-2 md:grid-cols-4 gap-4">
              {[
                { label: 'High confidence', value: data.version.high_confidence_count, icon: Shield },
                { label: 'Medium', value: data.version.medium_confidence_count, icon: TrendingUp },
                { label: 'Low / review', value: data.version.low_confidence_count, icon: AlertTriangle },
                { label: 'Overrides', value: data.version.override_count, icon: BarChart3 },
              ].map((kpi) => (
                <div key={kpi.label} className="bg-surface-900 border border-surface-700/50 rounded-xl p-4">
                  <kpi.icon className="w-4 h-4 text-deloitte-green mb-2" />
                  <div className="text-2xl font-bold">{kpi.value}</div>
                  <div className="text-xs text-surface-500">{kpi.label}</div>
                </div>
              ))}
            </section>

            {data.kpis?.category_totals && data.kpis.category_totals.length > 0 && (
              <section>
                <h2 className="text-sm font-semibold text-surface-300 mb-3">Category totals</h2>
                <div className="space-y-2">
                  {data.kpis.category_totals.map((row) => (
                    <div
                      key={row.category}
                      className="flex justify-between items-center bg-surface-900/80 border border-surface-700/40 rounded-lg px-4 py-2.5"
                    >
                      <span className="text-sm text-surface-300">{row.category}</span>
                      <span className="font-semibold">{formatCurrency(row.total)}</span>
                    </div>
                  ))}
                </div>
              </section>
            )}

            {bridge?.rows && (
              <section>
                <h2 className="text-sm font-semibold text-surface-300 mb-3">
                  Budget / prior bridge
                  {bridge.materiality_pct != null && (
                    <span className="text-surface-500 font-normal"> · materiality ≥ {bridge.materiality_pct}%</span>
                  )}
                </h2>
                <DataTable
                  title="Bridge lines"
                  maxHeight={420}
                  exportFilename="budget_bridge"
                  columns={
                    [
                      {
                        key: 'line_item',
                        label: 'Line',
                        render: (v, row) => (
                          <span>
                            {String(v)}
                            {row.material ? (
                              <span className="ml-2 text-xs text-amber-400">material</span>
                            ) : null}
                          </span>
                        ),
                      },
                      {
                        key: 'current_forecast',
                        label: 'Forecast',
                        align: 'right',
                        render: (v) => formatCurrency(Number(v) || 0),
                      },
                      {
                        key: 'budget',
                        label: 'Budget',
                        align: 'right',
                        render: (v) => formatCurrency(Number(v) || 0),
                      },
                      {
                        key: 'prior_forecast',
                        label: 'Prior',
                        align: 'right',
                        render: (v) => formatCurrency(Number(v) || 0),
                      },
                      {
                        key: 'variance_vs_budget',
                        label: 'Var vs Budget',
                        align: 'right',
                        render: (v) => {
                          const n = Number(v) || 0;
                          return (
                            <span className={n < 0 ? 'text-red-400' : 'text-green-400'}>
                              {formatCurrency(n)}
                            </span>
                          );
                        },
                      },
                    ] as DataTableColumn<Record<string, unknown>>[]
                  }
                  rows={(bridge.rows || []) as Record<string, unknown>[]}
                />
              </section>
            )}
          </>
        )}
      </main>
    </div>
  );
}
