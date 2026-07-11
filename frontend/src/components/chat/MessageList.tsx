import type { ChatMessage } from '../../types/chat';
import { MessageBubble } from './MessageBubble';
import { StreamingMessage } from './StreamingMessage';

interface Props {
  messages: ChatMessage[];
  streamingMessage: ChatMessage | null;
  onRegenerate?: () => void;
  isStreaming?: boolean;
}

export function MessageList({ messages, streamingMessage, onRegenerate, isStreaming }: Props) {
  const lastAssistantIdx = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant') return i;
    }
    return -1;
  })();

  return (
    <div className="space-y-6" role="log" aria-live="polite" aria-relevant="additions" aria-label="Conversation messages">
      {messages.map((message, idx) => (
        <MessageBubble
          key={message.id}
          message={message}
          canRegenerate={
            !isStreaming &&
            !streamingMessage &&
            Boolean(onRegenerate) &&
            idx === lastAssistantIdx
          }
          onRegenerate={onRegenerate}
        />
      ))}
      {streamingMessage && <StreamingMessage message={streamingMessage} />}
    </div>
  );
}
