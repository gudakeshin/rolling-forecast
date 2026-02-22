export interface ContentBlock {
  type: 'text' | 'table' | 'chart' | 'status' | 'action' | 'panel_trigger';
  data: Record<string, any>;
}

export interface ChatMessage {
  id: string;
  conversation_id: string;
  role: 'user' | 'assistant' | 'system';
  content: string;
  content_blocks: ContentBlock[];
  tool_calls?: ToolCall[];
  panel_payload?: PanelPayload;
  created_at: string;
  isStreaming?: boolean;
}

export interface ToolCall {
  tool: string;
  input: Record<string, any>;
  output_summary: string;
}

export interface PanelPayload {
  panel: string;
  params: Record<string, any>;
  title?: string;
}

export interface Conversation {
  id: string;
  title: string;
  created_at: string;
  updated_at: string;
  message_count: number;
}

export interface StreamEvent {
  event: string;
  data: Record<string, any>;
}

export interface TableData {
  title: string;
  columns: { key: string; label: string }[];
  rows: Record<string, string>[];
}

export interface StatusData {
  label: string;
  progress: number;
  step: string;
  is_complete: boolean;
}

export interface ActionData {
  actions: { id: string; label: string; variant?: string }[];
}

export interface PanelTriggerData {
  panel: string;
  params: Record<string, any>;
  label: string;
}
