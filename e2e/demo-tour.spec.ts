import { expect, test } from '@playwright/test';

const prompt = 'Order my usual groceries under ₹900 for delivery tonight';

test('record the five-minute AgentAuth judging tour', async ({ page }) => {
  test.skip(process.env.E2E_RECORD_DEMO !== '1', 'Set E2E_RECORD_DEMO=1 to record the tour');
  test.setTimeout(420_000);
  page.setDefaultTimeout(15_000);

  await page.goto('/');
  const login = page.getByRole('button', { name: 'Enter test environment' });
  const workspace = page.getByRole('heading', { name: /Good evening/ });
  const delegation = page.getByRole('heading', { name: 'Delegation consent' });
  await expect(login.or(workspace).or(delegation)).toBeVisible({ timeout: 10_000 });
  if (await login.isVisible()) {
    await login.click();
    await expect(workspace.or(delegation)).toBeVisible({ timeout: 10_000 });
  }
  await page.waitForTimeout(15_000);

  await page.getByRole('button', { name: 'Developer' }).click();
  await expect(page.getByRole('heading', { name: 'Break it safely.' })).toBeVisible();
  const reset = page.getByRole('button', { name: /Reset local demo/ });
  if (await reset.isEnabled()) {
    await reset.click();
    await expect(page.getByText(/Local fictional state reset/)).toBeVisible();
  }
  await page.waitForTimeout(15_000);

  await page.getByRole('button', { name: 'Delegations' }).click();
  const approve = page.getByRole('button', { name: 'Approve bounded authority' });
  if (await approve.isVisible()) {
    await approve.click();
    await expect(workspace).toBeVisible({ timeout: 10_000 });
  }
  await page.getByRole('button', { name: 'Delegations' }).click();
  await expect(page.getByRole('heading', { name: 'Delegation consent' })).toBeVisible();
  await page.waitForTimeout(30_000);

  await page.getByRole('button', { name: 'Commerce' }).click();
  const composer = page.getByRole('textbox', { name: 'Message your commerce agent' });
  await composer.fill(prompt);
  await page.waitForTimeout(10_000);

  let settled = false;
  for (let attempt = 0; attempt < 3 && !settled; attempt += 1) {
    await page.getByRole('button', { name: 'Send message' }).click();
    const terminal = page.getByText('SIMULATED SETTLED', { exact: true }).first();
    const error = page.locator('.global-banner.error');
    await expect(terminal.or(error)).toBeVisible({ timeout: 40_000 });
    if (await terminal.isVisible()) {
      settled = true;
      break;
    }
    const message = await error.textContent();
    if (!message?.includes('MODEL_RATE_LIMITED')) {
      throw new Error(`Agent demo failed: ${message}`);
    }
    await error.getByRole('button').click();
    await composer.fill(prompt);
    await page.waitForTimeout(25_000);
  }
  expect(settled).toBe(true);
  await page.waitForTimeout(50_000);

  await page.getByRole('button', { name: 'Trust Inspector' }).click();
  await expect(page.getByRole('heading', { level: 1, name: 'Trust Inspector', exact: true })).toBeVisible();
  await page.waitForTimeout(65_000);

  await page.getByRole('button', { name: 'Developer' }).click();
  await page.waitForTimeout(15_000);
  const replay = page.getByRole('button', { name: /Replay PoP nonce/ });
  if (await replay.isEnabled()) {
    await replay.click();
    await expect(page.getByText(/identical nonce rejected/)).toBeVisible({ timeout: 10_000 });
  }
  await page.waitForTimeout(50_000);

  await page.getByRole('button', { name: 'Delegations' }).click();
  await page.waitForTimeout(30_000);
  await page.getByRole('button', { name: 'Commerce' }).click();
  await expect(page.getByText('SIMULATED SETTLED', { exact: true }).first()).toBeVisible();
  await page.waitForTimeout(25_000);
});
