import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import type { ChatMessage, Conversation, ContentBlock } from '../types/chat';

interface ChatState {
  conversations: Conversation[];
  activeConversationId: string | null;
  messages: ChatMessage[];
  sidebarExpanded: boolean;

  isStreaming: boolean;
  streamingMessage: ChatMessage | null;
  currentToolName: string | null;

  setConversations: (conversations: Conversation[]) => void;
  removeConversation: (id: string) => void;
  setActiveConversation: (id: string | null) => void;
  setMessages: (messages: ChatMessage[]) => void;
  addMessage: (message: ChatMessage) => void;
  setSidebarExpanded: (expanded: boolean) => void;
  toggleSidebar: () => void;

  startStreaming: (conversationId: string) => void;
  appendStreamContent: (text: string) => void;
  addStreamContentBlock: (block: ContentBlock) => void;
  setToolInProgress: (toolName: string | null) => void;
  finishStreaming: (finalMessage: ChatMessage) => void;
  cancelStreaming: () => void;
  /** Drop trailing assistant (+ optional user) so a message can be regenerated. */
  truncateForRegenerate: () => string | null;
}

export const useChatStore = create<ChatState>()(
  persist(
    (set) => ({
      conversations: [],
      activeConversationId: null,
      messages: [],
      sidebarExpanded: true,
      isStreaming: false,
      streamingMessage: null,
      currentToolName: null,

      setConversations: (conversations) => set({ conversations }),

      removeConversation: (id) =>
        set((state) => ({
          conversations: state.conversations.filter((c) => c.id !== id),
          ...(state.activeConversationId === id
            ? { activeConversationId: null, messages: [] }
            : {}),
        })),

      setActiveConversation: (id) => set({ activeConversationId: id }),

      setMessages: (messages) => set({ messages }),

      addMessage: (message) =>
        set((state) => ({
          messages: [...state.messages, message],
        })),

      setSidebarExpanded: (expanded) => set({ sidebarExpanded: expanded }),

      toggleSidebar: () =>
        set((state) => ({
          sidebarExpanded: !state.sidebarExpanded,
        })),

      startStreaming: (conversationId) =>
        set({
          isStreaming: true,
          streamingMessage: {
            id: 'streaming',
            conversation_id: conversationId,
            role: 'assistant',
            content: '',
            content_blocks: [],
            created_at: new Date().toISOString(),
            isStreaming: true,
          },
        }),

      appendStreamContent: (text) =>
        set((state) => {
          if (!state.streamingMessage) return {};
          return {
            streamingMessage: {
              ...state.streamingMessage,
              content: state.streamingMessage.content + text,
            },
          };
        }),

      addStreamContentBlock: (block) =>
        set((state) => {
          if (!state.streamingMessage) return {};
          return {
            streamingMessage: {
              ...state.streamingMessage,
              content_blocks: [...state.streamingMessage.content_blocks, block],
            },
          };
        }),

      setToolInProgress: (toolName) => set({ currentToolName: toolName }),

      finishStreaming: (finalMessage) =>
        set((state) => ({
          isStreaming: false,
          streamingMessage: null,
          currentToolName: null,
          messages: [...state.messages, finalMessage],
        })),

      cancelStreaming: () =>
        set({
          isStreaming: false,
          streamingMessage: null,
          currentToolName: null,
        }),

      truncateForRegenerate: () => {
        let prompt: string | null = null;
        set((state) => {
          const msgs = [...state.messages];
          // Remove trailing assistant message(s)
          while (msgs.length && msgs[msgs.length - 1].role === 'assistant') {
            msgs.pop();
          }
          // Capture and remove the user prompt that produced them
          if (msgs.length && msgs[msgs.length - 1].role === 'user') {
            prompt = msgs[msgs.length - 1].content;
            msgs.pop();
          }
          return { messages: msgs };
        });
        return prompt;
      },
    }),
    {
      name: 'rf_chat_store',
      partialize: (s) => ({
        activeConversationId: s.activeConversationId,
        sidebarExpanded: s.sidebarExpanded,
      }),
    },
  ),
);
