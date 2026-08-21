import {
  type ButtonHTMLAttributes,
  type KeyboardEvent,
  type MouseEvent,
  type ReactNode,
  forwardRef,
} from 'react';
import clsx from 'clsx';

type PressableProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  asChild?: never;
};

/** Accessible press target — prefer over clickable divs. */
export const Pressable = forwardRef<HTMLButtonElement, PressableProps>(
  function Pressable({ className, type = 'button', children, ...rest }, ref) {
    return (
      <button
        ref={ref}
        type={type}
        className={clsx(
          'text-left focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-deloitte-green',
          className,
        )}
        {...rest}
      >
        {children}
      </button>
    );
  },
);

/** Stop row/card click bubbling without making the wrapper an interactive control. */
export function stopRowClick(e: MouseEvent | KeyboardEvent) {
  e.stopPropagation();
}

export function onActivate(
  handler: () => void,
): { onClick: () => void; onKeyDown: (e: KeyboardEvent) => void } {
  return {
    onClick: handler,
    onKeyDown: (e: KeyboardEvent) => {
      if (e.key === 'Enter' || e.key === ' ') {
        e.preventDefault();
        handler();
      }
    },
  };
}

export function IconButton({
  label,
  className,
  children,
  ...rest
}: ButtonHTMLAttributes<HTMLButtonElement> & { label: string; children: ReactNode }) {
  return (
    <button
      type="button"
      aria-label={label}
      className={clsx(
        'inline-flex items-center justify-center min-h-[44px] min-w-[44px] rounded-lg text-surface-400 hover:text-white hover:bg-surface-700 transition-colors focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-deloitte-green',
        className,
      )}
      {...rest}
    >
      {children}
    </button>
  );
}
