import { createHash } from "node:crypto";
import { readFileSync } from "node:fs";
import { test, expect } from "@playwright/test";

// These are the real released inputs, not a synthetic fixture or a second calculator.
const sourceBytes = readFileSync(
  new URL("../public/data/release_config.json", import.meta.url),
);
const source = JSON.parse(sourceBytes);
const sha256 = (bytes) => createHash("sha256").update(bytes).digest("hex");
const expectedContent =
  process.env.SPM_E2E_EXPECT_CONTENT_SHA256 ?? source.forecast.contentSha256;
const expectedVersion =
  process.env.SPM_E2E_EXPECT_PACKAGE_VERSION ?? source.packageVersion;
const expectedStatus =
  process.env.SPM_E2E_EXPECT_PACKAGE_STATUS ??
  source.packageDistribution.status;
const observations = new WeakMap();
const menu = (year) => source.areaMenus[source.areaMenuByYear[year]];
const yearEntry = (year, scenario = source.forecast.defaultScenario) =>
  source.forecast.scenarios[scenario].years[year];
const geography = (year) =>
  source.geographies[yearEntry(year).geographyRef].geography_by_area;
const currency = (value) =>
  new Intl.NumberFormat("en-US", {
    style: "currency",
    currency: "USD",
    maximumFractionDigits: 0,
    minimumFractionDigits: 0,
  }).format(value);
const threshold = (page) =>
  page
    .getByTestId("primary-result")
    .getByText("SPM threshold", { exact: true })
    .locator("..")
    .locator("span")
    .nth(1);

async function selectArea(page, id) {
  await page.getByRole("combobox", { name: "Search SPM areas" }).fill(id);
  await page
    .getByRole("listbox", { name: "Matching SPM areas" })
    .locator(`[role="option"][data-value="${id}"]`)
    .click();
}

async function completeSetup(page) {
  await page.getByRole("combobox", { name: "Search SPM areas" }).fill("35620");
  await page
    .getByRole("listbox", { name: "Matching SPM areas" })
    .getByRole("option", { name: menu(2025)["35620"].name, exact: true })
    .click();
  await page.getByLabel("Adults", { exact: true }).fill("2");
  await page.getByLabel("Children", { exact: true }).fill("2");
  await page.getByRole("tab", { name: "Renter", exact: true }).click();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByRole("button", { name: "2025", exact: true }).click();
}

async function expectCompactLocationSearch(page) {
  const search = page.getByRole("combobox", { name: "Search SPM areas" });
  // Check the rendered input row, including its wrapper: a tall container or
  // duplicate visible label can look wrong even when the input itself is small.
  const bounds = await search.evaluate((input) => {
    const root = input.closest("[cmdk-root]");
    const { x, width, height } = root.getBoundingClientRect();
    return {
      x,
      width,
      height,
      fontSize: parseFloat(getComputedStyle(input).fontSize),
    };
  });
  expect(bounds.height).toBeGreaterThanOrEqual(44);
  expect(bounds.height).toBeLessThanOrEqual(56);
  expect(bounds.width).toBeLessThanOrEqual(600);
  expect(bounds.x).toBeGreaterThanOrEqual(16);
  expect(bounds.x + bounds.width).toBeLessThanOrEqual(
    page.viewportSize().width - 16,
  );
  expect(bounds.fontSize).toBeGreaterThanOrEqual(16);
}

test.beforeEach(async ({ page, baseURL }, testInfo) => {
  const errors = [];
  page.on("pageerror", (error) => errors.push(error.message));
  const surface = new URL(baseURL);
  const responsePromise = page.waitForResponse((response) => {
    const url = new URL(response.url());
    return (
      url.origin === surface.origin &&
      url.pathname.endsWith("/data/release_config.json")
    );
  });
  await page.goto("./", { waitUntil: "domcontentloaded" });
  const response = await responsePromise;
  expect(response.ok()).toBe(true);
  const dataURL = response.url();
  const allowedDataURLs = [new URL("data/release_config.json", baseURL).href];
  // Vercel's standalone root can serve the same subpath-built export as the
  // PolicyEngine zone. Check the actual app request, including that mount.
  if (process.env.SPM_E2E_BASE_URL && surface.pathname === "/") {
    allowedDataURLs.push(
      new URL("us/spm-calculator/data/release_config.json", baseURL).href,
    );
  }
  expect(allowedDataURLs).toContain(dataURL);
  const bytes = await response.body();
  const data = JSON.parse(bytes);
  expect(data.forecast.contentSha256).toBe(expectedContent);
  // An external publication may change distribution metadata, but must serve
  // the same reviewed scientific inputs as this checkout.
  expect(data.forecast).toEqual(source.forecast);
  expect(data.areaMenus).toEqual(source.areaMenus);
  expect(data.areaMenuByYear).toEqual(source.areaMenuByYear);
  expect(data.geographies).toEqual(source.geographies);
  expect(data.methodology).toEqual(source.methodology);
  expect(data.availableYears).toEqual(source.availableYears);
  expect(data.packageVersion).toBe(expectedVersion);
  expect(data.packageDistribution.status).toBe(expectedStatus);
  expect(data.packageDistribution.version).toBe(expectedVersion);
  if (expectedStatus === "published") {
    expect(data.packageDistribution.publishedVersion).toBe(expectedVersion);
  } else {
    expect(data.packageDistribution.publishedVersion).toBeNull();
  }
  if (!process.env.SPM_E2E_BASE_URL)
    expect(sha256(bytes)).toBe(sha256(sourceBytes));
  if (!testInfo.title.startsWith("guided setup")) {
    await completeSetup(page);
    await expect(page.getByTestId("primary-result")).toBeVisible();
    await expect(threshold(page)).toHaveText(/^\$[\d,]+$/);
  }
  await testInfo.attach("surface-identity.json", {
    contentType: "application/json",
    body: JSON.stringify(
      {
        requestedURL: baseURL,
        finalURL: page.url(),
        dataURL,
        releaseConfigSha256: sha256(bytes),
        contentSha256: data.forecast.contentSha256,
        auditArtifact: data.forecast.auditArtifact,
        packageDistribution: data.packageDistribution,
      },
      null,
      2,
    ),
  });
  observations.set(testInfo, { errors, dataURL });
});

