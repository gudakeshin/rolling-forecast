import { test, expect, type APIRequestContext, type Page } from '@playwright/test';

/**
 * Browser checks from the foundation-model plan's verification list: the
 * refresh-token cookie flow, the "Models disagree" badge, and the instant
 * preview after an actuals upload. Requires E2E_BASE_URL (a running stack with
 * SEED_DEMO_USERS=true). The CI stack has no Chronos-2 sidecar, so the two
 * Chronos-2 responses are stubbed with page.route; everything else is real.
 */
const enabled = !!process.env.E2E_BASE_URL;
const ANALYST = process.env.E2E_USER || 'analyst';
const ANALYST_PW = process.env.E2E_PASSWORD || 'analyst';
const BOGUS_TOKEN = 'expired.or.tampered.token';

// /auth/login allows 10 attempts a minute per client: log in once per user.
let cached: string | null = null;
async function accessToken(request: APIRequestContext): Promise<string> {
  if (!cached) {
    const res = await request.post('/api/auth/login', { data: { username: ANALYST, password: ANALYST_PW } });
    expect(res.ok(), await res.text()).toBeTruthy();
    cached = (await res.json()).access_token as string;
  }
  return cached;
}

/** Seed the app's persisted auth state before any page script runs. */
async function seedAuth(page: Page, token: string) {
  await page.addInitScript((t) => {
    localStorage.setItem(
      'forecast-auth',
      JSON.stringify({ state: { token: t, user: null, isAuthenticated: true }, version: 0 }),
    );
  }, token);
}

async function openSignedIn(page: Page) {
  await seedAuth(page, await accessToken(page.request));
  await page.goto('/');
  await expect(page.getByLabel(/message composer/i)).toBeVisible({ timeout: 15_000 });
}

test.describe('foundation-model features', () => {
  test.skip(!enabled, 'Set E2E_BASE_URL to run Playwright against a live stack');

  test('an expired access token is renewed from the httpOnly refresh cookie', async ({ page, context }) => {
    // Browser-style login: the refresh token comes back only as a cookie.
    const res = await page.request.post('/api/auth/login', {
      data: { username: ANALYST, password: ANALYST_PW },
      headers: { 'X-Auth-Cookie': '1' },
    });
    expect(res.ok(), await res.text()).toBeTruthy();
    expect((await res.json()).refresh_token ?? null).toBeNull();
    const cookie = (await context.cookies()).find((c) => c.name === 'rf_refresh');
    expect(cookie?.httpOnly).toBe(true);
    expect(cookie?.path).toBe('/api/auth');

    // The page starts with a token the server rejects; the client must refresh.
    await seedAuth(page, BOGUS_TOKEN);
    const refreshed = page.waitForResponse(
      (r) => r.url().includes('/api/auth/refresh') && r.request().method() === 'POST',
      { timeout: 15_000 },
    );
    await page.goto('/');
    expect((await refreshed).status()).toBe(200);
    await expect(page.getByLabel(/message composer/i)).toBeVisible({ timeout: 15_000 });
    await expect
      .poll(() => page.evaluate(() => JSON.parse(localStorage.getItem('forecast-auth') || '{}')?.state?.token))
      .not.toBe(BOGUS_TOKEN);
    const token = await page.evaluate(() => JSON.parse(localStorage.getItem('forecast-auth') || '{}')?.state?.token);
    expect(token).toBeTruthy();
    expect(token).not.toBe(BOGUS_TOKEN);
    // Page script can never read the refresh token.
    expect(await page.evaluate(() => document.cookie)).not.toContain('rf_refresh');

    // With no cookie and no valid token in the tab, there is nothing to refresh
    // with: back to sign-in. (The tab keeps its own access token in
    // sessionStorage, which still works until it expires, so clear it too.)
    await context.clearCookies();
    await page.evaluate(() => sessionStorage.clear());
    await page.goto('/');
    await expect(page).toHaveURL(/\/login/, { timeout: 15_000 });
  });

  test('the review queue shows the "Models disagree" badge', async ({ page }) => {
    await page.route('**/api/panel/review-queue/**', (route) =>
      route.fulfill({
        json: {
          panel_type: 'review_queue',
          title: 'Review Queue',
          data: {
            items: [
              {
                id: 'e2e-disagree', line_item_id: 1, line_item_name: 'E2E disagreeing line', category: 'Revenue',
                period: '2026-01', p50: 1000, confidence_score: 92, confidence_level: 'high', model_type: 'ets',
                is_overridden: false, models_disagree: true,
              },
              {
                id: 'e2e-calm', line_item_id: 2, line_item_name: 'E2E agreeing line', category: 'Revenue',
                period: '2026-01', p50: 900, confidence_score: 40, confidence_level: 'low', model_type: 'ets',
                is_overridden: false, models_disagree: false,
              },
            ],
            total_count: 2,
          },
        },
      }),
    );
    await openSignedIn(page);
    const pp = encodeURIComponent(JSON.stringify({ version_id: 'e2e-version' }));
    await page.goto(`/?panel=review_queue&pp=${pp}`);
    await expect(page.getByText('E2E disagreeing line')).toBeVisible({ timeout: 15_000 });
    await expect(page.getByTestId('models-disagree-badge')).toHaveCount(1);
    await expect(page.getByTestId('models-disagree-badge')).toHaveText('Models disagree');
  });

  test('uploading actuals opens the instant preview', async ({ page }) => {
    let previewedDataset: string | null = null;
    await page.route('**/api/panel/forecast-preview/**', (route) => {
      previewedDataset = new URL(route.request().url()).pathname.split('/').pop() ?? null;
      return route.fulfill({
        json: {
          panel_type: 'forecast_preview',
          title: 'Instant forecast preview',
          data: {
            dataset_id: previewedDataset, horizon: 12, available: true, preview: true, reporting_currency: 'USD',
            skipped: [],
            lines: [{
              line_item_id: 1, line_item_name: 'E2E preview line', account_code: 'E2E-P-1', category: 'Revenue',
              n_history: 24, last_12_actual: 120000, total_p50: 130000,
              history: [{ period: '2025-12', actual: 10000 }],
              forecast: Array.from({ length: 12 }, (_, i) => ({
                period: `2026-${String(i + 1).padStart(2, '0')}`, p10: 9000, p50: 10800, p90: 12500,
              })),
            }],
          },
        },
      });
    });
    await openSignedIn(page);

    const tag = Date.now().toString(36);
    const rows = ['account_code,account_name,category,business_unit,geography,product_line,period,value,currency'];
    for (let m = 0; m < 24; m += 1) {
      const period = `${2024 + Math.floor(m / 12)}-${String((m % 12) + 1).padStart(2, '0')}`;
      rows.push(`E2E-P-${tag},E2E Preview ${tag},Revenue,North America,North America,All,${period},${1000 + m * 25},USD`);
    }
    const chooser = page.waitForEvent('filechooser');
    await page.getByRole('button', { name: 'Upload file' }).click();
    await (await chooser).setFiles({
      name: `preview-${tag}.csv`, mimeType: 'text/csv', buffer: Buffer.from(rows.join('\n')),
    });

    await expect(page.getByTestId('forecast-preview')).toBeVisible({ timeout: 30_000 });
    await expect(page.getByText('E2E preview line')).toBeVisible();
    await expect(page.getByText(/Instant preview from Chronos-2/)).toBeVisible();
    expect(previewedDataset).toBeTruthy(); // the real upload's dataset id reached the preview call
  });
});
