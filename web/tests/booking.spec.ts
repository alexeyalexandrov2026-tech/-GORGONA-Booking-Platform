import { createServer } from "node:http";
import type { AddressInfo } from "node:net";
import { expect, test, type Page } from "@playwright/test";
import AxeBuilder from "@axe-core/playwright";
import { availabilitySchema, bootstrapSchema } from "../lib/contracts";

async function choose(page: Page, addon = false) {
  await page.goto("/book/");
  await expect(
    page.getByRole("heading", {
      name: "Your appointment, thoughtfully arranged.",
    }),
  ).toBeVisible();
  await page
    .getByLabel("Service and variant")
    .selectOption({ label: "FAKE FAKE_BASE · 60 min" });
  if (addon) await page.getByLabel(/FAKE FAKE_MASSAGE/).check();
  expect(
    (await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze())
      .violations,
  ).toEqual([]);
  await page.screenshot({
    path: `test-results/service-${page.viewportSize()?.width}.png`,
    fullPage: true,
  });
  await page.getByRole("button", { name: "Continue to times" }).click();
  const response = page.waitForResponse(
    (r) =>
      r.url().includes("/availability") &&
      r.request().postDataJSON().day === process.env.GBA_BROWSER_DAY,
  );
  await page.getByLabel("Date").fill(process.env.GBA_BROWSER_DAY ?? "");
  await expect(page.getByRole("button", { name: /^\d/ }).first()).toBeVisible();
  return availabilitySchema.parse(await (await response).json());
}

test("real catalog → add-on → any artist → details → review → PostgreSQL confirmation", async ({
  page,
}) => {
  await choose(page, true);
  await page.getByRole("button", { name: /^\d/ }).first().click();
  await page.getByRole("button", { name: "Continue to details" }).click();
  await expect(page.getByRole("complementary")).toContainText("75 minutes");
  expect(
    (await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze())
      .violations,
  ).toEqual([]);
  await page.getByLabel("Full name").fill("FAKE Browser Customer");
  await page.getByLabel("Email").fill("fake-browser@example.test");
  await page.getByLabel("Phone").fill("+1 555 010 4321");
  await page.getByLabel("I agree to the cancellation policy").check();
  await page.getByRole("button", { name: "Review appointment" }).click();
  await expect(
    page.getByRole("heading", { name: "One last look." }),
  ).toBeVisible();
  expect(
    (await new AxeBuilder({ page }).withTags(["wcag2a", "wcag2aa"]).analyze())
      .violations,
  ).toEqual([]);
  await page.getByRole("button", { name: "Confirm booking" }).click();
  await expect(
    page.getByRole("heading", { name: "You’re booked." }),
  ).toBeVisible();
  await expect(page.getByTestId("booking-reference")).toContainText(
    /[0-9a-f-]{36}/,
  );
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth <= innerWidth,
    ),
  ).toBe(true);
});

test("slot race recovers to a fresh time selection", async ({
  page,
  request,
}) => {
  const available = await choose(page);
  const slot = available.slots[0]!;
  await page.getByRole("button", { name: /^\d/ }).first().click();
  const bootstrap = bootstrapSchema.parse(
    await (await request.get("/v1/customer/bootstrap")).json(),
  );
  const stolen = await request.post("/v1/customer/holds", {
    data: {
      location_id: bootstrap.locations[0]!.id,
      variant_id: bootstrap.variants.find((v) => v.name === "FAKE FAKE_BASE")!
        .id,
      add_on_ids: [],
      resource_id: slot.resource_id,
      start_at: slot.start_at,
    },
    headers: {
      "Booking-Token": "a".repeat(43),
      "Idempotency-Key": crypto.randomUUID(),
    },
  });
  expect(stolen.status()).toBe(201);
  await page.getByRole("button", { name: "Continue to details" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "no longer available" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "A time just for you." }),
  ).toBeVisible();
});

