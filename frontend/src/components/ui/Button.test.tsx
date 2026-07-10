import { fireEvent, render } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { axe } from 'vitest-axe';
import { Button } from './Button';
import { SlidePanel } from './SlidePanel';

describe('Button a11y', () => {
  it('has no axe violations', async () => {
    const { container } = render(<Button>Save forecast</Button>);
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});

describe('SlidePanel a11y', () => {
  it('exposes dialog semantics without axe violations', async () => {
    const { container } = render(
      <SlidePanel title="Forecast table" onClose={() => undefined}>
        <p>Panel body</p>
      </SlidePanel>,
    );
    const dialog = container.querySelector('[role="dialog"]');
    expect(dialog).toBeTruthy();
    expect(dialog?.getAttribute('aria-modal')).toBe('true');
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });

  it('traps Tab focus within the dialog', () => {
    const onClose = vi.fn();
    const { container } = render(
      <SlidePanel title="Forecast table" onClose={onClose}>
        <button type="button">Body action</button>
      </SlidePanel>,
    );
    const dialog = container.querySelector('[role="dialog"]') as HTMLElement;
    const focusables = Array.from(
      dialog.querySelectorAll<HTMLElement>(
        'button:not([disabled]), [href], input, select, textarea, [tabindex]:not([tabindex="-1"])',
      ),
    );
    expect(focusables.length).toBeGreaterThanOrEqual(2);

    const first = focusables[0];
    const last = focusables[focusables.length - 1];
    last.focus();
    fireEvent.keyDown(document, { key: 'Tab' });
    expect(document.activeElement).toBe(first);

    first.focus();
    fireEvent.keyDown(document, { key: 'Tab', shiftKey: true });
    expect(document.activeElement).toBe(last);
  });
});
