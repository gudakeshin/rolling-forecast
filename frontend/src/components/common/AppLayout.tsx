import { ChatContainer } from '../chat/ChatContainer';
import { PanelContainer } from '../panels/PanelContainer';
import { Header } from './Header';
import { ConversationSidebar } from './ConversationSidebar';
import { usePanelStore } from '../../store/panelStore';
import { useChatStore } from '../../store/chatStore';

export function AppLayout() {
  const isPanelOpen = usePanelStore((s) => s.isOpen);
  const closePanel = usePanelStore((s) => s.closePanel);
  const widthMode = usePanelStore((s) => s.widthMode);
  const sidebarExpanded = useChatStore((s) => s.sidebarExpanded);
  const toggleSidebar = useChatStore((s) => s.toggleSidebar);

  const panelWidthClass =
    widthMode === 'wide' ? 'lg:w-[55%] lg:min-w-[360px]' : 'lg:w-[40%] lg:min-w-[320px]';

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
          <ConversationSidebar />
        </div>

        <div
          className={`flex-1 min-w-0 min-h-0 transition-all duration-300 bg-surface-900 ${
            isPanelOpen && sidebarExpanded
              ? widthMode === 'wide'
                ? 'lg:max-w-[45%]'
                : 'lg:max-w-[60%]'
              : ''
          }`}
        >
          <ChatContainer />
        </div>

        {isPanelOpen && (
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
                w-full ${panelWidthClass} lg:max-h-none lg:max-w-[70%]
                animate-in slide-in-from-bottom lg:slide-in-from-right
                transition-[width] duration-300
              `}
            >
              <PanelContainer />
            </div>
          </>
        )}
      </div>
    </div>
  );
}
