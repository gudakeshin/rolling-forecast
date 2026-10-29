import { test, expect, type APIRequestContext, type Page } from '@playwright/test';

/**
 * Analyst workflows from the enhancement plan's verification list: first-run
 * guidance, ⌘K palette, two panels side by side, saved views that survive a
 * reload, bulk approve with preview + undo, and the full-version export.
 * Requires E2E_BASE_URL pointing at a running stack (frontend + backend) with
 * the demo users seeded (SEED_DEMO_USERS=true).
 */
const enabled = !!process.env.E2E_BASE_URL;
const ANALYST = process.env.E2E_USER || 'analyst';
const ANALYST_PW = process.env.E2E_PASSWORD || 'analyst';
const ADMIN = process.env.E2E_ADMIN_USER || 'admin';
const ADMIN_PW = process.env.E2E_ADMIN_PASSWORD || 'admin';

// /auth/login allows 10 attempts a minute per client, so each user logs in
// once through the API and every page reuses that session (the form itself is
// covered by smoke.spec.ts).
const sessions = new Map<string, { access_token: string; refresh_token: string | null }>();

async function session(request: APIRequestContext, username: string, password: string) {
  let s = sessions.get(username);
  if (!s) {
    const res = await request.post('/api/auth/login', { data: { username, password } });
    expect(res.ok(), await res.text()).toBeTruthy();
    const body = await res.json();
    s = { access_token: body.access_token, refresh_token: body.refresh_token ?? null };
    sessions.set(username, s);
  }
  return s;
}

async function token(request: APIRequestContext, username: string, password: string) {
  return { Authorization: `Bearer ${(await session(request, username, password)).access_token}` };
}

/** Open the app already signed in (App.tsx hydrates the user from a stored token). */
async function login(page: Page, username = ANALYST, password = ANALYST_PW) {
  const s = await session(page.request, username, password);
  await page.addInitScript((auth) => {
    localStorage.setItem(
      'forecast-auth',
      JSON.stringify({
        state: { token: auth.access_token, refreshToken: auth.refresh_token, user: null, isAuthenticated: true },
        version: 0,
      }),
    );
  }, s);
  await page.goto('/');
  await expect(page.getByLabel(/message composer/i)).toBeVisible({ timeout: 15_000 });
}

async function versionCount(request: APIRequestContext): Promise<number> {
  const headers = await token(request, ANALYST, ANALYST_PW);
  const res = await request.get('/api/panel/versions', { headers });
  const body = await res.json();
  return (Array.isArray(body) ? body : body.versions ?? []).length;
}

/** Twelve noisy monthly series for the analyst's company, forecast with a pinned
 * linear preset (no CV, so it runs in seconds), plus a reviewer in the same
 * company (the demo reviewer has no company, so it can see nothing). Returns
 * that reviewer's test credentials. */
async function seedForecast(request: APIRequestContext): Promise<{ username: string; password: string }> {
  const analyst = await token(request, ANALYST, ANALYST_PW);
  const admin = await token(request, ADMIN, ADMIN_PW);
  const tag = Date.now().toString(36);

  const rows = ['account_code,account_name,category,business_unit,geography,product_line,period,value,currency'];
  for (let line = 0; line < 12; line += 1) {
    for (let m = 0; m < 24; m += 1) {
      const period = `${2024 + Math.floor(m / 12)}-${String((m % 12) + 1).padStart(2, '0')}`;
      // A few lines get a late jump so review has something to flag.
      const jump = line % 4 === 0 && m > 18 ? 3 : 1;
      const value = (1000 + line * 150 + m * 20 + ((m * 37 + line * 11) % 90)) * jump;
      rows.push(`E2E-${tag}-${line},E2E Line ${line},${line % 2 ? 'COGS' : 'Revenue'},North America,North America,All,${period},${value},USD`);
    }
  }
  const upload = await request.post('/api/upload/actuals', {
    headers: analyst,
    multipart: {
      file: { name: `e2e-${tag}.csv`, mimeType: 'text/csv', buffer: Buffer.from(rows.join('\n')) },
      business_unit: 'North America',
    },
  });
  expect(upload.ok(), await upload.text()).toBeTruthy();

  const preset = await request.post('/api/model-presets', {
    headers: admin,
    data: { name: `E2E linear ${tag}`, model_type: 'linear' },
  });
  expect(preset.ok(), await preset.text()).toBeTruthy();
  const run = await request.post(`/api/model-presets/${(await preset.json()).id}/run`, {
    headers: analyst,
    data: { business_unit: 'North America', horizon_months: 3, async_job: false },
  });
  expect(run.ok(), await run.text()).toBeTruthy();

  const reviewer = { username: `e2e_reviewer_${tag}`, password: `e2e-${tag}-${Math.random().toString(36).slice(2)}` };
  const created = await request.post('/api/auth/admin/users', {
    headers: admin,
    data: {
      email: `${reviewer.username}@e2e.test`,
      username: reviewer.username,
      password: reviewer.password,
      business_unit: 'North America',
      role_name: 'reviewer',
    },
  });
  expect(created.ok(), await created.text()).toBeTruthy();
  return reviewer;
}

