import { useEffect, useState } from 'react';
import { Loader2, CheckCircle2, AlertCircle } from 'lucide-react';
import { apiGet } from '../../../api/client';

interface StatusData {
  label: string;
  progress: number;
  step: string;
  is_complete: boolean;
  job_id?: string;
}

interface Props {
  data: StatusData;
}

interface JobRecord {
  job_id: string;
  status: string;
  progress?: number;
  step?: string;
  error?: string | null;
}

function parseJobId(data: StatusData): string | null {
  if (data.job_id) return data.job_id;
  const match = data.step?.match(/job_id=([^\s]+)/);
  return match ? match[1] : null;
}

export function StatusCard({ data: initialData }: Props) {
  const [label, setLabel] = useState(initialData.label);
  const [progress, setProgress] = useState(initialData.progress);
  const [step, setStep] = useState(initialData.step);
  const [isComplete, setIsComplete] = useState(initialData.is_complete);
  const [error, setError] = useState<string | null>(null);

  const jobId = parseJobId(initialData);
  const pct = Math.round(progress * 100);

  useEffect(() => {
    if (!jobId || initialData.is_complete) return;

    let cancelled = false;
    let intervalId: ReturnType<typeof setInterval> | null = null;

    const applyJob = (job: JobRecord): boolean => {
      if (job.status === 'completed') {
        setProgress(1);
        setIsComplete(true);
        if (job.step) setStep(job.step);
        return true;
      }
      if (job.status === 'failed') {
        setError(job.error || 'Job failed');
        setIsComplete(true);
        return true;
      }
      if (job.progress != null) setProgress(job.progress);
      if (job.step) setStep(job.step);
      if (job.status === 'running') {
        setLabel('Generating forecast');
      }
      return false;
    };

    const poll = async () => {
      if (cancelled) return;
      try {
        const job = await apiGet<JobRecord>(`/jobs/${jobId}`);
        if (cancelled) return;
        if (applyJob(job) && intervalId) {
          clearInterval(intervalId);
          intervalId = null;
        }
      } catch (e: unknown) {
        if (!cancelled) {
          setError(e instanceof Error ? e.message : 'Failed to poll job status');
          if (intervalId) clearInterval(intervalId);
        }
      }
    };

    void poll();
    intervalId = setInterval(() => void poll(), 2000);

    return () => {
      cancelled = true;
      if (intervalId) clearInterval(intervalId);
    };
  }, [jobId, initialData.is_complete]);

  return (
    <div className="bg-surface-800/80 border border-surface-700 rounded-xl p-4">
      <div className="flex items-center gap-3 mb-2">
        {error ? (
          <AlertCircle className="w-5 h-5 text-red-400" />
        ) : isComplete ? (
          <CheckCircle2 className="w-5 h-5 text-deloitte-green" />
        ) : (
          <Loader2 className="w-5 h-5 animate-spin text-deloitte-green" />
        )}
        <span className="text-sm font-medium text-white">{label}</span>
      </div>

      {error ? (
        <p className="text-xs text-red-400">{error}</p>
      ) : !isComplete ? (
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
      ) : null}
    </div>
  );
}
