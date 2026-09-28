import type { ChatMessage } from '../../types/chat';
import { MessageBubble } from './MessageBubble';
import { StreamingMessage } from './StreamingMessage';
import { FollowUpChips } from './FollowUpChips';

interface Props {
  messages: ChatMessage[];
  streamingMessage: ChatMessage | null;
  onRegenerate?: () => void;
  isStreaming?: boolean;
  onSend?: (content: string) => void;
}

export function MessageList({ messages, streamingMessage, onRegenerate, isStreaming, onSend }: Props) {
  const lastAssistantIdx = (() => {
    for (let i = messages.length - 1; i >= 0; i--) {
      if (messages[i].role === 'assistant') return i;
    }
    return -1;
  })();
  const showFollowUps = !isStreaming && !streamingMessage && Boolean(onSend) && lastAssistantIdx >= 0;

  return (
    <div className="space-y-6" role="log" aria-live="polite" aria-relevant="additions" aria-label="Conversation messages">
      {messages.map((message, idx) => (
        <div key={message.id}>
          <MessageBubble
            message={message}
            canRegenerate={
              !isStreaming &&
              !streamingMessage &&
              Boolean(onRegenerate) &&
              idx === lastAssistantIdx
            }
            onRegenerate={onRegenerate}
          />
          {showFollowUps && idx === lastAssistantIdx && onSend && (
            <FollowUpChips message={message} onSend={onSend} />
          )}
        </div>
      ))}
      {streamingMessage && <StreamingMessage message={streamingMessage} />}
    </div>
  );
}