test.describe.configure({ mode: 'serial' });

test.describe('analyst workflows', () => {
  test.skip(!enabled, 'Set E2E_BASE_URL (e.g. http://localhost:5173) to run e2e workflows');

  test('empty workspace guides the first run', async ({ page, request }) => {
    test.skip((await versionCount(request)) > 0, 'Only meaningful on an empty workspace');
    await login(page);
    await expect(page.getByRole('region', { name: /getting started/i })).toBeVisible();
    await expect(page.getByText(/0 of 4 done|1 of 4 done/)).toBeVisible();
    const comparison = page.getByRole('button', { name: /^comparison/i });
    await expect(comparison).toHaveAttribute('aria-disabled', 'true');
  });

  test('command palette opens a panel', async ({ page }) => {
    await login(page);
    await page.keyboard.press('ControlOrMeta+k');
    const search = page.getByLabel(/open a panel, switch version/i);
    await expect(search).toBeVisible();
    await search.fill('executive');
    await page.keyboard.press('Enter');
    await expect(page.getByRole('dialog')).toBeVisible({ timeout: 15_000 });
  });

  test('two panels side by side, and a saved view survives reload', async ({ page }) => {
    await login(page);
    await page.getByRole('button', { name: /executive dashboard/i }).click();
    await expect(page.getByRole('dialog')).toHaveCount(1, { timeout: 15_000 });
    await page.getByRole('button', { name: /pin beside/i }).first().click();
    await page.getByRole('button', { name: /^document library$/i }).click();
    await expect(page.getByRole('dialog')).toHaveCount(2, { timeout: 15_000 });

    const name = `E2E view ${Date.now().toString(36)}`;
    page.once('dialog', (d) => d.accept(name));
    await page.getByRole('button', { name: /save as view/i }).last().click();
    await page.reload();
    const saved = page.getByRole('button', { name: new RegExp(`^${name}`) });
    await expect(saved).toBeVisible({ timeout: 15_000 });
    await page.getByRole('button', { name: new RegExp(`delete view ${name}`, 'i') }).click();
    await expect(saved).toHaveCount(0);
  });

  test.describe('with a forecast', () => {
    let reviewer: { username: string; password: string };

    test.beforeAll(async ({ request }) => {
      test.setTimeout(180_000);
      reviewer = await seedForecast(request);
    });

    test('bulk approve shows a preview and can be undone', async ({ page }) => {
      await login(page, reviewer.username, reviewer.password);
      await page.getByRole('button', { name: /^review dashboard$/i }).click();
      const panel = page.getByRole('dialog');
      await expect(panel).toBeVisible({ timeout: 20_000 });

      // Seeded lines land in whichever bucket the AI analysis picks: bulk-select
      // in Flagged / Needs Review if any are there, else accept all AI-approved.
      let selectAll = panel.getByRole('button', { name: /^select all$/i });
      for (const bucket of [/flagged/i, /needs review/i]) {
        if (await selectAll.isVisible()) break;
        await panel.getByRole('button', { name: bucket }).first().click();
      }
      if (await selectAll.isVisible()) {
        await selectAll.click();
        await panel.getByRole('button', { name: /^approve \d+$/i }).click();
      } else {
        const acceptAll = panel.getByRole('button', { name: /accept all ai \(\d+\)/i });
        await expect(acceptAll).toBeEnabled();
        await acceptAll.click();
      }

      // Preview step: nothing is approved until the confirmation is accepted.
      const confirm = page.getByRole('alertdialog');
      await expect(confirm.getByText(/you can undo this/i)).toBeVisible();
      await confirm.getByRole('button', { name: /^approve \d+$/i }).click();

      await page.getByRole('button', { name: /^undo$/i }).click();
      await expect(page.getByText(/^undone: /i)).toBeVisible({ timeout: 10_000 });
    });

    test('export full version downloads every row', async ({ page }) => {
      await login(page);
      await page.getByRole('button', { name: /^forecast table$/i }).click();
      await expect(page.getByText(/export full version/i)).toBeVisible({ timeout: 20_000 });
      const [download] = await Promise.all([
        page.waitForEvent('download'),
        page.getByRole('button', { name: /^csv$/i }).last().click(),
      ]);
      const body = await (await download.createReadStream())?.toArray();
      const lines = Buffer.concat(body ?? []).toString('utf8').trim().split('\n');
      expect(lines[0]).toContain('Published value');
      expect(lines.length).toBeGreaterThan(12 * 3); // ≥ 12 lines × 3-month horizon + header
    });
  });
});
