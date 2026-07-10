import { render } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { axe } from 'vitest-axe';
import { Button } from './Button';

describe('Button a11y', () => {
  it('has no axe violations', async () => {
    const { container } = render(<Button>Save forecast</Button>);
    const results = await axe(container);
    expect(results).toHaveNoViolations();
  });
});
