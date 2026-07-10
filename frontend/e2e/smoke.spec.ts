import { test, expect } from '@playwright/test';

/**
 * Smoke: login → chat composer visible → open a side panel via UI.
 * Requires E2E_BASE_URL pointing at a running stack (frontend + backend).
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

  test('can open executive view from the UI', async ({ page }) => {
    await page.goto('/login');
    await page.getByLabel(/username/i).fill(process.env.E2E_USER || 'analyst');
    await page.getByLabel(/password/i).fill(process.env.E2E_PASSWORD || 'analyst');
    await page.getByRole('button', { name: /sign in|log in|login/i }).click();

    await expect(page.getByLabel(/message composer/i)).toBeVisible({ timeout: 15_000 });

    await page.getByRole('link', { name: /executive/i }).click();
    await expect(page).toHaveURL(/executive/i, { timeout: 15_000 });
  });
});
