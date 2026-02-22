import type { ChatMessage } from '../../types/chat';
import { Loader2 } from 'lucide-react';
import { useChatStore } from '../../store/chatStore';

interface Props {
  message: ChatMessage;
}

export function StreamingMessage({ message }: Props) {
  const currentToolName = useChatStore((s) => s.currentToolName);

  const hasContent = message.content && message.content.trim().length > 0;
  const hasBlocks = message.content_blocks && message.content_blocks.length > 0;

  return (
    <div className="flex gap-3 chat-message-enter">
      <div className="flex-shrink-0 w-8 h-8 rounded-lg bg-deloitte-green/15 flex items-center justify-center mt-1 border border-deloitte-green/20">
        <div className="w-1 h-4 bg-deloitte-green rounded-full animate-pulse" />
      </div>

      <div className="space-y-3 max-w-[80%]">
        {/* Tool in progress */}
        {currentToolName && (
          <div className="inline-flex items-center gap-2 px-3 py-2 bg-deloitte-green/8 border border-deloitte-green/20 rounded-lg text-sm text-deloitte-green">
            <Loader2 className="w-3.5 h-3.5 animate-spin" />
            Running: {currentToolName.replace(/_/g, ' ')}
          </div>
        )}

        {/* Streaming text content (from token events) */}
        {hasContent && !hasBlocks && (
          <p className="text-surface-200 leading-relaxed whitespace-pre-wrap">
            {message.content}
            <span className="inline-block w-1.5 h-4 bg-deloitte-green/70 rounded-sm ml-0.5 animate-pulse" />
          </p>
        )}

        {/* Content blocks accumulated so far */}
        {hasBlocks && message.content_blocks.map((block, i) => {
          try {
            if (!block || !block.data) return null;
            if (block.type === 'text') {
              return (
                <p key={i} className="text-surface-200 leading-relaxed whitespace-pre-wrap">
                  {String(block.data.text ?? '')}
                </p>
              );
            }
            if (block.type === 'table' && block.data.title) {
              return (
                <div key={i} className="text-xs text-surface-400 bg-surface-800/50 rounded-lg px-3 py-2 border border-surface-700">
                  Loading table: {block.data.title}...
                </div>
              );
            }
            return null;
          } catch {
            return null;
          }
        })}

        {/* Typing indicator — only when nothing else is showing */}
        {!currentToolName && !hasContent && !hasBlocks && (
          <div className="typing-indicator flex gap-1 py-2">
            <span className="w-2 h-2 bg-deloitte-green rounded-full" />
            <span className="w-2 h-2 bg-deloitte-green rounded-full" />
            <span className="w-2 h-2 bg-deloitte-green rounded-full" />
          </div>
        )}
      </div>
    </div>
  );
}
