import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
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
});
