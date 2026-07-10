import { ChatContainer } from '../chat/ChatContainer';
import { PanelContainer } from '../panels/PanelContainer';
import { Header } from './Header';
import { ConversationSidebar } from './ConversationSidebar';
import { usePanelStore } from '../../store/panelStore';

export function AppLayout() {
  const isPanelOpen = usePanelStore((s) => s.isOpen);

  return (
    <div className="h-screen flex flex-col bg-black">
      <Header />
      <div className="flex-1 flex overflow-hidden">
        <ConversationSidebar />

        <div
          className={`flex-1 transition-all duration-300 bg-surface-900 ${
            isPanelOpen ? 'w-[60%]' : 'w-full'
          }`}
        >
          <ChatContainer />
        </div>

        {isPanelOpen && (
          <div className="w-[40%] border-l border-surface-700/50 bg-surface-800 overflow-hidden animate-in slide-in-from-right">
            <PanelContainer />
          </div>
        )}
      </div>
    </div>
  );
}