test.afterEach(async ({}, testInfo) => {
  expect(
    observations.get(testInfo)?.errors ?? [],
    "Uncaught browser errors",
  ).toEqual([]);
});

test("guided setup retains answers and allows direct editing after results", async ({
  page,
}, testInfo) => {
  await expect(
    page.getByRole("heading", { name: "Where do you live?" }),
  ).toBeVisible();
  await expect(page.getByTestId("primary-result")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Continue", exact: true }),
  ).toHaveCount(0);
  await expect(
    page.getByLabel("SPM estimation area", { exact: true }),
  ).toHaveCount(0);
  await expect(page.getByLabel("Threshold year", { exact: true })).toHaveCount(
    0,
  );
  const search = page.getByRole("combobox", { name: "Search SPM areas" });
  await expect(search).toHaveValue("");
  await search.focus();
  await search.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Where do you live?" }),
  ).toBeVisible();
  await expect(page.getByTestId("primary-result")).toHaveCount(0);
  await search.click();
  const locationList = page.getByRole("listbox", { name: "Matching SPM areas" });
  await locationList.focus();
  await locationList.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Where do you live?" }),
  ).toBeVisible();
  await expect(locationList).toHaveCount(0);
  await expect(search).toBeFocused();
  await expectCompactLocationSearch(page);
  await page.screenshot({ path: testInfo.outputPath("guided-location.png") });
  await search.fill("san jose");
  await expect(page.getByRole("option").first()).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("guided-location-search.png"),
  });
  await expect(
    page.getByRole("heading", { name: "Where do you live?" }),
  ).toBeVisible();
  await expect(page.getByLabel("Adults", { exact: true })).toHaveCount(0);
  await page
    .getByRole("listbox", { name: "Matching SPM areas" })
    .getByRole("option", { name: menu(2025)["41940"].name, exact: true })
    .click();
  await expect(
    page.getByRole("heading", { name: "Who is in your household?" }),
  ).toBeFocused();
  await expect(page.getByLabel("Adults", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("Children", { exact: true })).toHaveValue("");
  await expect(page.locator('[role="tab"][aria-selected="true"]')).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("button", { name: "Continue", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("Adults", { exact: true }).fill("3");
  await page.getByLabel("Children", { exact: true }).fill("0");
  await expect(
    page.getByRole("button", { name: "Continue", exact: true }),
  ).toBeDisabled();
  await page.getByRole("tab", { name: "Mortgage", exact: true }).click();
  await expect(
    page.getByRole("button", { name: "Continue", exact: true }),
  ).toBeEnabled();
  await page.getByLabel("Children", { exact: true }).fill("");
  await expect(page.getByLabel("Children", { exact: true })).toHaveValue("");
  await expect(
    page.getByRole("button", { name: "Continue", exact: true }),
  ).toBeDisabled();
  await page.getByLabel("Children", { exact: true }).fill("0");
  await page.screenshot({
    path: testInfo.outputPath("guided-household-filled.png"),
  });
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Which year?" }),
  ).toBeFocused();
  await expect(page.getByLabel("Real spending", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("button", { name: "View thresholds", exact: true }),
  ).toHaveCount(0);
  await expect(page.getByTestId("primary-result")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "2025", exact: true }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "2036 (forecast)", exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("guided-year-unselected.png"),
  });
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(page.getByLabel("Adults", { exact: true })).toHaveValue("3");
  await expect(page.getByLabel("Children", { exact: true })).toHaveValue("0");
  await expect(
    page.getByRole("tab", { name: "Mortgage", exact: true }),
  ).toHaveAttribute("aria-selected", "true");
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Where do you live?" }),
  ).toBeVisible();
  await expect(search).toHaveValue(menu(2025)["41940"].name);
  await search.fill("no matching place xyz");
  await search.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Where do you live?" }),
  ).toBeVisible();
  // Keyboard selection must stop at the retained household step. A bubbling
  // implicit form submit must not skip it merely because its answers are valid.
  await search.fill("san jose");
  await search.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Who is in your household?" }),
  ).toBeVisible();
  await expect(page.getByRole("heading", { name: "Which year?" })).toHaveCount(
    0,
  );
  await expect(page.getByLabel("Adults", { exact: true })).toHaveValue("3");
  await page.getByRole("button", { name: "Back", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Where do you live?" }),
  ).toBeVisible();
  await selectArea(page, "41940");
  await expect(
    page.getByRole("heading", { name: "Who is in your household?" }),
  ).toBeVisible();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page
    .getByRole("button", { name: "2030 (forecast)", exact: true })
    .click();
  await expect(page.getByTestId("primary-result")).toContainText("San Jose");
  await expect(
    page.getByTestId("primary-result").getByRole("heading"),
  ).toBeFocused();
  await expect(page.getByLabel("Adults", { exact: true })).toHaveValue("3");
  await expect(page.getByLabel("Real spending", { exact: true })).toHaveValue(
    source.forecast.defaultScenario,
  );
  await page
    .getByLabel("Real spending", { exact: true })
    .selectOption("zero_real");
  await page.screenshot({ path: testInfo.outputPath("guided-results.png") });
  const yearControl = page.getByLabel("Threshold year", { exact: true });
  await yearControl.focus();
  await yearControl.selectOption("2025");
  await expect(yearControl).toBeFocused();
  await selectArea(page, "1002");
  await expect(search).toBeFocused();
  await expect(
    page.getByTestId("primary-result").getByRole("heading"),
  ).not.toBeFocused();
  await expect(page.getByLabel("Real spending", { exact: true })).toHaveCount(
    0,
  );
  await expect(
    page.getByRole("heading", { name: "Where do you live?" }),
  ).toHaveCount(0);
  await page.getByLabel("Threshold year", { exact: true }).selectOption("2030");
  await expect(page.getByLabel("Real spending", { exact: true })).toHaveValue(
    "zero_real",
  );
  const children = page.getByLabel("Children", { exact: true });
  const familySize = page
    .locator('[data-slot="card"]')
    .filter({ has: page.getByText("Family size", { exact: true }) });
  await children.fill("");
  await expect(children).toHaveValue("");
  await expect(threshold(page)).toHaveText("Unavailable");
  for (const field of ["Formula", "Normalized scale", "Household"]) {
    await expect(
      familySize.getByText(field, { exact: true }).locator(".."),
    ).toContainText("Unavailable");
  }
  await expect(familySize).not.toContainText("0.000");
  await children.fill("0");
  await expect(children).toHaveValue("0");
  await expect(threshold(page)).toHaveText(/^\$[\d,]+$/);
  await expect(familySize).not.toContainText("Unavailable");
  await expect(familySize).toContainText("3 adults, 0 children");
});

