import { Loader2, CheckCircle2 } from 'lucide-react';

interface Props {
  data: {
    label: string;
    progress: number;
    step: string;
    is_complete: boolean;
  };
}

export function StatusCard({ data }: Props) {
  const { label, progress, step, is_complete } = data;
  const pct = Math.round(progress * 100);

  return (
    <div className="bg-surface-800/80 border border-surface-700 rounded-xl p-4">
      <div className="flex items-center gap-3 mb-2">
        {is_complete ? (
          <CheckCircle2 className="w-5 h-5 text-deloitte-green" />
        ) : (
          <Loader2 className="w-5 h-5 animate-spin text-deloitte-green" />
        )}
        <span className="text-sm font-medium text-white">{label}</span>
      </div>

      {!is_complete && (
        <>
          <div className="w-full bg-surface-700 rounded-full h-1.5 mb-2">
            <div
              className="bg-deloitte-green h-1.5 rounded-full transition-all duration-500"
              style={{ width: `${pct}%` }}
            />
          </div>
          <div className="flex justify-between text-xs text-surface-500">
            <span>{step}</span>
            <span>{pct}%</span>
          </div>
        </>
      )}
    </div>
  );
}
