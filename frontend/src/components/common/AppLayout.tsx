import { ChatContainer } from '../chat/ChatContainer';
import { PanelContainer } from '../panels/PanelContainer';
import { Header } from './Header';
import { Sidebar } from './Sidebar';
import {
  usePanelStore,
  useSecondaryPanelStore,
  useWorkspaceLayoutStore,
} from '../../store/panelStore';
import { useChatStore } from '../../store/chatStore';

export function AppLayout() {
  const isPanelOpen = usePanelStore((s) => s.isOpen);
  const closePanel = usePanelStore((s) => s.closePanel);
  const widthMode = usePanelStore((s) => s.widthMode);
  const isSecondaryOpen = useSecondaryPanelStore((s) => s.isOpen);
  const closeSecondary = useSecondaryPanelStore((s) => s.closePanel);
  const maximizedSlot = useWorkspaceLayoutStore((s) => s.maximizedSlot);
  const sidebarExpanded = useChatStore((s) => s.sidebarExpanded);
  const toggleSidebar = useChatStore((s) => s.toggleSidebar);

  // A slot can only be "maximized" while it's actually open — closing the
  // maximized slot should fall back to the normal layout, not a blank one.
  const effectiveMaximized =
    (maximizedSlot === 'primary' && isPanelOpen) ||
      (maximizedSlot === 'secondary' && isSecondaryOpen)
      ? maximizedSlot
      : null;
  const bothOpen = isPanelOpen && isSecondaryOpen && !effectiveMaximized;
  const anyPanelOpen = (isPanelOpen || isSecondaryOpen) && !effectiveMaximized;

  const panelWidthClass = bothOpen
    ? 'lg:w-[32%] lg:min-w-[280px]'
    : widthMode === 'wide'
      ? 'lg:w-[55%] lg:min-w-[360px]'
      : 'lg:w-[40%] lg:min-w-[320px]';

  return (
    <div className="h-[100dvh] flex flex-col rf-shell bg-surface-900">
      <Header />
      <div className="flex-1 flex flex-col lg:flex-row overflow-hidden min-h-0 relative">
        {/* Mobile sidebar drawer backdrop */}
        {sidebarExpanded && (
          <button
            type="button"
            className="lg:hidden absolute inset-0 z-20 bg-black/50"
            aria-label="Close conversation sidebar"
            onClick={toggleSidebar}
          />
        )}

        <div
          className={`
            z-30 shrink-0
            max-lg:absolute max-lg:inset-y-0 max-lg:left-0 max-lg:h-full
            max-lg:shadow-2xl max-lg:transition-transform max-lg:duration-200
            ${sidebarExpanded ? 'max-lg:translate-x-0' : 'max-lg:-translate-x-full max-lg:pointer-events-none'}
            lg:relative lg:translate-x-0 lg:pointer-events-auto
          `}
        >
          <Sidebar />
        </div>

        <div
          className={`flex-1 min-w-0 min-h-0 transition-all duration-300 bg-surface-900 ${
            effectiveMaximized ? 'lg:hidden' : ''
          } ${
            anyPanelOpen && sidebarExpanded
              ? bothOpen
                ? 'lg:max-w-[36%]'
                : widthMode === 'wide'
                  ? 'lg:max-w-[45%]'
                  : 'lg:max-w-[60%]'
              : ''
          }`}
        >
          <ChatContainer />
        </div>

        {isPanelOpen && effectiveMaximized !== 'secondary' && (
          <>
            {/* Mobile: full-screen panel overlay */}
            <button
              type="button"
              className="lg:hidden absolute inset-0 z-30 bg-black/50"
              aria-label="Close panel backdrop"
              onClick={closePanel}
            />
            <div
              className={`
                z-40 bg-surface-800 overflow-hidden
                max-lg:absolute max-lg:inset-0 max-lg:w-full max-lg:h-full
                lg:relative lg:border-l lg:border-surface-700/50
                w-full ${effectiveMaximized === 'primary' ? 'lg:max-w-none lg:flex-1' : `${panelWidthClass} lg:max-h-none lg:max-w-[70%]`}
                animate-in slide-in-from-bottom lg:slide-in-from-right
                transition-[width] duration-300
              `}
            >
              <PanelContainer variant="primary" />
            </div>
          </>
        )}

        {isSecondaryOpen && effectiveMaximized !== 'primary' && (
          <>
            {/* Mobile: primary takes priority when both are open — a stack
                only makes sense once there's room to show two at once. */}
            <button
              type="button"
              className={`lg:hidden absolute inset-0 z-30 bg-black/50 ${isPanelOpen ? 'max-lg:hidden' : ''}`}
              aria-label="Close pinned panel backdrop"
              onClick={closeSecondary}
            />
            <div
              className={`
                z-40 bg-surface-800 overflow-hidden
                max-lg:absolute max-lg:inset-0 max-lg:w-full max-lg:h-full
                ${isPanelOpen ? 'max-lg:hidden' : ''}
                lg:relative lg:border-l lg:border-surface-700/50
                w-full ${effectiveMaximized === 'secondary' ? 'lg:max-w-none lg:flex-1' : `${panelWidthClass} lg:max-h-none lg:max-w-[70%]`}
                animate-in slide-in-from-bottom lg:slide-in-from-right
                transition-[width] duration-300
              `}
            >
              <PanelContainer variant="secondary" />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