test("guided setup accepts keyboard area selection and explicit zero children", async ({
  page,
}, testInfo) => {
  await page.setViewportSize({ width: 390, height: 844 });
  const captureMobileStage = async (stage) => {
    expect(
      await page.evaluate(() =>
        Math.max(
          document.documentElement.scrollWidth,
          document.body.scrollWidth,
        ),
      ),
    ).toBeLessThanOrEqual(390);
    await page.screenshot({
      path: testInfo.outputPath(`guided-mobile-${stage}.png`),
      fullPage: true,
    });
  };
  await expect(
    page.getByRole("heading", { name: "Where do you live?" }),
  ).toBeVisible();
  await expectCompactLocationSearch(page);
  await captureMobileStage("location");
  const search = page.getByRole("combobox", { name: "Search SPM areas" });
  await search.fill("Alabama Nonmetro");
  await expect(
    page
      .getByRole("listbox", { name: "Matching SPM areas" })
      .getByRole("option"),
  ).toHaveCount(1);
  const optionBounds = await page.getByRole("option").boundingBox();
  expect(optionBounds.height).toBeGreaterThanOrEqual(44);
  await captureMobileStage("location-search");
  await search.press("ArrowDown");
  await expect(
    page.getByRole("heading", { name: "Where do you live?" }),
  ).toBeVisible();
  await search.press("Enter");
  await expect(
    page.getByRole("heading", { name: "Who is in your household?" }),
  ).toBeFocused();
  await captureMobileStage("household");
  await page.getByLabel("Adults", { exact: true }).fill("1");
  await page.getByLabel("Children", { exact: true }).fill("0");
  const renter = page.getByRole("tab", { name: "Renter", exact: true });
  await renter.focus();
  await expect(renter).toHaveAttribute("aria-selected", "false");
  await expect(
    page.getByRole("button", { name: "Continue", exact: true }),
  ).toBeDisabled();
  await renter.press("Space");
  await expect(renter).toHaveAttribute("aria-selected", "true");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Which year?" }),
  ).toBeVisible();
  await captureMobileStage("year");
  const back = page.getByRole("button", { name: "Back", exact: true });
  await back.scrollIntoViewIfNeeded();
  await back.click();
  const householdHeading = page.getByRole("heading", {
    name: "Who is in your household?",
  });
  await expect(householdHeading).toBeFocused();
  await expect(householdHeading).toBeInViewport();
  expect((await householdHeading.boundingBox()).y).toBeGreaterThanOrEqual(58);
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByRole("button", { name: "2025", exact: true }).click();
  await expect(page.getByTestId("primary-result")).toContainText(
    "Alabama Nonmetro",
  );
  await expect(page.getByLabel("Children", { exact: true })).toHaveValue("0");
  await expect(threshold(page)).toHaveText(/^\$[\d,]+$/);
  await expectCompactLocationSearch(page);
  await page.locator("[cmdk-root]").screenshot({
    path: testInfo.outputPath("results-mobile-area-selected.png"),
  });
  await search.fill("New York");
  await expect(page.getByRole("listbox", { name: "Matching SPM areas" })).toBeVisible();
  await page.locator("[cmdk-root]").screenshot({
    path: testInfo.outputPath("results-mobile-area-search.png"),
  });
  await search.press("Escape");
  await expect(search).toHaveValue("Alabama Nonmetro");
});

