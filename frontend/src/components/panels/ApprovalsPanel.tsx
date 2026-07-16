import { useCallback, useEffect, useState } from 'react';
import {
  CheckCircle, XCircle, Clock, Loader2, Shield, Send, AlertTriangle, RefreshCw, Download,
} from 'lucide-react';
import {
  getApprovalStatus,
  getApprovalWorkflows,
  submitForApproval,
  decideStep,
  type ApprovalStatus,
  type ApprovalWorkflow,
} from '../../api/approvals';
import { usePanelStore } from '../../store/panelStore';
import { toast } from '../../store/toastStore';
import { downloadCsv } from '../ui/DataTable';

function statusBadgeClass(status: string): string {
  if (status === 'draft') return 'bg-blue-500/10 border-blue-500/20 text-blue-400';
  if (status === 'in_review') return 'bg-amber-500/10 border-amber-500/20 text-amber-400';
  return 'bg-deloitte-green/10 border-deloitte-green/20 text-deloitte-green';
}

function StepIcon({ status }: { status: string }) {
  if (status === 'approved') return <CheckCircle className="w-4 h-4 text-deloitte-green" />;
  if (status === 'rejected') return <XCircle className="w-4 h-4 text-red-400" />;
  if (status === 'skipped') return <Clock className="w-4 h-4 text-surface-600" />;
  return <Clock className="w-4 h-4 text-amber-400" />;
}

