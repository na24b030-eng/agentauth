import { expect, test } from '@playwright/test';

const prompt = 'Order my usual groceries under ₹900 for delivery tonight';

async function enterWorkspace(page: import('@playwright/test').Page) {
  await page.goto('/');
  const login = page.getByRole('button', { name: 'Enter test environment' });
  const workspace = page.getByRole('heading', { name: /Good evening/ });
  const delegation = page.getByRole('heading', { name: 'Delegation consent' });
  await expect(login.or(workspace).or(delegation)).toBeVisible({ timeout: 10_000 });
  const isLive = await login.isVisible();
  if (isLive) {
    await login.click();
    await expect(workspace.or(delegation)).toBeVisible({ timeout: 10_000 });
  }
  if (await delegation.isVisible()) {
    await page.getByRole('button', { name: 'Approve bounded authority' }).click();
    await expect(workspace).toBeVisible({ timeout: 10_000 });
  }
  return isLive;
}

test('honestly identifies live or preview mode and exposes one composer', async ({ page }) => {
  const isLive = await enterWorkspace(page);
  await expect(page.getByRole('heading', { name: /Good evening/ })).toBeVisible();
  await expect(page.getByText('AgentAuth', { exact: true }).first()).toBeVisible();
  await expect(page.getByRole('textbox', { name: 'Message your commerce agent' })).toHaveCount(1);
  if (isLive) {
    await expect(page.getByText('Live test services', { exact: true })).toBeVisible();
    await expect(page.getByRole('textbox', { name: 'Message your commerce agent' })).toHaveValue(
      prompt,
    );
  } else {
    await expect(page.getByText('Preview fixture', { exact: true })).toBeVisible();
    await expect(page.getByText(/labelled preview fixture/i)).toBeVisible();
  }
});

test('mobile layout has no horizontal overflow and primary controls remain reachable', async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await enterWorkspace(page);
  await expect(page.getByRole('heading', { name: /Good evening/ })).toBeVisible();
  const widths = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(widths.content).toBeLessThanOrEqual(widths.viewport + 1);
  await expect(page.getByRole('button', { name: 'Send message' })).toBeVisible();
});

test('unconfigured Razorpay mode is capability-gated', async ({ page }) => {
  const isLive = await enterWorkspace(page);
  test.skip(!isLive, 'Razorpay configuration is a live-service capability');
  const lab = page.getByRole('button', { name: 'Razorpay Test' });
  await expect(lab).toBeDisabled();
  await expect(page.getByText(/Razorpay Test Mode is not configured/)).toBeVisible();
});

test('@live autonomous simulator completes with one visible submitted prompt', async ({ page }) => {
  test.setTimeout(75_000);
  test.skip(process.env.E2E_LIVE_AGENT !== '1', 'Set E2E_LIVE_AGENT=1 for the real Gemini run');
  const isLive = await enterWorkspace(page);
  expect(isLive).toBe(true);
  await page.getByRole('button', { name: 'Send message' }).click();
  await expect(page.getByText('SIMULATED SETTLED', { exact: true }).first()).toBeVisible({
    timeout: 45_000,
  });
  await expect(page.locator('.user-message')).toHaveCount(1);
  await expect(page.locator('.user-message')).toHaveText(prompt);
  await expect(page.getByRole('textbox', { name: 'Message your commerce agent' })).toHaveValue('');
  await expect(
    page.locator('.decision').filter({ hasText: 'Authorization + reservation' }),
  ).toContainText('Checkout passed deterministic authorization');
});