test("guided setup checks historical area membership after the year choice", async ({
  page,
}) => {
  const search = page.getByRole("combobox", { name: "Search SPM areas" });
  await search.fill("45001");
  await page
    .getByRole("listbox", { name: "Matching SPM areas" })
    .getByRole("option", { name: "South Carolina Metro", exact: true })
    .click();
  await page.getByLabel("Adults", { exact: true }).fill("2");
  await page.getByLabel("Children", { exact: true }).fill("2");
  await page.getByRole("tab", { name: "Renter", exact: true }).click();
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await page.getByRole("button", { name: "2023", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Where do you live?" }),
  ).toBeVisible();
  await expect(page.getByTestId("primary-result")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: menu(2025)["35620"].name, exact: true }),
  ).toHaveCount(0);
  await search.fill("45001");
  await expect(page.getByText("No SPM areas match your search.")).toBeVisible();
  await search.fill("Alabama Nonmetro");
  await page
    .getByRole("listbox", { name: "Matching SPM areas" })
    .getByRole("option", { name: "Alabama Nonmetro", exact: true })
    .click();
  await expect(page.getByLabel("Adults", { exact: true })).toHaveValue("2");
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  await expect(
    page.getByRole("heading", { name: "Which year?" }),
  ).toBeVisible();
  await expect(page.getByTestId("primary-result")).toHaveCount(0);
  await page.getByRole("button", { name: "2023", exact: true }).click();
  await expect(page.getByTestId("primary-result")).toContainText(
    "Alabama Nonmetro",
  );
});

test("one shared header, working mobile navigation, and app-owned provenance", async ({
  page,
}) => {
  const toggle = page.locator('button[aria-label="Toggle navigation"]');
  await expect(toggle).toHaveCount(1);
  const header = page
    .locator("main")
    .first()
    .locator("xpath=preceding-sibling::*[1]");
  await expect(
    header.locator('button[aria-label="Toggle navigation"]'),
  ).toHaveCount(1);
  const chrome = await page.locator("header, footer").evaluateAll((elements) =>
    elements
      .filter((element) => !element.closest('[data-testid="results-footnote"]'))
      .map((element) => element.textContent)
      .join("\n"),
  );
  expect(`${await header.textContent()}\n${chrome}`).not.toMatch(
    /SHA-256|rolling_ce_acs_v1|equivalence_scale|load_forecast/,
  );
  await expect(page.getByTestId("results-footnote")).toContainText(
    expectedContent,
  );
  await page.setViewportSize({ width: 800, height: 900 });
  await toggle.click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await expect(
    page.getByRole("dialog").getByRole("link").first(),
  ).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
});

