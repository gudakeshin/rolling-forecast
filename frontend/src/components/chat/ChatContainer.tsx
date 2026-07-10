import { useRef, useEffect } from 'react';
import { useChatStore } from '../../store/chatStore';
import { MessageList } from './MessageList';
import { InputBar } from './InputBar';
import { getConversations, sendMessage } from '../../api/chat';
import type { ContentBlock } from '../../types/chat';
import { TrendingUp, Upload, BarChart3, Search, GitCompare } from 'lucide-react';

export function ChatContainer() {
  const {
    messages,
    activeConversationId,
    isStreaming,
    streamingMessage,
    addMessage,
    startStreaming,
    appendStreamContent,
    addStreamContentBlock,
    setToolInProgress,
    finishStreaming,
    cancelStreaming,
    setActiveConversation,
    setConversations,
  } = useChatStore();

  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' });
  }, [messages, streamingMessage]);

  const handleSend = async (content: string) => {
    if (isStreaming) return;

    const userMessage = {
      id: `user-${Date.now()}`,
      conversation_id: activeConversationId || '',
      role: 'user' as const,
      content,
      content_blocks: [],
      created_at: new Date().toISOString(),
    };
    addMessage(userMessage);
    startStreaming(activeConversationId || 'new');

    try {
      let conversationId = activeConversationId;
      let finalContent = '';
      let finalBlocks: ContentBlock[] = [];
      let finalToolCalls: any[] = [];
      let finalPanelPayload: any = null;

      await sendMessage(content, conversationId, (event) => {
        switch (event.event) {
          case 'message_start':
            if (event.data.conversation_id) {
              conversationId = event.data.conversation_id;
              setActiveConversation(conversationId!);
            }
            break;
          case 'token':
            // Stream intermediate LLM tokens for real-time typing effect
            if (event.data.text) {
              appendStreamContent(event.data.text);
            }
            break;
          case 'content_block':
            addStreamContentBlock(event.data as ContentBlock);
            finalBlocks.push(event.data as ContentBlock);
            break;
          case 'tool_start':
            setToolInProgress(event.data.tool_name);
            break;
          case 'tool_end':
            setToolInProgress(null);
            break;
          case 'message_end':
            finalContent = event.data.content || '';
            if (event.data.content_blocks) finalBlocks = event.data.content_blocks;
            finalToolCalls = event.data.tool_calls || [];
            finalPanelPayload = event.data.panel_payload;
            break;
          case 'error':
            finalContent = `Error: ${event.data.error}`;
            break;
        }
      });

      finishStreaming({
        id: `assistant-${Date.now()}`,
        conversation_id: conversationId || '',
        role: 'assistant',
        content: finalContent,
        content_blocks: finalBlocks,
        tool_calls: finalToolCalls.length > 0 ? finalToolCalls : undefined,
        panel_payload: finalPanelPayload,
        created_at: new Date().toISOString(),
      });

      // Keep left sidebar in sync (new chats + updated titles)
      try {
        const list = await getConversations();
        setConversations(list);
      } catch {
        /* non-fatal */
      }
    } catch (error: any) {
      cancelStreaming();
      addMessage({
        id: `error-${Date.now()}`,
        conversation_id: activeConversationId || '',
        role: 'assistant',
        content: `Sorry, something went wrong: ${error.message}`,
        content_blocks: [{ type: 'text', data: { text: `Error: ${error.message}` } }],
        created_at: new Date().toISOString(),
      });
    }
  };

  const isEmpty = messages.length === 0 && !streamingMessage;

  return (
    <div className="h-full flex flex-col">
      {isEmpty ? (
        <WelcomeScreen onSuggestion={handleSend} />
      ) : (
        <div className="flex-1 overflow-y-auto px-4 py-6">
          <div className="max-w-3xl mx-auto">
            <MessageList messages={messages} streamingMessage={streamingMessage} />
            <div ref={messagesEndRef} />
          </div>
        </div>
      )}
      <div className="px-4 pb-4 pt-2">
        <div className="max-w-3xl mx-auto">
          <InputBar onSend={handleSend} isStreaming={isStreaming} />
        </div>
      </div>
    </div>
  );
}

function WelcomeScreen({ onSuggestion }: { onSuggestion: (s: string) => void }) {
  const suggestions = [
    {
      icon: Upload,
      text: 'Upload actuals and generate a forecast',
      color: 'text-deloitte-green',
      bg: 'bg-deloitte-green/10 border-deloitte-green/20',
    },
    {
      icon: BarChart3,
      text: 'Show me the latest forecast summary',
      color: 'text-accent-500',
      bg: 'bg-accent-500/10 border-accent-500/20',
    },
    {
      icon: Search,
      text: 'Which lines have low confidence?',
      color: 'text-deloitte-teal-light',
      bg: 'bg-deloitte-teal-light/10 border-deloitte-teal-light/20',
    },
    {
      icon: GitCompare,
      text: 'Compare current vs prior forecast',
      color: 'text-deloitte-green-light',
      bg: 'bg-deloitte-green-light/10 border-deloitte-green-light/20',
    },
  ];

  return (
    <div className="flex-1 flex items-center justify-center">
      <div className="text-center max-w-xl px-4">
        <div className="inline-flex items-center gap-3 mb-6">
          <div className="w-1.5 h-12 bg-deloitte-green rounded-full" />
          <div className="text-left">
            <h2 className="text-3xl font-bold text-white tracking-tight">
              Rolling Forecast
            </h2>
            <p className="text-deloitte-green text-xs font-semibold tracking-[0.2em] uppercase">
              AI-Powered FP&A
            </p>
          </div>
        </div>
        <p className="text-surface-400 mb-10 text-sm leading-relaxed max-w-md mx-auto">
          Generate statistical forecasts, analyze variances, manage overrides,
          and answer questions about your financial data through natural conversation.
        </p>
        <div className="grid grid-cols-2 gap-3">
          {suggestions.map((s, i) => (
            <button
              key={i}
              onClick={() => onSuggestion(s.text)}
              className={`p-4 text-left ${s.bg} border rounded-xl hover:scale-[1.02] transition-all group`}
            >
              <s.icon className={`w-5 h-5 ${s.color} mb-2 group-hover:scale-110 transition-transform`} />
              <span className="text-sm text-surface-300 group-hover:text-white transition-colors">
                {s.text}
              </span>
            </button>
          ))}
        </div>
      </div>
    </div>
  );
}
