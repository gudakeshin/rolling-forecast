import type { ChatMessage } from '../../types/chat';
import { MessageBubble } from './MessageBubble';
import { StreamingMessage } from './StreamingMessage';

interface Props {
  messages: ChatMessage[];
  streamingMessage: ChatMessage | null;
}

export function MessageList({ messages, streamingMessage }: Props) {
  return (
    <div className="space-y-6" role="log" aria-live="polite" aria-relevant="additions" aria-label="Conversation messages">
      {messages.map((message) => (
        <MessageBubble key={message.id} message={message} />
      ))}
      {streamingMessage && <StreamingMessage message={streamingMessage} />}
    </div>
  );
}