test("area search selects by click and Enter and preserves selection on no match", async ({
  page,
}, testInfo) => {
  const search = page.getByRole("combobox", { name: "Search SPM areas" });
  await expect(search).toHaveCount(1);
  await expect(
    page.getByLabel("SPM estimation area", { exact: true }),
  ).toHaveCount(0);
  const matches = page.getByRole("listbox", { name: "Matching SPM areas" });
  const initialThreshold = await threshold(page).textContent();
  await page.locator("[cmdk-root] svg").click();
  await expect(search).toBeFocused();
  await expect(search).toHaveAttribute("aria-expanded", "true");
  await search.press("Enter");
  await expect(search).toHaveValue(menu(2025)["35620"].name);
  await expect(threshold(page)).toHaveText(initialThreshold);
  await search.press("ArrowDown");
  const firstArea = await matches.getByRole("option").first().textContent();
  await search.press("Enter");
  await expect(search).toHaveValue(firstArea);
  await search.click();
  await search.press("End");
  const lastArea = await matches.getByRole("option").last().textContent();
  await expect(matches.getByRole("option").last()).toHaveAttribute(
    "aria-selected", "true",
  );
  await search.press("Enter");
  await expect(search).toHaveValue(lastArea);
  for (const [key, index] of [
    ["ArrowDown", 1],
    ["Control+n", 1],
    ["Control+j", 1],
    ["Meta+ArrowDown", -1],
  ]) {
    await search.click();
    const options = matches.getByRole("option");
    const target = index === -1 ? options.last() : options.nth(index);
    const name = await target.textContent();
    await search.press(key);
    await expect(target).toHaveAttribute("aria-selected", "true");
    await search.press("Enter");
    await expect(search).toHaveValue(name);
  }
  await search.click();
  await matches.focus();
  await matches.press("End");
  await matches.press("Enter");
  await expect(search).toHaveValue(lastArea);
  await expect(search).toBeFocused();
  await search.pressSequentially("san jose");
  await expect(search).toHaveValue("san jose");
  await search.press("Escape");
  await search.click();
  // Click the list's padding, outside an option: focus must stay in the input
  // and Enter must not turn the automatic highlight into a committed choice.
  await matches.click({ position: { x: 1, y: 1 } });
  await expect(search).toBeFocused();
  await matches.hover({ position: { x: 1, y: 1 } });
  await page.mouse.wheel(0, 500);
  await expect.poll(() => matches.evaluate((list) => list.scrollTop))
    .toBeGreaterThan(0);
  const scrollbar = await matches.evaluate((list) => {
    const bounds = list.getBoundingClientRect();
    return {
      x: bounds.right - 8,
      y: bounds.top,
      width: list.offsetWidth - list.clientWidth,
    };
  });
  if (scrollbar.width > 4) {
    await matches.evaluate((list) => { list.scrollTop = 0; });
    await page.mouse.move(scrollbar.x, scrollbar.y + 10);
    await page.mouse.down();
    try {
      await page.mouse.move(scrollbar.x, scrollbar.y + 180, { steps: 5 });
    } finally {
      await page.mouse.up();
    }
    await expect.poll(() => matches.evaluate((list) => list.scrollTop))
      .toBeGreaterThan(0);
  }
  await search.press("Enter");
  await expect(search).toHaveValue(lastArea);
  await search.fill("san jose");
  await expect(matches.getByRole("option")).toHaveCount(1);
  await page.locator('[data-slot="command-input-wrapper"]').click({
    position: { x: 4, y: 24 },
  });
  await expect(search).toBeFocused();
  await expect(search).toHaveValue("san jose");
  await matches.focus();
  await search.focus();
  await expect(search).toHaveValue("san jose");
  await matches
    .getByRole("option", { name: menu(2025)["41940"].name, exact: true })
    .click();
  await expect(search).toHaveValue(menu(2025)["41940"].name);
  await expect(
    page.getByTestId("primary-result").getByRole("heading"),
  ).toHaveText(menu(2025)["41940"].name);
  await expect(matches).toHaveCount(0);
  await expect(search).toHaveAttribute("aria-expanded", "false");
  await page.locator("[cmdk-root]").screenshot({
    path: testInfo.outputPath("results-area-selected.png"),
  });
  // The same field reopens after selection, even while it retains focus.
  await search.click();
  await expect(matches.getByRole("option")).toHaveCount(
    Object.keys(menu(2025)).length,
  );
  await search.fill("no matching place xyz");
  await search.press("Escape");
  await expect(search).toHaveValue(menu(2025)["41940"].name);
  await expect(matches).toHaveCount(0);
  await search.press("ArrowDown");
  await expect(matches).toBeVisible();
  await search.fill("Alabama");
  await page.locator("[cmdk-root]").screenshot({
    path: testInfo.outputPath("results-area-search.png"),
  });
  await page.getByLabel("Adults", { exact: true }).focus();
  await expect(search).toHaveValue(menu(2025)["41940"].name);
  await expect(matches).toHaveCount(0);
  await search.fill("1002");
  await expect(matches.getByRole("option")).toHaveCount(5);
  await expect(matches.getByRole("option").first()).toHaveText(
    menu(2025)["1002"].name,
  );
  await search.press("Enter");
  await expect(search).toHaveValue(menu(2025)["1002"].name);
  await expect(
    page.getByTestId("primary-result").getByRole("heading"),
  ).toHaveText(menu(2025)["1002"].name);
  // Real keystrokes after committing a selection must replace its displayed
  // name with a fresh search, even though the input never lost focus.
  await search.pressSequentially("san jose");
  await expect(search).toHaveValue("san jose");
  await expect(matches.getByRole("option")).toHaveCount(1);
  await expect(matches.getByRole("option")).toHaveText(menu(2025)["41940"].name);
  await search.press("Escape");
  await expect(search).toHaveValue(menu(2025)["1002"].name);
  const selectedThreshold = await threshold(page).textContent();
  await search.fill("no matching place xyz");
  await expect(page.getByText("No SPM areas match your search.")).toBeVisible();
  await expect(matches.getByRole("option")).toHaveCount(0);
  await search.press("Enter");
  await expect(
    page.getByTestId("primary-result").getByRole("heading"),
  ).toHaveText(menu(2025)["1002"].name);
  await expect(threshold(page)).toHaveText(selectedThreshold);
});

test("annual SPM-area menus and published versus modeled geography are explicit", async ({
  page,
}) => {
  const year = page.getByLabel("Threshold year", { exact: true });
  await expect(page.getByLabel("Geography type", { exact: true })).toHaveCount(
    0,
  );
  for (const value of [2022, 2024, 2025, 2036]) {
    await year.selectOption(String(value));
    await page.getByRole("combobox", { name: "Search SPM areas" }).click();
    const actual = await page
      .getByRole("listbox", { name: "Matching SPM areas" })
      .getByRole("option")
      .evaluateAll((options) =>
        Object.fromEntries(
          options.map((option) => [option.dataset.value, option.textContent]),
        ),
      );
    await page
      .getByRole("combobox", { name: "Search SPM areas" })
      .press("Escape");
    expect(actual).toEqual(
      Object.fromEntries(
        Object.entries(menu(value)).map(([id, entry]) => [id, entry.name]),
      ),
    );
    expect(
      Object.values(menu(value)).every((entry) =>
        [
          "msa",
          "state_nonmetro",
          "state_metro_residual",
          "modeled_residual_metro",
        ].includes(entry.area_type),
      ),
    ).toBe(true);
  }
  await year.selectOption("2024");
  const official = Object.keys(geography(2024)).find(
    (id) =>
      menu(2024)[id]?.area_type === "msa" &&
      geography(2024)[id].status === "published_anchor",
  );
  const modeled = Object.keys(geography(2024)).find(
    (id) => menu(2024)[id]?.area_type === "modeled_residual_metro",
  );
  expect(official).toBeTruthy();
  expect(modeled).toBeTruthy();
  await selectArea(page, official);
  await expect(page.getByTestId("primary-result")).toContainText(
    "2024 published geography anchor",
  );
  await expect(page.getByTestId("area-provenance")).toContainText(
    "This area has a published Census geography anchor.",
  );
  await selectArea(page, modeled);
  await expect(page.getByTestId("primary-result")).toContainText(
    "Modeled geography",
  );
  await expect(page.getByTestId("area-provenance")).toContainText(
    "This residual group has no published Census geography anchor.",
  );
  await selectArea(page, official);
  await year.selectOption("2025");
  await expect(
    page.getByText("Published by BLS", { exact: true }),
  ).toBeVisible();
  await expect(page.getByTestId("primary-result")).toContainText(
    "Modeled geography",
  );
  await expect(page.getByTestId("primary-result")).not.toContainText(
    "Research forecast",
  );
  await expect(page.getByTestId("forecast-disclaimer")).toContainText(
    "The 2025 geography is modeled",
  );
});

