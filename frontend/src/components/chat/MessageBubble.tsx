import type { ChatMessage } from '../../types/chat';
import { TextRenderer } from './renderers/TextRenderer';
import { TableRenderer } from './renderers/TableRenderer';
import { ChartRenderer } from './renderers/ChartRenderer';
import { StatusCard } from './renderers/StatusCard';
import { ActionCard } from './renderers/ActionCard';
import { PanelTrigger } from './renderers/PanelTrigger';
import { CitationsRenderer } from './renderers/CitationsRenderer';
import { IconButton } from '../ui/Pressable';
import { User, Wrench, RotateCcw } from 'lucide-react';
import { useI18n } from '../../i18n/useI18n';

interface Props {
  message: ChatMessage;
  onRegenerate?: () => void;
  canRegenerate?: boolean;
}

export function MessageBubble({ message, onRegenerate, canRegenerate }: Props) {
  const isUser = message.role === 'user';
  const { t } = useI18n();

  return (
    <div className={`flex gap-3 chat-message-enter ${isUser ? 'justify-end' : ''}`}>
      {/* Agent avatar */}
      {!isUser && (
        <div className="flex-shrink-0 w-8 h-8 rounded-lg bg-deloitte-green/15 flex items-center justify-center mt-1 border border-deloitte-green/20">
          <div className="w-1 h-4 bg-deloitte-green rounded-full" />
        </div>
      )}

      {/* Message content */}
      <div
        className={`max-w-[80%] ${
          isUser
            ? 'bg-deloitte-green-dark rounded-2xl rounded-br-md px-4 py-3'
            : 'space-y-3'
        }`}
      >
        {isUser ? (
          <p className="text-white">{message.content}</p>
        ) : (
          <>
            {/* Tool calls indicator */}
            {message.tool_calls && message.tool_calls.length > 0 && (
              <div className="flex flex-wrap gap-2 mb-2">
                {message.tool_calls.map((tc, i) => (
                  <span
                    key={i}
                    className="inline-flex items-center gap-1.5 px-2.5 py-1 bg-deloitte-green/8 border border-deloitte-green/20 rounded-lg text-xs text-deloitte-green"
                  >
                    <Wrench className="w-3 h-3" />
                    {tc.tool.replace(/_/g, ' ')}
                  </span>
                ))}
              </div>
            )}

            {/* Content blocks */}
            {message.content_blocks && message.content_blocks.length > 0 ? (
              (() => {
                const citationsBlock = message.content_blocks.find((b) => b.type === 'citations');
                const citations = citationsBlock?.data?.citations;
                return message.content_blocks.map((block, i) => (
                  <ContentBlockRenderer key={i} block={block} citations={citations} />
                ));
              })()
            ) : (
              <TextRenderer text={message.content} />
            )}

            {canRegenerate && onRegenerate && (
              <div className="pt-1">
                <IconButton
                  label={t('chat.regenerate')}
                  title={t('chat.regenerate')}
                  onClick={() => onRegenerate()}
                  className="text-surface-500 hover:text-surface-200 gap-1.5 px-2"
                >
                  <RotateCcw className="w-3.5 h-3.5" />
                  <span className="text-xs">{t('chat.regenerate')}</span>
                </IconButton>
              </div>
            )}
          </>
        )}
      </div>

      {/* User avatar */}
      {isUser && (
        <div className="flex-shrink-0 w-8 h-8 rounded-lg bg-surface-700 flex items-center justify-center mt-1">
          <User className="w-4 h-4 text-surface-400" />
        </div>
      )}
    </div>
  );
}

function ContentBlockRenderer({
  block,
  citations,
}: {
  block: { type: string; data: any };
  citations?: any[];
}) {
  try {
    if (!block || !block.data) {
      return null;
    }
    switch (block.type) {
      case 'text':
        return <TextRenderer text={block.data.text ?? ''} citations={citations} />;
      case 'table':
        return <TableRenderer data={block.data} />;
      case 'chart':
        return <ChartRenderer data={block.data} />;
      case 'status':
        return <StatusCard data={block.data} />;
      case 'action':
        return <ActionCard data={block.data} />;
      case 'panel_trigger':
        return <PanelTrigger data={block.data} />;
      case 'citations':
        return <CitationsRenderer data={block.data} />;
      default:
        return <TextRenderer text={JSON.stringify(block.data)} />;
    }
  } catch (err) {
    console.error('[ContentBlockRenderer] Error rendering block:', block.type, err);
    return (
      <div className="text-xs text-red-400 bg-red-400/10 border border-red-400/20 rounded-lg px-3 py-2">
        Failed to render {block.type} block
      </div>
    );
  }
}
