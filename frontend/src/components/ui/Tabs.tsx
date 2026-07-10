import { ReactNode, useId } from 'react';
import clsx from 'clsx';

export interface TabItem<T extends string = string> {
  id: T;
  label: string;
}

interface TabsProps<T extends string = string> {
  tabs: TabItem<T>[];
  value: T;
  onChange: (id: T) => void;
  className?: string;
  /** Optional panel content keyed by tab id — when provided, renders the active panel. */
  children?: (active: T) => ReactNode;
}

export function Tabs<T extends string>({
  tabs,
  value,
  onChange,
  className,
  children,
}: TabsProps<T>) {
  const baseId = useId().replace(/:/g, '');
  const hasPanels = typeof children === 'function';

  return (
    <div className={className}>
      <div
        role="tablist"
        aria-orientation="horizontal"
        className="flex gap-1 bg-surface-800/50 rounded-lg p-1"
      >
        {tabs.map((tab) => {
          const selected = tab.id === value;
          return (
            <button
              key={tab.id}
              type="button"
              role="tab"
              id={`${baseId}-tab-${tab.id}`}
              aria-selected={selected}
              aria-controls={hasPanels ? `${baseId}-panel-${tab.id}` : undefined}
              tabIndex={selected ? 0 : -1}
              onClick={() => onChange(tab.id)}
              onKeyDown={(e) => {
                const idx = tabs.findIndex((t) => t.id === tab.id);
                if (e.key === 'ArrowRight') {
                  e.preventDefault();
                  onChange(tabs[(idx + 1) % tabs.length].id);
                } else if (e.key === 'ArrowLeft') {
                  e.preventDefault();
                  onChange(tabs[(idx - 1 + tabs.length) % tabs.length].id);
                } else if (e.key === 'Home') {
                  e.preventDefault();
                  onChange(tabs[0].id);
                } else if (e.key === 'End') {
                  e.preventDefault();
                  onChange(tabs[tabs.length - 1].id);
                }
              }}
              className={clsx(
                'flex-1 px-3 py-1.5 text-xs font-medium rounded-md transition-all min-h-[36px]',
                'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-deloitte-green',
                selected
                  ? 'bg-deloitte-green/20 text-deloitte-green border border-deloitte-green/30'
                  : 'text-surface-300 hover:text-white hover:bg-surface-700/50',
              )}
            >
              {tab.label}
            </button>
          );
        })}
      </div>
      {hasPanels ? (
        <div
          role="tabpanel"
          id={`${baseId}-panel-${value}`}
          aria-labelledby={`${baseId}-tab-${value}`}
          className="mt-3"
        >
          {children!(value)}
        </div>
      ) : null}
    </div>
  );
}
