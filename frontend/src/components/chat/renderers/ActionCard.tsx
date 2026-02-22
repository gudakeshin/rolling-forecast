interface Props {
  data: {
    actions: { id: string; label: string; variant?: string }[];
  };
}

export function ActionCard({ data }: Props) {
  const { actions } = data;

  const handleAction = (actionId: string) => {
    console.log('Action clicked:', actionId);
  };

  return (
    <div className="flex flex-wrap gap-2">
      {actions.map((action) => (
        <button
          key={action.id}
          onClick={() => handleAction(action.id)}
          className={`px-4 py-2 rounded-lg text-sm font-medium transition-all ${
            action.variant === 'primary'
              ? 'bg-deloitte-green hover:bg-deloitte-green/90 text-black'
              : action.variant === 'danger'
              ? 'bg-red-500/15 hover:bg-red-500/25 text-red-400 border border-red-500/30'
              : 'bg-surface-700 hover:bg-surface-600 text-surface-300 border border-surface-600'
          }`}
        >
          {action.label}
        </button>
      ))}
    </div>
  );
}