test("future years, real spending, and household controls update real results", async ({
  page,
}) => {
  const year = page.getByLabel("Threshold year", { exact: true });
  const factor = page
    .getByTestId("primary-result")
    .getByText("Location factor", { exact: true })
    .locator("..")
    .locator("span")
    .nth(1);
  const factor2025 = await factor.textContent();
  for (let value = 2026; value <= 2036; value++) {
    await expect(
      year.getByRole("option", { name: `${value} (forecast)`, exact: true }),
    ).toHaveCount(1);
    await year.selectOption(String(value));
    await expect(page.getByTestId("primary-result")).toContainText(
      "Research forecast",
    );
    await expect(page.getByTestId("source-windows")).toContainText(
      `${value} source windows`,
    );
    await expect(threshold(page)).toHaveText(/^\$[\d,]+$/);
  }
  await expect(factor).not.toHaveText(factor2025);
  await page.getByTestId("threshold-history-values").locator("summary").click();
  const row = page
    .getByRole("region", { name: "Year-by-year thresholds" })
    .getByRole("row")
    .filter({ has: page.getByRole("cell", { name: "2036", exact: true }) });
  const scenario = page.getByLabel("Real spending", { exact: true });
  await scenario.selectOption("ce_trend");
  await expect(row.getByRole("cell").nth(1)).toHaveText(
    currency(yearEntry(2036, "ce_trend").thresholds.renter),
  );
  const trendThreshold = await threshold(page).textContent();
  await scenario.selectOption("zero_real");
  await expect(row.getByRole("cell").nth(1)).toHaveText(
    currency(yearEntry(2036, "zero_real").thresholds.renter),
  );
  await expect(threshold(page)).not.toHaveText(trendThreshold);
  await expect(page.getByTestId("year-by-year-card")).toContainText(
    source.forecast.scenarios.zero_real.label,
  );
  const zeroThreshold = await threshold(page).textContent();
  await page.getByLabel("Adults", { exact: true }).fill("3");
  await expect(threshold(page)).not.toHaveText(zeroThreshold);
  await expect(threshold(page)).toHaveText(/^\$[\d,]+$/);
  const familyThreshold = await threshold(page).textContent();
  const noMortgage = page.getByRole("tab", {
    name: "No mortgage",
    exact: true,
  });
  await noMortgage.click();
  await expect(noMortgage).toHaveAttribute("aria-selected", "true");
  await expect(threshold(page)).not.toHaveText(familyThreshold);
  await expect(page.getByTestId("primary-result")).toContainText(
    "Owner without mortgage",
  );
  await expect(page.getByTestId("forecast-provenance")).toContainText(
    expectedContent,
  );
});

