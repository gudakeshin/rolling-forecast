/** FX rates, reporting currency and fiscal calendar. */
import { useEffect, useState } from 'react';
import { apiGet, apiPost, apiPut, uploadFile } from '../../api/client';
import { FxRate, type AdminTabProps } from './AdminConsoleShared';

export function AdminFxTab({ onError, onMessage }: AdminTabProps) {
  const [fxRates, setFxRates] = useState<FxRate[]>([]);
  const [reportingCurrency, setReportingCurrency] = useState('USD');
  const [fiscalCalendar, setFiscalCalendar] = useState({
    calendar_type: 'gregorian_month',
    fiscal_year_start_month: 2,
    week_start: 6,
  });
  const [fxForm, setFxForm] = useState({
    from_currency: 'EUR',
    to_currency: 'USD',
    period: new Date().toISOString().slice(0, 7),
    rate: '1.0',
    rate_type: 'average',
  });

  const load = async () => {
    try {
      const [rates, currency, calendar] = await Promise.all([
        apiGet<FxRate[]>('/admin/fx/rates'),
        apiGet<{ reporting_currency: string }>('/admin/fx/settings/reporting_currency'),
        apiGet<{
          calendar_type: string;
          fiscal_year_start_month: number;
          week_start: number;
        }>('/admin/fx/settings/fiscal_calendar'),
      ]);
      setFxRates(rates);
      setReportingCurrency(currency.reporting_currency);
      setFiscalCalendar(calendar);
    } catch (e: any) {
      onError(e.message || 'Failed to load');
    }
  };

  useEffect(() => {
    void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const saveReportingCurrency = async () => {
    try {
      const res = await apiPut<{ reporting_currency: string }>(
        '/admin/fx/settings/reporting_currency',
        { currency: reportingCurrency },
      );
      setReportingCurrency(res.reporting_currency);
      onMessage(`Reporting currency set to ${res.reporting_currency}`);
    } catch (e: any) {
      onError(e.message);
    }
  };

  const saveFiscalCalendar = async () => {
    try {
      const res = await apiPut<typeof fiscalCalendar>(
        '/admin/fx/settings/fiscal_calendar',
        fiscalCalendar,
      );
      setFiscalCalendar(res);
      onMessage(`Fiscal calendar set to ${res.calendar_type}`);
    } catch (e: any) {
      onError(e.message);
    }
  };

  const createFxRate = async () => {
    try {
      await apiPost('/admin/fx/rates', {
        ...fxForm,
        from_currency: fxForm.from_currency.toUpperCase(),
        to_currency: fxForm.to_currency.toUpperCase(),
        rate: Number(fxForm.rate),
      });
      onMessage('FX rate created');
      load();
    } catch (e: any) {
      onError(e.message);
    }
  };

  const uploadFxCsv = async (file: File) => {
    try {
      const res = await uploadFile('/admin/fx/rates/upload', file);
      onMessage(`Uploaded FX rates: ${res.created} created, ${res.updated} updated`);
      load();
    } catch (e: any) {
      onError(e.message);
    }
  };

  return (
    <div className="space-y-6">
      <div className="bg-surface-900 border border-surface-700/50 rounded-xl p-4 space-y-3">
        <h3 className="text-sm font-semibold">Reporting currency</h3>
        <div className="flex gap-2 items-center">
          <input
            value={reportingCurrency}
            onChange={(e) => setReportingCurrency(e.target.value.toUpperCase())}
            maxLength={3}
            className="w-24 bg-surface-800 border border-surface-600 rounded-lg px-3 py-1.5 text-sm"
          />
          <button
            type="button"
            onClick={saveReportingCurrency}
            className="px-3 py-1.5 text-xs bg-deloitte-green text-white font-semibold rounded-lg"
          >
            Save
          </button>
        </div>
      </div>

      <div className="bg-surface-900 border border-surface-700/50 rounded-xl p-4 space-y-3">
        <h3 className="text-sm font-semibold">Fiscal calendar</h3>
        <p className="text-xs text-surface-500">
          Gregorian months (YYYY-MM) or NRF-style 4-4-5 (FY2026-P01).
        </p>
        <div className="flex flex-wrap gap-2 items-center">
          <select
            value={fiscalCalendar.calendar_type}
            onChange={(e) =>
              setFiscalCalendar({ ...fiscalCalendar, calendar_type: e.target.value })
            }
            className="bg-surface-800 border border-surface-600 rounded-lg px-3 py-1.5 text-sm"
          >
            <option value="gregorian_month">Gregorian months</option>
            <option value="fiscal_445">4-4-5 fiscal</option>
          </select>
          <label className="text-xs text-surface-400 flex items-center gap-1">
            FY start month
            <input
              type="number"
              min={1}
              max={12}
              value={fiscalCalendar.fiscal_year_start_month}
              onChange={(e) =>
                setFiscalCalendar({
                  ...fiscalCalendar,
                  fiscal_year_start_month: Number(e.target.value),
                })
              }
              className="w-16 bg-surface-800 border border-surface-600 rounded-lg px-2 py-1 text-sm"
            />
          </label>
          <button
            type="button"
            onClick={saveFiscalCalendar}
            className="px-3 py-1.5 text-xs bg-deloitte-green text-white font-semibold rounded-lg"
          >
            Save calendar
          </button>
        </div>
      </div>

      <div className="bg-surface-900 border border-surface-700/50 rounded-xl p-4 space-y-3">
        <h3 className="text-sm font-semibold">Add rate</h3>
        <div className="grid grid-cols-2 md:grid-cols-5 gap-2">
          {(['from_currency', 'to_currency', 'period', 'rate', 'rate_type'] as const).map((k) => (
            <input
              key={k}
              value={fxForm[k]}
              onChange={(e) => setFxForm({ ...fxForm, [k]: e.target.value })}
              placeholder={k}
              className="bg-surface-800 border border-surface-600 rounded-lg px-2 py-1.5 text-xs"
            />
          ))}
        </div>
        <button
          type="button"
          onClick={createFxRate}
          className="px-3 py-1.5 text-xs bg-deloitte-green text-white font-semibold rounded-lg"
        >
          Create rate
        </button>
      </div>

      <div className="bg-surface-900 border border-surface-700/50 rounded-xl p-4 space-y-2">
        <h3 className="text-sm font-semibold">Upload CSV</h3>
        <p className="text-xs text-surface-500">
          Columns: from_currency,to_currency,period,rate[,rate_type]
        </p>
        <input
          type="file"
          accept=".csv,text/csv"
          onChange={(e) => {
            const f = e.target.files?.[0];
            if (f) uploadFxCsv(f);
          }}
          className="text-xs text-surface-400"
        />
      </div>

      <div className="max-h-96 overflow-auto border border-surface-700/40 rounded-xl">
        <table className="w-full text-xs">
          <thead className="bg-surface-900 text-surface-500 sticky top-0">
            <tr>
              <th className="px-3 py-2 text-left">From</th>
              <th className="px-3 py-2 text-left">To</th>
              <th className="px-3 py-2 text-left">Period</th>
              <th className="px-3 py-2 text-left">Rate</th>
              <th className="px-3 py-2 text-left">Type</th>
            </tr>
          </thead>
          <tbody>
            {fxRates.map((r) => (
              <tr key={r.id} className="border-t border-surface-700/40">
                <td className="px-3 py-1.5">{r.from_currency}</td>
                <td className="px-3 py-1.5">{r.to_currency}</td>
                <td className="px-3 py-1.5">{r.period}</td>
                <td className="px-3 py-1.5">{r.rate}</td>
                <td className="px-3 py-1.5 text-surface-400">{r.rate_type}</td>
              </tr>
            ))}
            {!fxRates.length && (
              <tr>
                <td colSpan={5} className="px-3 py-4 text-surface-500 text-center">
                  No FX rates yet
                </td>
              </tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  );
}
