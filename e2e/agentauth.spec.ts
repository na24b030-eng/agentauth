import { expect, test } from "@playwright/test";

const prompt = "Order my usual groceries under ₹900 for delivery tonight";

async function enterWorkspace(page: import("@playwright/test").Page) {
  await page.goto("/");
  const login = page.getByRole("button", { name: "Enter test environment" });
  const workspace = page.getByRole("heading", { name: /Good evening/ });
  const delegation = page.getByRole("heading", { name: "Delegation consent" });
  await expect(login.or(workspace).or(delegation)).toBeVisible({
    timeout: 10_000,
  });
  const isLive = await login.isVisible();
  if (isLive) {
    await login.click();
    await expect(workspace.or(delegation)).toBeVisible({ timeout: 10_000 });
  }
  if (await delegation.isVisible()) {
    await page
      .getByRole("button", { name: "Approve bounded authority" })
      .click();
    await expect(workspace).toBeVisible({ timeout: 10_000 });
  }
  return isLive;
}

test("honestly identifies live or preview mode and exposes one composer", async ({
  page,
}) => {
  const isLive = await enterWorkspace(page);
  await expect(
    page.getByRole("heading", { name: /Good evening/ }),
  ).toBeVisible();
  await expect(
    page.getByText("AgentAuth", { exact: true }).first(),
  ).toBeVisible();
  await expect(
    page.getByRole("textbox", { name: "Message your commerce agent" }),
  ).toHaveCount(1);
  if (isLive) {
    await expect(
      page.getByText("Test services online", { exact: true }),
    ).toBeVisible();
    await expect(
      page.getByRole("textbox", { name: "Message your commerce agent" }),
    ).toHaveValue(prompt);
  } else {
    await expect(
      page.getByText("Preview fixture", { exact: true }),
    ).toBeVisible();
    await expect(page.getByText(/labelled preview fixture/i)).toBeVisible();
    await page.getByRole("button", { name: "Send message" }).click();
    await expect(
      page.getByRole("heading", { name: "Tonight’s basket" }),
    ).toBeVisible({ timeout: 5_000 });
    await expect(page.locator(".user-message")).toHaveCount(1);
  }
});

test("mobile layout has no horizontal overflow and primary controls remain reachable", async ({
  page,
}) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await enterWorkspace(page);
  await expect(
    page.getByRole("heading", { name: /Good evening/ }),
  ).toBeVisible();
  const widths = await page.evaluate(() => ({
    viewport: document.documentElement.clientWidth,
    content: document.documentElement.scrollWidth,
  }));
  expect(widths.content).toBeLessThanOrEqual(widths.viewport + 1);
  await expect(
    page.getByRole("button", { name: "Send message" }),
  ).toBeVisible();
});

test("sandbox execution is explicitly disclosed", async ({ page }) => {
  const isLive = await enterWorkspace(page);
  test.skip(!isLive, "requires the local AgentAuth services");
  await page.getByRole("button", { name: "AgentAuth Sandbox" }).click();
  const dialog = page.getByRole("dialog", { name: "AgentAuth Sandbox" });
  await expect(dialog).toBeVisible();
  await expect(dialog).toContainText("Enforced for real");
  await expect(dialog).toContainText("Simulated by design");
  await expect(dialog).toContainText("SIMULATED_SETTLED");
  await expect(
    page.getByText(/Deterministic settlement · no real money or personal KYC/),
  ).toBeVisible();
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await expect(dialog).toBeHidden();
});

test("every primary tab opens a distinct, purposeful workspace", async ({
  page,
}) => {
  await enterWorkspace(page);

  const destinations = [
    ["Trust Inspector", "Trust Inspector"],
    ["Delegations", "Delegation consent"],
    ["Developer", "Recovery lab"],
    ["Commerce", /Good evening/],
  ] as const;

  for (const [tab, heading] of destinations) {
    await page.getByRole("button", { name: tab, exact: true }).click();
    await expect(page.getByRole("heading", { name: heading })).toBeVisible();
    await expect(
      page.getByRole("button", { name: tab, exact: true }),
    ).toHaveAttribute("aria-current", "page");
  }
});

test("signed-evidence action opens an honest inspector and remains actionable", async ({
  page,
}) => {
  await enterWorkspace(page);
  await page
    .getByRole("button", { name: "Inspect the signed evidence" })
    .click();

  await expect(
    page.getByRole("heading", { name: "Trust Inspector" }),
  ).toBeVisible();
  await expect(
    page.getByText(/SIGNED CANONICAL REQUEST|CANONICAL SIGNING FORMAT/),
  ).toBeVisible();
  await expect(
    page.getByText(
      /Persisted checkout evidence|No signed evidence in preview mode|No checkout evidence in this session/,
    ),
  ).toBeVisible();

  await page.getByRole("button", { name: "Return to commerce" }).click();
  await expect(
    page.getByRole("heading", { name: /Good evening/ }),
  ).toBeVisible();
});

test("secondary controls expose a concrete action or prerequisite", async ({
  page,
}) => {
  await enterWorkspace(page);

  await page
    .getByRole("button", {
      name: "Find a high-protein basket for tomorrow morning",
    })
    .click();
  await expect(
    page.getByRole("textbox", { name: "Message your commerce agent" }),
  ).toHaveValue("Find a high-protein basket for tomorrow morning");

  await page.getByRole("button", { name: "Delegations", exact: true }).click();
  await expect(page.getByText("Per order", { exact: true })).toBeVisible();
  await expect(page.getByText("Cumulative", { exact: true })).toBeVisible();

  await page.getByRole("button", { name: "Developer", exact: true }).click();
  const recoveryControls = [
    /Provider-response recovery/,
    /Replay PoP nonce/,
    /Out-of-order webhook/,
    /Model timeout/,
    /Reset local demo/,
  ];
  for (const name of recoveryControls) {
    await expect(page.getByRole("button", { name })).toBeVisible();
  }
  await expect(page.locator(".developer-grid small")).toHaveCount(5);
});

test("@live autonomous simulator completes with one visible submitted prompt", async ({
  page,
}) => {
  test.setTimeout(75_000);
  test.skip(
    process.env.E2E_LIVE_AGENT !== "1",
    "Set E2E_LIVE_AGENT=1 for the real Gemini run",
  );
  const isLive = await enterWorkspace(page);
  expect(isLive).toBe(true);
  await page.getByRole("button", { name: "Send message" }).click();
  await expect(
    page.getByText("SIMULATED SETTLED", { exact: true }).first(),
  ).toBeVisible({
    timeout: 45_000,
  });
  await expect(page.locator(".user-message")).toHaveCount(1);
  await expect(page.locator(".user-message")).toHaveText(prompt);
  await expect(
    page.getByRole("textbox", { name: "Message your commerce agent" }),
  ).toHaveValue("");
  await expect(
    page
      .locator(".decision")
      .filter({ hasText: "Authorization + reservation" }),
  ).toContainText("Checkout passed deterministic authorization");
});
