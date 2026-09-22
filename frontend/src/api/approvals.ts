import { apiGet, apiPost } from './client';

export interface ApprovalWorkflow {
  id: string;
  name: string;
  description: string;
  levels: { level: number; role: string; label?: string }[];
  require_sod: boolean;
}

export interface ApprovalStep {
  id: string;
  level: number;
  required_role: string;
  status: string;
  actor_id: string | null;
  actor_username: string | null;
  comments: string | null;
  decided_at: string | null;
  can_decide: boolean;
  blocked_reason: string | null;
}

export interface ApprovalStatus {
  version_id: string;
  version: {
    id: string;
    name: string;
    status: string;
    created_by: string | null;
  };
  steps: ApprovalStep[];
  can_submit: boolean;
  is_creator: boolean;
}

export async function getApprovalWorkflows(): Promise<ApprovalWorkflow[]> {
  return apiGet('/approvals/workflows');
}

export async function getApprovalStatus(versionId: string): Promise<ApprovalStatus> {
  return apiGet(`/approvals/status/${versionId}`);
}

export async function submitForApproval(
  versionId: string,
  workflowId?: string,
): Promise<{ success: boolean; status: string }> {
  return apiPost('/approvals/submit', {
    version_id: versionId,
    workflow_id: workflowId || null,
  });
}

export async function decideStep(
  stepId: string,
  action: 'approve' | 'reject',
  comments?: string,
): Promise<{ success: boolean; step_status: string; version_status: string | null }> {
  return apiPost('/approvals/decide', {
    step_id: stepId,
    action,
    comments: comments || null,
  });
}
