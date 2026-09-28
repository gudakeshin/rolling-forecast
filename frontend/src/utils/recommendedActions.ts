import { usePanelStore } from '../store/panelStore';

export interface RecommendedAction {
  id?: string;
  type?: string;
  label: string;
  detail?: string;
  variant?: string;
  href?: string;
  panel?: string;
  version_id?: string;
  item_id?: string;
}

type OpenPanel = (type: string, params: Record<string, unknown>) => void;

/**
 * Dispatch a recommended action from chat ActionCards or panel remediation UIs.
 * Returns true if the action was handled.
 */
export async function dispatchRecommendedAction(
  action: RecommendedAction,
  opts?: {
    openPanel?: OpenPanel;
    onApproveItem?: (itemId: string) => void | Promise<void>;
    onRejectItem?: (itemId: string) => void | Promise<void>;
    lineItemName?: string;
  },
): Promise<boolean> {
  const openPanel = opts?.openPanel ?? ((type, params) => usePanelStore.getState().openPanel(type, params));
  const key = (action.type || action.id || '').toLowerCase();
  const versionId = action.version_id;

  if (action.href) {
    window.open(action.href, '_blank', 'noopener,noreferrer');
    return true;
  }

  if (action.panel) {
    openPanel(action.panel, versionId ? { version_id: versionId, versionId } : {});
    return true;
  }

  if (key.includes('upload') || key === 'upload_data') {
    openPanel('document_library', versionId ? { version_id: versionId } : {});
    return true;
  }

  if (key.includes('driver') || key === 'driver_input') {
    openPanel('driver_inputs', versionId ? { version_id: versionId } : {});
    return true;
  }

  if (key.includes('anomaly') || key === 'investigate') {
    openPanel('anomaly_dashboard', versionId ? { version_id: versionId } : {});
    return true;
  }

  if (key.includes('accuracy') || key.includes('compare_peers')) {
    openPanel('accuracy_tracking', versionId ? { version_id: versionId } : {});
    return true;
  }

  if (key.includes('override') || key === 'review_override' || key === 'switch_model') {
    openPanel('forecast_table', versionId ? { version_id: versionId } : {});
    return true;
  }

  if (key.includes('review') && !key.includes('submit') && !key.includes('override')) {
    openPanel('review_dashboard', versionId ? { version_id: versionId } : {});
    return true;
  }

  if ((key === 'confirm_zero' || key === 'approve' || key.includes('approve')) && action.item_id && opts?.onApproveItem) {
    await opts.onApproveItem(action.item_id);
    return true;
  }

  if (key.includes('reject') && action.item_id && opts?.onRejectItem) {
    await opts.onRejectItem(action.item_id);
    return true;
  }

  if ((key.includes('approve') || key === 'confirm_zero') && versionId) {
    openPanel('approvals', { version_id: versionId });
    return true;
  }

  if (key.includes('submit') && versionId) {
    openPanel('approvals', { version_id: versionId });
    return true;
  }

  if (key.startsWith('open_') || key.includes('panel') || key.includes('drill')) {
    const panelType = key.replace(/^open_/, '');
    openPanel(panelType, versionId ? { version_id: versionId, versionId } : {});
    return true;
  }

  // Fallback: copy a chat prompt for the user
  if (opts?.lineItemName || action.detail) {
    const prompt =
      action.detail ||
      `${action.label}${opts?.lineItemName ? ` for "${opts.lineItemName}"` : ''}`;
    try {
      await navigator.clipboard.writeText(prompt);
    } catch {
      /* ignore */
    }
    return true;
  }

  return false;
}
