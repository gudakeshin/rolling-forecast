import { useMemo } from 'react';
import type { ChatMessage } from '../../types/chat';
import type { MessageKey } from '../../i18n';
import { useI18n } from '../../i18n/useI18n';

const MAX_CHIPS = 3;

/** Picks a few contextual continuation prompts based on what the assistant just showed. */
function pickFollowUpKeys(message: ChatMessage): MessageKey[] {
  const blockTypes = new Set(message.content_blocks.map((b) => b.type));
  const keys: MessageKey[] = [];
  if (blockTypes.has('table') || blockTypes.has('chart')) {
    keys.push('chat.followup.deviations', 'chat.followup.drivers');
  }
  if (!keys.includes('chat.followup.compare')) keys.push('chat.followup.compare');
  if (!keys.includes('chat.followup.nextSteps')) keys.push('chat.followup.nextSteps');
  keys.push('chat.followup.explain');
  return keys.slice(0, MAX_CHIPS);
}

interface Props {
  message: ChatMessage;
  onSend: (content: string) => void;
}

/**
 * Lightweight, client-only continuation prompts shown under the latest
 * assistant reply — distinct from the backend-driven ActionCard buttons
 * (which trigger operational actions like approve/open-panel), these just
 * keep the conversation going with one click.
 */
export function FollowUpChips({ message, onSend }: Props) {
  const { t } = useI18n();
  const hasActionBlock = message.content_blocks.some((b) => b.type === 'action');
  const keys = useMemo(() => pickFollowUpKeys(message), [message]);

  if (hasActionBlock || keys.length === 0) return null;

  return (
    <div className="flex flex-wrap gap-2 mt-2 pl-9" role="group" aria-label={t('chat.followup.hint')}>
      {keys.map((key) => (
        <button
          key={key}
          type="button"
          onClick={() => onSend(t(key))}
          className="px-3 py-1.5 text-xs font-medium text-surface-300 bg-surface-800 hover:bg-surface-700 border border-surface-700 rounded-full transition-colors"
        >
          {t(key)}
        </button>
      ))}
    </div>
  );
}
