import { ButtonHTMLAttributes, forwardRef } from 'react';
import clsx from 'clsx';

type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
}

const variantClass: Record<Variant, string> = {
  primary: 'bg-deloitte-green hover:bg-deloitte-green/90 text-black',
  secondary: 'bg-surface-700 hover:bg-surface-600 text-surface-200 border border-surface-600',
  ghost: 'bg-transparent hover:bg-surface-800 text-surface-300',
  danger: 'bg-red-500/15 hover:bg-red-500/25 text-red-400 border border-red-500/30',
};

export const Button = forwardRef<HTMLButtonElement, ButtonProps>(
  function Button({ variant = 'secondary', className, type = 'button', children, ...rest }, ref) {
    return (
      <button
        ref={ref}
        type={type}
        className={clsx(
          'inline-flex items-center justify-center gap-2 px-4 py-2 rounded-lg text-sm font-medium transition-all min-h-[44px] focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-deloitte-green disabled:opacity-50',
          variantClass[variant],
          className,
        )}
        {...rest}
      >
        {children}
      </button>
    );
  },
);
