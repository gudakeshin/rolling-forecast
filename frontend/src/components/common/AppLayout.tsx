import { ChatContainer } from '../chat/ChatContainer';
import { PanelContainer } from '../panels/PanelContainer';
import { Header } from './Header';
import { ConversationSidebar } from './ConversationSidebar';
import { usePanelStore } from '../../store/panelStore';
import { useChatStore } from '../../store/chatStore';

export function AppLayout() {
  const isPanelOpen = usePanelStore((s) => s.isOpen);
  const widthMode = usePanelStore((s) => s.widthMode);
  const sidebarExpanded = useChatStore((s) => s.sidebarExpanded);

  const panelWidthClass =
    widthMode === 'wide' ? 'lg:w-[55%] lg:min-w-[360px]' : 'lg:w-[40%] lg:min-w-[320px]';

  return (
    <div className="h-screen flex flex-col rf-shell bg-black">
      <Header />
      <div className="flex-1 flex flex-col lg:flex-row overflow-hidden min-h-0">
        <ConversationSidebar />

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
          <div
            className={`w-full ${panelWidthClass} max-h-[45vh] lg:max-h-none lg:max-w-[70%] border-t lg:border-t-0 lg:border-l border-surface-700/50 bg-surface-800 overflow-hidden animate-in slide-in-from-bottom lg:slide-in-from-right transition-[width] duration-300`}
          >
            <PanelContainer />
          </div>
        )}
      </div>
    </div>
  );
}