test("year chart exposes the breakdown by pointer, keyboard and touch", async ({
  page,
}, testInfo) => {
  const chart = page.getByTestId("threshold-history-chart");
  await expect(chart).toBeVisible();
  await expect(page.getByTestId("threshold-history-segment")).toHaveCount(14);
  await expect(
    page.getByTestId("threshold-history-values"),
  ).not.toHaveAttribute("open");
  const point = page.getByTestId("threshold-year-2026");
  await point.hover();
  const tooltip = page.getByRole("tooltip");
  await expect(tooltip).toContainText("2026");
  await expect(tooltip).toContainText("National base (2 adults, 2 children)");
  await expect(tooltip).toContainText(
    currency(yearEntry(2026).thresholds.renter),
  );
  await expect(tooltip).toContainText("Household scale");
  await expect(tooltip).toContainText("Location factor (GEOADJ)");
  await expect(tooltip).toContainText("housing share");
  await expect(tooltip).toContainText("rent index");
  await chart.scrollIntoViewIfNeeded();
  await point.hover();
  const header = page
    .locator("main")
    .first()
    .locator("xpath=preceding-sibling::*[1]");
  const expectVisibleBreakdown = async () => {
    const headerBox = await header.boundingBox();
    await expect
      .poll(async () => {
        const box = await tooltip.boundingBox();
        return box?.y ?? -1;
      })
      .toBeGreaterThanOrEqual(headerBox.y + headerBox.height);
    const box = await tooltip.boundingBox();
    expect(box.y + box.height).toBeLessThanOrEqual(page.viewportSize().height);
  };
  await expectVisibleBreakdown();
  // Keep the year hit target visible while scrolling the chart top under the
  // shared header. The complete breakdown must remain readable.
  await page.evaluate(() => window.scrollBy(0, 40));
  await expectVisibleBreakdown();
  await page.screenshot({ path: testInfo.outputPath("chart-desktop.png") });
  await point.focus();
  await page.keyboard.press("ArrowRight");
  await expect(page.getByTestId("threshold-year-2027")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(page.getByLabel("Threshold year", { exact: true })).toHaveValue(
    "2027",
  );
  await page.keyboard.press("Escape");
  await expect(tooltip).toHaveCount(0);
  await page.setViewportSize({ width: 390, height: 844 });
  await page.getByTestId("threshold-year-2024").click();
  await expect(page.getByLabel("Threshold year", { exact: true })).toHaveValue(
    "2024",
  );
  await expect(page.getByRole("tooltip")).toContainText("2024");
  await chart.scrollIntoViewIfNeeded();
  await page.screenshot({ path: testInfo.outputPath("chart-mobile.png") });
  await expect(page.getByLabel("Real spending", { exact: true })).toHaveCount(
    0,
  );
});

test("canonical download and package provenance match the served calculation", async ({
  page,
}, testInfo) => {
  const artifact = source.forecast.auditArtifact;
  const artifactURL = new URL(
    artifact.url.replace(/^\/data\//, ""),
    observations.get(testInfo).dataURL,
  ).href;
  const link = page.getByRole("link", {
    name: "Download full canonical audit data (JSON)",
  });
  expect(await link.evaluate((element) => element.href)).toBe(artifactURL);
  const downloadPromise = page.waitForEvent("download");
  await link.click();
  const download = await downloadPromise;
  expect(await download.failure()).toBeNull();
  expect(download.suggestedFilename()).toBe(artifact.name);
  const bytes = readFileSync(await download.path());
  expect(bytes.length).toBe(artifact.bytes);
  expect(sha256(bytes)).toBe(artifact.sha256);
  const canonical = JSON.parse(bytes);
  expect(canonical.content_sha256).toBe(expectedContent);
  expect(canonical.base_release_sha256).toBe(source.forecast.baseReleaseSha256);
  expect(canonical.assumption_sha256).toBe(source.forecast.assumptionSha256);
  await testInfo.attach("canonical-download.json", {
    contentType: "application/json",
    body: JSON.stringify(
      {
        url: artifactURL,
        bytes: bytes.length,
        sha256: sha256(bytes),
        contentSha256: canonical.content_sha256,
      },
      null,
      2,
    ),
  });
  const footnote = page.getByTestId("results-footnote");
  if (expectedStatus === "published") {
    await expect(footnote).toContainText(
      `Published package: spm-calculator ${expectedVersion}.`,
    );
    await expect(
      footnote.getByRole("link", {
        name: `spm-calculator ${expectedVersion}`,
        exact: true,
      }),
    ).toHaveAttribute(
      "href",
      `https://pypi.org/project/spm-calculator/${expectedVersion}/`,
    );
    await expect(footnote).not.toContainText(
      "Publication on PyPI is not confirmed",
    );
  } else {
    await expect(footnote).toContainText(
      `Local build / development preview: spm-calculator ${expectedVersion}.`,
    );
    await expect(footnote).toContainText(
      "Publication on PyPI is not confirmed for this build.",
    );
  }
});

for (const failure of ["failed", "stalled"]) {
  test(`calculator recovers from a ${failure} data request using the real server`, async ({
    page,
  }, testInfo) => {
    if (failure === "stalled") test.setTimeout(90_000);
    const { dataURL } = observations.get(testInfo);
    let intercepted = 0;
    await page.route(
      dataURL,
      async (route) => {
        intercepted++;
        if (failure === "failed") await route.abort("failed");
        // A stalled request gets no response. The production loader must abort
        // it after its real 30-second timeout; the retry is not intercepted.
      },
      { times: 1 },
    );
    await page.reload({ waitUntil: "domcontentloaded" });
    if (failure === "stalled") {
      await expect(page.getByRole("status")).toHaveText(
        "Loading thresholds and geographic inputs…",
      );
    }
    await expect(
      page
        .getByRole("alert")
        .filter({ hasText: "We couldn’t load the calculator data" }),
    ).toBeVisible({ timeout: 35_000 });
    expect(intercepted).toBe(1);
    await expect(page.getByTestId("primary-result")).toHaveCount(0);
    const recovered = page.waitForResponse(
      (response) => response.url() === dataURL,
    );
    await page.getByRole("button", { name: "Try again", exact: true }).click();
    const response = await recovered;
    expect(response.ok()).toBe(true);
    const data = await response.json();
    expect(data.forecast).toEqual(source.forecast);
    expect(data.geographies).toEqual(source.geographies);
    await completeSetup(page);
    await expect(page.getByTestId("primary-result")).toBeVisible();
    await expect(threshold(page)).toHaveText(/^\$[\d,]+$/);
    await expect(
      page.getByRole("button", { name: "Try again", exact: true }),
    ).toHaveCount(0);
  });
}

test("an unavailable area stays explicit until the user selects a valid area", async ({
  page,
}) => {
  const year = page.getByLabel("Threshold year", { exact: true });
  await year.selectOption("2022");
  await selectArea(page, "45001");
  await expect(threshold(page)).toHaveText(/^\$[\d,]+$/);
  await year.selectOption("2023");
  const search = page.getByRole("combobox", { name: "Search SPM areas" });
  await expect(search).toHaveValue("");
  await expect(search).toHaveAttribute(
    "placeholder",
    "Selected area unavailable in 2023",
  );
  await search.fill("45001");
  await expect(
    page.getByRole("option", { name: menu(2022)["45001"].name, exact: true }),
  ).toHaveCount(0);
  await search.press("Escape");
  await expect(threshold(page)).toHaveText("Unavailable");
  await selectArea(page, "modeled_residual_metro:45");
  await expect(threshold(page)).toHaveText(/^\$[\d,]+$/);
  await expect(
    page.getByTestId("primary-result").getByRole("heading"),
  ).toHaveText(menu(2023)["modeled_residual_metro:45"].name);
});

test("Massachusetts and Sumter series breaks remain visible in later years", async ({
  page,
}) => {
  const year = page.getByLabel("Threshold year", { exact: true });
  const warning = page.getByTestId("historical-series-break-warning");
  for (const id of ["25002", "modeled_residual_metro:45"]) {
    await year.selectOption("2023");
    await selectArea(page, id);
    const disclosure = geography(2023)[id].series_breaks[0];
    await expect(warning).toContainText(
      `${disclosure.from_year}→${disclosure.to_year}: ${disclosure.interpretation}`,
    );
    await year.selectOption("2036");
    await expect(warning).toContainText(disclosure.interpretation);
  }
  await selectArea(page, "41940");
  await expect(warning).toHaveCount(0);
});

test("rental support and topcoding diagnostics follow the actual area and year", async ({
  page,
}) => {
  const year = page.getByLabel("Threshold year", { exact: true });
  const diagnostics =
    source.geographies[yearEntry(2036).geographyRef].median_diagnostics;
  const thin = Object.keys(diagnostics).find(
    (id) => diagnostics[id].thin_support,
  );
  const topcoded = Object.keys(diagnostics).find(
    (id) =>
      !diagnostics[id].thin_support &&
      diagnostics[id].rent_index_topcode_warning,
  );
  expect(thin).toBeTruthy();
  expect(topcoded).toBeTruthy();
  expect(menu(2036)[thin]).toBeTruthy();
  expect(menu(2036)[topcoded]).toBeTruthy();
  await year.selectOption("2036");
  const warning = page
    .getByTestId("results-footnote")
    .getByTestId("median-diagnostics-note");
  await expect(
    page.getByTestId("primary-result").getByTestId("median-diagnostics-note"),
  ).toHaveCount(0);
  for (const id of [thin, topcoded]) {
    await selectArea(page, id);
    const entry = diagnostics[id];
    if (!(await warning.evaluate((element) => element.open)))
      await warning.locator("summary").click();
    await expect(warning).toContainText(
      entry.thin_support ? "Thin rental support" : "Rental topcoding",
    );
    await expect(warning).toContainText(
      `${entry.unique_records.toLocaleString("en-US")} unique records`,
    );
    await expect(warning).toContainText(
      `Kish effective count ${entry.kish_effective_count.toFixed(1)}`,
    );
    await expect(warning).toContainText(
      `${(entry.topcoded_weight_share * 100).toFixed(1)}% topcoded weight`,
    );
    await expect(warning).toContainText(
      "Repeated future cohorts do not add independent observations",
    );
    await expect(warning).toContainText(
      "not a survey-design effective sample size",
    );
    if (entry.rent_index_topcode_warning)
      await expect(warning).toContainText("including its vintage bridge");
    await expect(threshold(page)).toHaveText(/^\$[\d,]+$/);
  }
  await year.selectOption("2024");
  await expect(warning).toHaveCount(0);
  await expect(threshold(page)).toHaveText(/^\$[\d,]+$/);
});

test.describe("touch area selection", () => {
  test.use({
    hasTouch: true,
    isMobile: true,
    viewport: { width: 390, height: 844 },
  });

  test("guided setup accepts touch selection and lets results change area", async ({ page }, testInfo) => {
    const search = page.getByRole("combobox", { name: "Search SPM areas" });
    await page.locator("[cmdk-root] svg").tap();
    await expect(search).toBeFocused();
    await expect(search).toHaveAttribute("aria-expanded", "true");
    await search.fill("san jose");
    await page.getByRole("option", {
      name: menu(2025)["41940"].name, exact: true,
    }).tap();
    await expect(page.getByRole("heading", {
      name: "Who is in your household?",
    })).toBeVisible();
    await page.getByLabel("Adults", { exact: true }).fill("2");
    await page.getByLabel("Children", { exact: true }).fill("0");
    await page.getByRole("tab", { name: "Renter", exact: true }).tap();
    await page.getByRole("button", { name: "Continue", exact: true }).tap();
    await page.getByRole("button", { name: "2025", exact: true }).tap();
    await expect(page.getByTestId("primary-result")).toContainText("San Jose");
    await search.tap();
    await search.fill("35620");
    await page.getByRole("option", {
      name: menu(2025)["35620"].name, exact: true,
    }).tap();
    await expect(search).toHaveValue(menu(2025)["35620"].name);
    await expect(page.getByTestId("primary-result").getByRole("heading"))
      .toHaveText(menu(2025)["35620"].name);
    await expect(threshold(page)).toHaveText(/^\$[\d,]+$/);
    await page.locator("[cmdk-root]").screenshot({
      path: testInfo.outputPath("touch-results-area-selected.png"),
    });
  });
});
