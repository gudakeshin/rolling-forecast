import { create } from 'zustand';
import type { ChatMessage, Conversation, ContentBlock } from '../types/chat';

interface ChatState {
  // Conversations
  conversations: Conversation[];
  activeConversationId: string | null;
  messages: ChatMessage[];

  // Streaming state
  isStreaming: boolean;
  streamingMessage: ChatMessage | null;
  currentToolName: string | null;

  // Actions
  setConversations: (conversations: Conversation[]) => void;
  setActiveConversation: (id: string | null) => void;
  setMessages: (messages: ChatMessage[]) => void;
  addMessage: (message: ChatMessage) => void;

  // Streaming actions
  startStreaming: (conversationId: string) => void;
  appendStreamContent: (text: string) => void;
  addStreamContentBlock: (block: ContentBlock) => void;
  setToolInProgress: (toolName: string | null) => void;
  finishStreaming: (finalMessage: ChatMessage) => void;
  cancelStreaming: () => void;
}

export const useChatStore = create<ChatState>((set, get) => ({
  conversations: [],
  activeConversationId: null,
  messages: [],
  isStreaming: false,
  streamingMessage: null,
  currentToolName: null,

  setConversations: (conversations) => set({ conversations }),

  setActiveConversation: (id) => set({ activeConversationId: id }),

  setMessages: (messages) => set({ messages }),

  addMessage: (message) =>
    set((state) => ({
      messages: [...state.messages, message],
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
}));
