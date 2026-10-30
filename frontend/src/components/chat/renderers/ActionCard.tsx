import { Button } from '../../ui/Button';
import { dispatchRecommendedAction, type RecommendedAction } from '../../../utils/recommendedActions';

interface Props {
  data: {
    actions: RecommendedAction[];
  };
}

export function ActionCard({ data }: Props) {
  const { actions } = data;

  return (
    <div className="flex flex-wrap gap-2" role="group" aria-label="Suggested actions">
      {actions.map((action) => (
        <Button
          key={action.id || action.type || action.label}
          variant={
            action.variant === 'primary'
              ? 'primary'
              : action.variant === 'danger'
                ? 'danger'
                : 'secondary'
          }
          onClick={() => {
            void dispatchRecommendedAction(action);
          }}
        >
          {action.label}
        </Button>
      ))}
    </div>
  );
}
