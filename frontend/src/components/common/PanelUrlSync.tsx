import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { usePanelStore } from '../../store/panelStore';

const VALID_PANELS = new Set([
  'forecast_table',
  'review_queue',
  'review_dashboard',
  'overrides',
  'comparison',
  'executive_dashboard',
  'accuracy_tracking',
  'driver_inputs',
  'drivers',
  'explainability',
  'what_if',
  'anomaly_dashboard',
  'heuristics',
  'document_library',
  'approvals',
  'skill_editor',
  'admin_console',
]);

/** Keep `?panel=` in sync with the open side panel (deep-link + shareable URL). */
export function PanelUrlSync() {
  const [params, setParams] = useSearchParams();
  const isOpen = usePanelStore((s) => s.isOpen);
  const panelType = usePanelStore((s) => s.panelType);
  const openPanel = usePanelStore((s) => s.openPanel);
  const skipNextUrlOpen = useRef(false);

  // Deep-link: open panel from URL on first load
  useEffect(() => {
    const requested = params.get('panel');
    if (!requested || !VALID_PANELS.has(requested)) return;
    if (isOpen && panelType === requested) return;
    if (skipNextUrlOpen.current) {
      skipNextUrlOpen.current = false;
      return;
    }
    openPanel(requested, {});
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional mount/deep-link only
  }, []);

  // Store → URL
  useEffect(() => {
    const current = params.get('panel');
    if (isOpen && panelType) {
      if (current !== panelType) {
        const next = new URLSearchParams(params);
        next.set('panel', panelType);
        setParams(next, { replace: true });
      }
    } else if (current) {
      skipNextUrlOpen.current = true;
      const next = new URLSearchParams(params);
      next.delete('panel');
      setParams(next, { replace: true });
    }
  }, [isOpen, panelType, params, setParams]);

  return null;
}
