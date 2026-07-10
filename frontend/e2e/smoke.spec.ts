import { test, expect } from '@playwright/test';

/**
 * Smoke: login → open chat → trigger a panel.
 * Skips unless E2E_BASE_URL points at a running stack (frontend + backend).
 */
const enabled = !!process.env.E2E_BASE_URL;

test.describe('enterprise smoke', () => {
  test.skip(!enabled, 'Set E2E_BASE_URL (e.g. http://localhost:5173) to run e2e smoke');

  test('login opens chat composer', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel(/username/i).fill(process.env.E2E_USER || 'analyst');
    await page.getByLabel(/password/i).fill(process.env.E2E_PASSWORD || 'analyst');
    await page.getByRole('button', { name: /sign in|log in|login/i }).click();

    await expect(page.getByLabel(/message composer/i)).toBeVisible({ timeout: 15_000 });
  });

  test('chat can open a side panel', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel(/username/i).fill(process.env.E2E_USER || 'analyst');
    await page.getByLabel(/password/i).fill(process.env.E2E_PASSWORD || 'analyst');
    await page.getByRole('button', { name: /sign in|log in|login/i }).click();

    const composer = page.getByLabel(/message composer/i);
    await expect(composer).toBeVisible({ timeout: 15_000 });
    await composer.fill('Show me the forecast table');
    await page.keyboard.press('Enter');

    // Panel dialog may open from agent action or existing UI affordance
    const panel = page.getByRole('dialog');
    await expect(panel.or(page.getByText(/forecast/i).first())).toBeVisible({ timeout: 45_000 });
  });
});