export function ApprovalsPanel() {
  const panelParams = usePanelStore((s) => s.panelParams);
  const versionId = panelParams.version_id as string | undefined;

  const [status, setStatus] = useState<ApprovalStatus | null>(null);
  const [workflows, setWorkflows] = useState<ApprovalWorkflow[]>([]);
  const [workflowId, setWorkflowId] = useState<string>('');
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [rejectComment, setRejectComment] = useState('');
  const [rejectingStepId, setRejectingStepId] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const load = useCallback(async () => {
    if (!versionId) {
      setError('No version selected');
      setLoading(false);
      return;
    }
    setLoading(true);
    setError(null);
    try {
      const [st, wfs] = await Promise.all([
        getApprovalStatus(versionId),
        getApprovalWorkflows(),
      ]);
      setStatus(st);
      setWorkflows(wfs);
      if (wfs.length && !workflowId) setWorkflowId(wfs[0].id);
    } catch (e: any) {
      setError(e.message || 'Failed to load approval status');
    } finally {
      setLoading(false);
    }
  }, [versionId, workflowId]);

  useEffect(() => {
    load();
  }, [load]);

  // Soft poll while panel is open (approval queues change out-of-band)
  useEffect(() => {
    if (!versionId) return;
    const id = window.setInterval(() => {
      void load();
    }, 60_000);
    return () => window.clearInterval(id);
  }, [versionId, load]);

  const handleSubmit = async () => {
    if (!versionId) return;
    setBusy(true);
    try {
      await submitForApproval(versionId, workflowId || undefined);
      toast.success('Submitted for approval');
      await load();
    } catch (e: any) {
      toast.error(e.message || 'Submit failed');
    } finally {
      setBusy(false);
    }
  };

  const handleDecide = async (stepId: string, action: 'approve' | 'reject') => {
    if (action === 'reject' && !rejectComment.trim()) {
      toast.error('Comment required when rejecting');
      return;
    }
    setBusy(true);
    try {
      const res = await decideStep(stepId, action, rejectComment.trim() || undefined);
      toast.success(
        action === 'approve'
          ? `Approved — version is now ${res.version_status}`
          : 'Rejected — version returned to draft',
      );
      setRejectingStepId(null);
      setRejectComment('');
      await load();
    } catch (e: any) {
      toast.error(e.message || `${action} failed`);
    } finally {
      setBusy(false);
    }
  };

  if (loading) {
    return (
      <div className="flex flex-col items-center justify-center h-32 gap-2">
        <Loader2 className="w-6 h-6 animate-spin text-deloitte-green" />
        <span className="text-xs text-surface-500">Loading approvals…</span>
      </div>
    );
  }

  if (error || !status) {
    return (
      <div className="flex flex-col items-center justify-center h-32 gap-2 text-center px-4">
        <AlertTriangle className="w-6 h-6 text-amber-400" />
        <p className="text-sm text-amber-300">{error || 'No approval data'}</p>
      </div>
    );
  }

  const { version, steps, can_submit, is_creator } = status;
  const firstPending = steps.find((s) => s.status === 'pending');

  const handleExportSteps = () => {
    downloadCsv(
      `approvals_${versionId || 'export'}`,
      [
        { key: 'order', label: 'Step Order' },
        { key: 'name', label: 'Step Name' },
        { key: 'status', label: 'Status' },
        { key: 'assignee', label: 'Assignee' },
        { key: 'decided_at', label: 'Decided At' },
        { key: 'comments', label: 'Comments' },
      ],
      steps.map((step, idx) => ({
        order: idx + 1,
        name: `Level ${step.level} · ${step.required_role}`,
        status: step.status,
        assignee: step.actor_username || '',
        decided_at: step.decided_at || '',
        comments: step.comments || '',
      })) as Record<string, unknown>[],
    );
  };

  return (
    <div className="space-y-4">
      <div className="flex items-center justify-end gap-1.5">
        {steps.length > 0 && (
          <button
            type="button"
            onClick={handleExportSteps}
            className="inline-flex items-center gap-1.5 text-xs text-surface-300 hover:text-white px-2 py-1 rounded-md border border-surface-600 hover:border-deloitte-green/40"
            title="Export approval steps CSV"
          >
            <Download className="w-3.5 h-3.5" />
            CSV
          </button>
        )}
        <button
          type="button"
          onClick={() => void load()}
          disabled={loading || busy}
          className="inline-flex items-center gap-1 text-xs text-surface-400 hover:text-deloitte-green disabled:opacity-50"
          title="Refresh"
        >
          <RefreshCw className={`w-3.5 h-3.5 ${loading ? 'animate-spin' : ''}`} />
          Refresh
        </button>
      </div>
      <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3">
        <div className="flex items-center justify-between">
          <div>
            <h4 className="text-sm font-semibold text-white flex items-center gap-1.5">
              <Shield className="w-4 h-4 text-deloitte-green" />
              {version.name}
            </h4>
            {is_creator && (
              <p className="text-xs text-surface-500 mt-0.5">You created this forecast</p>
            )}
          </div>
          <span className={`px-2 py-1 text-xs font-medium rounded-md border ${statusBadgeClass(version.status)}`}>
            {version.status}
          </span>
        </div>
      </div>

      {can_submit && (
        <div className="bg-surface-800/60 border border-surface-700/50 rounded-xl p-3 space-y-2">
          <p className="text-xs text-surface-400">Submit this draft into the approval workflow.</p>
          {workflows.length > 1 && (
            <select
              value={workflowId}
              onChange={(e) => setWorkflowId(e.target.value)}
              className="w-full px-2 py-1.5 bg-surface-900 border border-surface-600 rounded-lg text-xs text-white"
            >
              {workflows.map((w) => (
                <option key={w.id} value={w.id}>{w.name}</option>
              ))}
            </select>
          )}
          <button
            type="button"
            onClick={handleSubmit}
            disabled={busy}
            className="w-full flex items-center justify-center gap-1.5 px-3 py-2 bg-deloitte-green text-black text-xs font-semibold rounded-lg hover:bg-deloitte-green/90 disabled:opacity-50"
          >
            {busy ? <Loader2 className="w-3.5 h-3.5 animate-spin" /> : <Send className="w-3.5 h-3.5" />}
            Submit for approval
          </button>
        </div>
      )}

      {steps.length === 0 ? (
        <p className="text-sm text-surface-500 text-center py-6">
          No approval steps yet. Submit the version to start the workflow.
        </p>
      ) : (
        <div className="space-y-0">
          {steps.map((step, idx) => {
            const isActive = firstPending?.id === step.id;
            return (
              <div key={step.id} className="flex gap-3">
                <div className="flex flex-col items-center">
                  <div className={`w-8 h-8 rounded-full flex items-center justify-center border ${
                    step.status === 'approved'
                      ? 'border-deloitte-green/40 bg-deloitte-green/10'
                      : step.status === 'rejected'
                      ? 'border-red-500/40 bg-red-500/10'
                      : isActive
                      ? 'border-amber-400/40 bg-amber-500/10'
                      : 'border-surface-600 bg-surface-800'
                  }`}>
                    <StepIcon status={step.status} />
                  </div>
                  {idx < steps.length - 1 && (
                    <div className="w-px flex-1 min-h-[24px] bg-surface-700" />
                  )}
                </div>
                <div className={`flex-1 pb-4 ${isActive ? '' : 'opacity-80'}`}>
                  <div className="flex items-center justify-between gap-2">
                    <div>
                      <div className="text-xs font-semibold text-white">
                        Level {step.level} · {step.required_role}
                      </div>
                      <div className="text-xs text-surface-500 capitalize">{step.status}</div>
                    </div>
                    {step.actor_username && (
                      <div className="text-xs text-surface-400 text-right">
                        {step.actor_username}
                        {step.decided_at && (
                          <div className="text-surface-600">
                            {new Date(step.decided_at).toLocaleString()}
                          </div>
                        )}
                      </div>
                    )}
                  </div>
                  {step.comments && (
                    <p className="text-xs text-surface-400 mt-1 italic">"{step.comments}"</p>
                  )}
                  {isActive && step.can_decide && (
                    <div className="mt-2 space-y-2">
                      {rejectingStepId === step.id ? (
                        <div className="space-y-1.5">
                          <input
                            type="text"
                            value={rejectComment}
                            onChange={(e) => setRejectComment(e.target.value)}
                            placeholder="Rejection reason (required)"
                            className="w-full px-2 py-1.5 bg-surface-900 border border-surface-600 rounded text-xs text-white placeholder-surface-500"
                          />
                          <div className="flex gap-2">
                            <button
                              type="button"
                              disabled={busy || !rejectComment.trim()}
                              onClick={() => handleDecide(step.id, 'reject')}
                              className="px-2.5 py-1 bg-red-500/20 text-red-400 rounded text-xs hover:bg-red-500/30 disabled:opacity-40"
                            >
                              Confirm Reject
                            </button>
                            <button
                              type="button"
                              onClick={() => { setRejectingStepId(null); setRejectComment(''); }}
                              className="px-2.5 py-1 bg-surface-700 text-surface-400 rounded text-xs"
                            >
                              Cancel
                            </button>
                          </div>
                        </div>
                      ) : (
                        <div className="flex gap-2">
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => handleDecide(step.id, 'approve')}
                            className="px-2.5 py-1.5 bg-deloitte-green text-black rounded-lg text-xs font-semibold hover:bg-deloitte-green/90 disabled:opacity-50"
                          >
                            Approve
                          </button>
                          <button
                            type="button"
                            disabled={busy}
                            onClick={() => setRejectingStepId(step.id)}
                            className="px-2.5 py-1.5 bg-red-500/15 text-red-400 border border-red-500/25 rounded-lg text-xs hover:bg-red-500/25 disabled:opacity-50"
                          >
                            Reject
                          </button>
                        </div>
                      )}
                    </div>
                  )}
                  {isActive && !step.can_decide && step.blocked_reason && (
                    <p className="mt-1.5 text-xs text-surface-500">{step.blocked_reason}</p>
                  )}
                </div>
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
