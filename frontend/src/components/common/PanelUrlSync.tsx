import { useEffect, useRef } from 'react';
import { useSearchParams } from 'react-router-dom';
import { usePanelStore, useSecondaryPanelStore, type PanelSlot } from '../../store/panelStore';
import { shareablePanelParams } from '../../utils/panelParams';

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
  'why_this_number',
]);

const SLOT_PARAM: Record<PanelSlot, string> = { primary: 'panel', secondary: 'panel2' };
const SLOT_PARAMS_PARAM: Record<PanelSlot, string> = { primary: 'pp', secondary: 'pp2' };

function serializeParams(params: Record<string, any>): string {
  const shareable = shareablePanelParams(params);
  return Object.keys(shareable).length ? JSON.stringify(shareable) : '';
}

function deserializeParams(raw: string | null): Record<string, any> {
  if (!raw) return {};
  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
  } catch {
    return {};
  }
}

/**
 * Keeps `?panel=`/`?panel2=` (+ `?pp=`/`?pp2=` filter params) in sync with
 * the open panel stack, so a link you send a colleague opens the same
 * panel(s) with the same filters, not just the same panel *type*.
 */
export function PanelUrlSync() {
  const [params, setParams] = useSearchParams();
  const isOpen = usePanelStore((s) => s.isOpen);
  const panelType = usePanelStore((s) => s.panelType);
  const panelParams = usePanelStore((s) => s.panelParams);
  const openPanel = usePanelStore((s) => s.openPanel);
  const isSecondaryOpen = useSecondaryPanelStore((s) => s.isOpen);
  const secondaryPanelType = useSecondaryPanelStore((s) => s.panelType);
  const secondaryPanelParams = useSecondaryPanelStore((s) => s.panelParams);
  const openSecondary = useSecondaryPanelStore((s) => s.openPanel);
  const skipNextUrlOpen = useRef<Record<PanelSlot, boolean>>({ primary: false, secondary: false });

  // Deep-link: open panel(s) + filters from the URL on first load.
  useEffect(() => {
    const requested = params.get(SLOT_PARAM.primary);
    if (requested && VALID_PANELS.has(requested) && !(isOpen && panelType === requested)) {
      if (skipNextUrlOpen.current.primary) {
        skipNextUrlOpen.current.primary = false;
      } else {
        openPanel(requested, deserializeParams(params.get(SLOT_PARAMS_PARAM.primary)));
      }
    }
    const requested2 = params.get(SLOT_PARAM.secondary);
    if (
      requested2 &&
      VALID_PANELS.has(requested2) &&
      !(isSecondaryOpen && secondaryPanelType === requested2)
    ) {
      if (skipNextUrlOpen.current.secondary) {
        skipNextUrlOpen.current.secondary = false;
      } else {
        openSecondary(requested2, deserializeParams(params.get(SLOT_PARAMS_PARAM.secondary)));
      }
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps -- intentional mount/deep-link only
  }, []);

  // Store → URL, one slot at a time so a change to one doesn't clobber the other.
  useEffect(() => {
    const currentType = params.get(SLOT_PARAM.primary);
    const currentParams = params.get(SLOT_PARAMS_PARAM.primary) || '';
    if (isOpen && panelType) {
      const nextParams = serializeParams(panelParams);
      if (currentType !== panelType || currentParams !== nextParams) {
        const next = new URLSearchParams(params);
        next.set(SLOT_PARAM.primary, panelType);
        if (nextParams) next.set(SLOT_PARAMS_PARAM.primary, nextParams);
        else next.delete(SLOT_PARAMS_PARAM.primary);
        setParams(next, { replace: true });
      }
    } else if (currentType) {
      skipNextUrlOpen.current.primary = true;
      const next = new URLSearchParams(params);
      next.delete(SLOT_PARAM.primary);
      next.delete(SLOT_PARAMS_PARAM.primary);
      setParams(next, { replace: true });
    }
  }, [isOpen, panelType, panelParams, params, setParams]);

  useEffect(() => {
    const currentType = params.get(SLOT_PARAM.secondary);
    const currentParams = params.get(SLOT_PARAMS_PARAM.secondary) || '';
    if (isSecondaryOpen && secondaryPanelType) {
      const nextParams = serializeParams(secondaryPanelParams);
      if (currentType !== secondaryPanelType || currentParams !== nextParams) {
        const next = new URLSearchParams(params);
        next.set(SLOT_PARAM.secondary, secondaryPanelType);
        if (nextParams) next.set(SLOT_PARAMS_PARAM.secondary, nextParams);
        else next.delete(SLOT_PARAMS_PARAM.secondary);
        setParams(next, { replace: true });
      }
    } else if (currentType) {
      skipNextUrlOpen.current.secondary = true;
      const next = new URLSearchParams(params);
      next.delete(SLOT_PARAM.secondary);
      next.delete(SLOT_PARAMS_PARAM.secondary);
      setParams(next, { replace: true });
    }
  }, [isSecondaryOpen, secondaryPanelType, secondaryPanelParams, params, setParams]);

  return null;
}