test("unknown/non-live host shows a safe unavailable state", async ({
  browser,
}) => {
  const context = await browser.newContext();
  const page = await context.newPage();
  await page.goto(
    `${process.env.GBA_BROWSER_URL?.replace("127.0.0.1", "localhost")}/book/`,
  );
  await expect(
    page.getByRole("heading", { name: "Online booking is not available yet." }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Continue to times" }),
  ).toHaveCount(0);
  await context.close();
});

test("lost hold/confirmation responses retry safely with the same keys", async ({
  page,
}) => {
  await choose(page);
  await page
    .getByRole("combobox", { name: /^Artist/ })
    .selectOption({ label: "FAKE artist A2" });
  await expect(page.getByRole("button", { name: /^\d/ }).first()).toContainText(
    "FAKE artist A2",
  );
  await page.getByRole("button", { name: /^\d/ }).first().focus();
  await page.keyboard.press("Enter");
  await expect(
    page.getByRole("button", { name: /^\d/ }).first(),
  ).toHaveAttribute("aria-pressed", "true");
  const holdKeys: string[] = [];
  await page.route("**/v1/customer/holds", async (route) => {
    holdKeys.push(route.request().headers()["idempotency-key"]!);
    const response = await route.fetch(); // actual backend commits before response loss
    if (holdKeys.length === 1) await route.abort("failed");
    else await route.fulfill({ response });
  });
  await page.getByRole("button", { name: "Continue to details" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "request may have arrived" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Continue to details" }).click();
  await page.getByLabel("Full name").fill("FAKE Retry Guest");
  await page.getByLabel("Email").fill("fake-retry@example.test");
  await page.getByLabel("Phone").fill("+1 555 010 4321");
  await page.getByLabel("I agree to the cancellation policy").check();
  await page.getByRole("button", { name: "Review appointment" }).click();
  const confirmKeys: string[] = [];
  await page.route("**/v1/customer/bookings/*/confirm", async (route) => {
    confirmKeys.push(route.request().headers()["idempotency-key"]!);
    const response = await route.fetch();
    if (confirmKeys.length === 1) await route.abort("failed");
    else await route.fulfill({ response });
  });
  await page.getByRole("button", { name: "Confirm booking" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "request may have arrived" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Confirm booking" }).click();
  await expect(
    page.getByRole("heading", { name: "You’re booked." }),
  ).toBeVisible();
  expect(holdKeys).toHaveLength(2);
  expect(holdKeys[0]).toBe(holdKeys[1]);
  expect(confirmKeys).toHaveLength(2);
  expect(confirmKeys[0]).toBe(confirmKeys[1]);
});

test("availability network failure has a usable retry", async ({ page }) => {
  await page.goto("/book/");
  await page
    .getByLabel("Service and variant")
    .selectOption({ label: "FAKE FAKE_BASE · 60 min" });
  let failed = false;
  await page.route("**/v1/customer/availability", async (route) => {
    if (!failed) {
      failed = true;
      await route.abort("failed");
    } else await route.continue();
  });
  await page.getByRole("button", { name: "Continue to times" }).click();
  await expect(
    page.getByRole("button", { name: "Retry availability" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Retry availability" }).click();
  if (process.env.GBA_BROWSER_DAY) {
    await page.getByLabel("Date").fill(process.env.GBA_BROWSER_DAY);
  }
  await expect(page.getByRole("button", { name: /^\d/ }).first()).toBeVisible();
});

// Governed framing policy (ADR-0012), closing the M3 clickjacking finding. Embedders
// are real loopback servers on their own ports, so they are distinct origins and
// Chromium's Private Network Access rules do not mask the policy.
async function withEmbedder(
  port: number,
  booking: string,
  run: (origin: string) => Promise<void>,
) {
  const embedder = createServer((_, response) => {
    response.writeHead(200, { "Content-Type": "text/html; charset=utf-8" });
    response.end(
      `<!doctype html><title>Embedder</title><iframe title="embedded" src="${booking}" width="800" height="900"></iframe>`,
    );
  });
  await new Promise<void>((resolve) =>
    embedder.listen(port, "127.0.0.1", resolve),
  );
  try {
    const { port: bound } = embedder.address() as AddressInfo;
    const origin = `http://127.0.0.1:${bound}`;
    expect(origin).not.toBe(new URL(booking).origin);
    await run(origin);
  } finally {
    await new Promise((resolve) => embedder.close(resolve));
  }
}

test("framing: an unapproved origin is BLOCKED by frame-ancestors", async ({
  page,
  request,
  baseURL,
}) => {
  const booking = `${baseURL}/book/`;
  expect(
    (await request.get(booking)).headers()["content-security-policy"],
  ).toBe(
    `frame-ancestors 'self' http://127.0.0.1:${process.env.GBA_APPROVED_EMBED_PORT}`,
  );
  await withEmbedder(0, booking, async (origin) => {
    const refused = page.waitForEvent("console", (message) =>
      message.text().includes("frame-ancestors"),
    );
    await page.goto(`${origin}/`);
    await refused;
    await expect(
      page
        .frameLocator('iframe[title="embedded"]')
        .getByLabel("Service and variant"),
    ).toHaveCount(0);
  });
});

test("framing: the approved test origin is ALLOWED and top-level still works", async ({
  page,
  baseURL,
}) => {
  const booking = `${baseURL}/book/`;
  await withEmbedder(
    Number(process.env.GBA_APPROVED_EMBED_PORT),
    booking,
    async (origin) => {
      await page.goto(`${origin}/`);
      await expect(
        page
          .frameLocator('iframe[title="embedded"]')
          .getByLabel("Service and variant"),
      ).toBeVisible();
    },
  );
  await page.goto(booking);
  await expect(page.getByLabel("Service and variant")).toBeVisible();
});

test("expiry response returns to availability (contract fault injection)", async ({
  page,
}) => {
  await choose(page);
  await page.getByRole("button", { name: /^\d/ }).first().click();
  await page.getByRole("button", { name: "Continue to details" }).click();
  await page.getByLabel("Full name").fill("FAKE Expiry Guest");
  await page.getByLabel("Email").fill("fake-expiry@example.test");
  await page.getByLabel("Phone").fill("+1 555 010 4321");
  await page.getByLabel("I agree to the cancellation policy").check();
  await page.getByRole("button", { name: "Review appointment" }).click();
  // Real PostgreSQL expiry is covered by test_customer_api. Here, inject only
  // its documented response to exercise the browser's recovery branch.
  await page.route("**/v1/customer/bookings/*/confirm", (route) =>
    route.fulfill({
      status: 409,
      contentType: "application/json",
      body: JSON.stringify({
        error: {
          code: "HOLD_EXPIRED",
          message: "The hold expired; choose another time",
          request_id: "fake-expiry-contract",
        },
      }),
    }),
  );
  await page.getByRole("button", { name: "Confirm booking" }).click();
  await expect(
    page.getByRole("alert").filter({ hasText: "hold expired" }),
  ).toBeVisible();
  await expect(
    page.getByRole("heading", { name: "A time just for you." }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Continue to details" }),
  ).toBeDisabled();
});
