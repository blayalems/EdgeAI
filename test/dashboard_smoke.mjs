#!/usr/bin/env node
/* End-to-end browser contracts for canonical and deterministic dashboards. */

import assert from "node:assert/strict";
import { createReadStream, existsSync, statSync } from "node:fs";
import { createServer } from "node:http";
import { extname, resolve, sep } from "node:path";
import { pathToFileURL } from "node:url";
import { chromium } from "playwright";


function args(argv) {
  const values = { root: resolve("."), standalone: resolve("dist/BananaGuard-Standalone.html") };
  for (let i = 0; i < argv.length; i += 1) {
    if (argv[i] === "--root") values.root = resolve(argv[++i]);
    else if (argv[i] === "--standalone") values.standalone = resolve(argv[++i]);
    else throw new Error(`unknown argument: ${argv[i]}`);
  }
  return values;
}


const MIME = {
  ".css": "text/css; charset=utf-8",
  ".html": "text/html; charset=utf-8",
  ".js": "text/javascript; charset=utf-8",
  ".json": "application/json; charset=utf-8",
  ".txt": "text/plain; charset=utf-8",
  ".woff2": "font/woff2",
};

const DEMO_SENTINELS = [
  "North Ridge",
  "Creekside",
  "Highland",
  "River Flat",
  "South Gate",
  "Simulated spray cycle",
  "synthetic confidence",
  "demo battery",
];
const canonicalRuntimeErrors = [];
const expectedFixtureConsoleErrors = [];


function collectCanonicalRuntimeErrors(page) {
  page.on("pageerror", (error) => {
    canonicalRuntimeErrors.push(`pageerror @ ${page.url()}: ${error.message}`);
  });
  page.on("console", (message) => {
    if (message.type() !== "error") return;
    const location = message.location();
    const sourceUrl = location.url || page.url();
    const text = message.text();
    let pathname = "";
    try { pathname = new URL(sourceUrl).pathname; } catch (_error) {}
    const expectedFixtureFailure = text.includes("Failed to load resource") && (
      (pathname === "/api/meta" && text.includes("404"))
      || ((pathname === "/api/logs" || pathname === "/api/history") && text.includes("500"))
    );
    const formatted = `console.error @ ${sourceUrl}: ${text}`;
    if (expectedFixtureFailure) expectedFixtureConsoleErrors.push(formatted);
    else canonicalRuntimeErrors.push(formatted);
  });
}


function baseMeta(overrides = {}) {
  const meta = {
    api_version: "1",
    backend_profile: "field",
    profile: "field",
    read_only: true,
    field_ingest_configured: true,
    field_ready: true,
    stale_after_seconds: 900,
    supported_payload_versions: [1],
    capabilities: {
      telemetry: true,
      history: true,
      export: true,
      remote_commands: false,
      simulator_ingest: false,
      node_registry: true,
    },
    counts: { field: 0, simulator: 0, legacy_unknown: 0, uplinks: 0, nodes: 0, registered_nodes: 0 },
    server_time: new Date().toISOString(),
  };
  const merged = { ...meta, ...overrides };
  if (overrides.capabilities) merged.capabilities = { ...meta.capabilities, ...overrides.capabilities };
  return merged;
}


function isoAgo(seconds) {
  return new Date(Date.now() - seconds * 1000).toISOString();
}


function fieldNode(overrides = {}) {
  return {
    id: 41,
    device_id: "field-node-007",
    display_name: "Verified Plot Alpha",
    block: "Zone 7",
    registered: true,
    registry_enabled: true,
    latitude: 7.0722,
    longitude: 125.6131,
    n_pest: 23,
    soil_safe: true,
    soil_fault: false,
    camera_fault: false,
    infer_ready: true,
    soil_vwc_pct: 41,
    batt_mv: 3970,
    batt_pct: 87.6,
    sprays_today: 4,
    action: "LOG",
    action_status: "reported",
    status: "watch",
    treatment_held: false,
    lockout_active: false,
    safety_violation: false,
    rssi: -91,
    snr: 8.5,
    sf: 9,
    fcnt: 321,
    eil_threshold: 15,
    window_minutes: 30,
    received_at: isoAgo(12),
    ingested_at: isoAgo(4),
    source_time: isoAgo(12),
    source_age_seconds: 12,
    ingestion_age_seconds: 4,
    source_kind: "field",
    payload_version: 1,
    application_id: "bananaguard-production",
    online: true,
    ...overrides,
  };
}


function createFixture(root) {
  const state = {
    reset(overrides = {}) {
      this.meta = overrides.meta || baseMeta();
      this.metaStatus = overrides.metaStatus || 200;
      this.nodes = overrides.nodes || [];
      this.logs = overrides.logs || [];
      this.history = overrides.history || [];
      this.delayMetaMs = overrides.delayMetaMs || 0;
      this.delayNodesMs = overrides.delayNodesMs || 0;
      this.delayLogsMs = overrides.delayLogsMs || 0;
      this.delayHistoryMs = overrides.delayHistoryMs || 0;
      this.logsStatus = overrides.logsStatus ?? 200;
      this.historyStatus = overrides.historyStatus ?? 200;
      this.requests = [];
      this.abortedRequests = [];
    },
  };
  state.reset();

  const server = createServer((request, response) => {
    const url = new URL(request.url || "/", "http://127.0.0.1");
    if (url.pathname.startsWith("/api/")) {
      const requestKey = url.pathname + url.search;
      state.requests.push(requestKey);
      response.setHeader("Content-Type", "application/json; charset=utf-8");
      let payload;
      let status = 200;
      let delay = 0;
      if (url.pathname === "/api/meta") {
        payload = state.meta;
        status = state.metaStatus;
        delay = state.delayMetaMs;
      }
      else if (url.pathname === "/api/nodes") {
        payload = state.nodes;
        delay = state.delayNodesMs;
      } else if (url.pathname === "/api/logs") {
        payload = state.logs;
        status = state.logsStatus;
        delay = state.delayLogsMs;
      }
      else if (url.pathname === "/api/history") {
        payload = state.history;
        status = state.historyStatus;
        delay = state.delayHistoryMs;
      }
      else {
        status = 404;
        payload = { error: "fixture route is not implemented" };
      }
      let finished = false;
      let timer = null;
      const finish = () => {
        if (response.destroyed || response.writableEnded) return;
        finished = true;
        response.statusCode = status;
        response.end(JSON.stringify(payload));
      };
      response.on("close", () => {
        if (!finished) {
          if (timer) clearTimeout(timer);
          state.abortedRequests.push(requestKey);
        }
      });
      if (delay > 0) timer = setTimeout(finish, delay);
      else finish();
      return;
    }

    const rel = decodeURIComponent(url.pathname === "/" ? "/index.html" : url.pathname).replace(/^\/+/, "");
    const file = resolve(root, rel);
    if (!file.startsWith(root + sep) || !existsSync(file) || !statSync(file).isFile()) {
      response.statusCode = 404;
      response.end("not found");
      return;
    }
    response.setHeader("Content-Type", MIME[extname(file).toLowerCase()] || "application/octet-stream");
    createReadStream(file).pipe(response);
  });
  return { server, state };
}


async function listen(server) {
  await new Promise((resolveListen, reject) => {
    server.once("error", reject);
    server.listen(0, "127.0.0.1", resolveListen);
  });
  return server.address().port;
}


async function eventually(predicate, message, timeoutMs = 5000) {
  const deadline = Date.now() + timeoutMs;
  while (Date.now() < deadline) {
    if (predicate()) return;
    await new Promise((resolveWait) => setTimeout(resolveWait, 40));
  }
  assert.fail(message);
}


async function waitForApp(page) {
  await page.locator("#dc-root").waitFor({ state: "attached", timeout: 15000 });
  await page.waitForFunction(() => document.body.innerText.includes("BananaGuard"), null, { timeout: 15000 });
}


async function openDemoPage(browser, url, viewport = { width: 1280, height: 900 }) {
  const context = await browser.newContext({ viewport });
  await context.addInitScript(() => {
    try {
      localStorage.setItem("bg_data_mode", "demo");
      localStorage.removeItem("bg_actual_cache_v1");
    } catch (_error) {}
  });
  const page = await context.newPage();
  collectCanonicalRuntimeErrors(page);
  await page.goto(url, { waitUntil: "domcontentloaded" });
  await waitForApp(page);
  await page.waitForFunction(() => document.documentElement.dataset.dataMode === "demo");
  return { context, page };
}


async function chooseMode(page, mode) {
  await page.locator('[data-testid="data-mode-chip"]:visible').first().click();
  await page.getByTestId(`data-mode-${mode}`).click();
  await page.waitForFunction((value) => document.documentElement.dataset.dataMode === value, mode);
}


async function checkDimensions(page, width) {
  const dimensions = await page.evaluate(() => ({
    viewport: window.innerWidth,
    document: document.documentElement.scrollWidth,
    body: document.body.scrollWidth,
  }));
  assert.ok(
    Math.max(dimensions.document, dimensions.body) <= dimensions.viewport + 1,
    `${width}px viewport has horizontal overflow: ${JSON.stringify(dimensions)}`,
  );
}


async function checkFonts(page) {
  const loaded = await page.evaluate(async () => {
    await document.fonts.ready;
    const manrope = await document.fonts.load("16px Manrope", "BananaGuard");
    const space = await document.fonts.load("16px 'Space Grotesk'", "BananaGuard");
    const symbols = await document.fonts.load("24px 'Material Symbols Rounded'", "settings");
    return [manrope.length, space.length, symbols.length];
  });
  assert.deepEqual(loaded.map((count) => count > 0), [true, true, true], "vendored fonts did not load");
}


function assertNoDemoLeakage(text, extra = []) {
  for (const forbidden of [...DEMO_SENTINELS, ...extra]) {
    assert.ok(!text.toLowerCase().includes(forbidden.toLowerCase()), `Actual leaked Demo content: ${forbidden}`);
  }
}


async function checkHttpLicenses(page) {
  const licenses = await page.evaluate(async () => {
    const paths = ["/LICENSE", "/vendor/LICENSES.txt", "/vendor/fonts/LICENSES.txt"];
    return Object.fromEntries(await Promise.all(paths.map(async (path) => {
      const response = await fetch(path);
      return [path, { ok: response.ok, text: await response.text() }];
    })));
  });
  assert.ok(licenses["/LICENSE"].ok && licenses["/LICENSE"].text.includes("Copyright (c) 2026 BananaGuard Team"));
  assert.ok(licenses["/vendor/LICENSES.txt"].ok && licenses["/vendor/LICENSES.txt"].text.includes("Copyright (c) Facebook, Inc. and its affiliates."));
  assert.ok(licenses["/vendor/LICENSES.txt"].text.includes("Modernizr 3.0.0pre (Custom Build) | MIT"));
  assert.ok(licenses["/vendor/fonts/LICENSES.txt"].ok && licenses["/vendor/fonts/LICENSES.txt"].text.includes("SIL OPEN FONT LICENSE Version 1.1"));
  assert.ok(licenses["/vendor/fonts/LICENSES.txt"].text.includes("Apache License"));
}


async function settingsAccessibility(page) {
  await page.locator('button[title="Settings"]:visible').click();
  await page.waitForFunction(() => document.body.innerText.includes("Synthetic interface controls"));
  const controls = page.locator("#main-content button:visible, #main-content input:visible, #main-content select:visible");
  await assertNamedControls(controls, "settings");
}


async function temperatureCardReading(page, label) {
  const card = page.getByText(label, { exact: true }).filter({ visible: true }).first().locator("xpath=../..");
  const text = await card.innerText();
  const match = text.match(/(-?\d+(?:\.\d+)?)\s*°([CF])/i);
  assert.ok(match, `${label} did not expose a temperature reading: ${text}`);
  return { value: Number(match[1]), unit: `°${match[2].toUpperCase()}`, text };
}


async function demoSettingsContracts(page) {
  await page.locator('button[title="Settings"]:visible').click();
  await page.waitForFunction(() => document.body.innerText.includes("Synthetic interface controls"));
  assert.equal(
    await page.getByText("Text size", { exact: true }).filter({ visible: true }).count(),
    0,
    "Demo Settings still exposed the inert Text size control",
  );
  for (const category of ["Power settings", "Connectivity settings"]) {
    assert.equal(
      await page.getByRole("button", { name: category, exact: true }).count(),
      0,
      `Demo Settings still exposed the no-op ${category} category`,
    );
  }

  const fahrenheit = page.locator("button:visible").filter({ hasText: /^°F$/ }).first();
  await fahrenheit.click();
  assert.equal(await fahrenheit.getAttribute("aria-pressed"), "true", "°F setting did not become active");
  await page.locator('button[title="Environment"]:visible').first().click();
  await page.getByRole("heading", { name: "Environment", exact: true }).waitFor();
  const airF = await temperatureCardReading(page, "Air temp");
  const soilF = await temperatureCardReading(page, "Soil temp");
  const thermalF = await page.getByText("Thermal profile", { exact: true }).filter({ visible: true }).first().locator("xpath=../..").innerText();
  assert.equal(airF.unit, "°F");
  assert.equal(soilF.unit, "°F");
  assert.ok(airF.value >= 86 && airF.value <= 91, `Demo air temperature was not converted to roughly 88.5°F: ${airF.text}`);
  assert.ok(soilF.value >= 79 && soilF.value <= 84, `Demo soil temperature was not converted to roughly 81.7°F: ${soilF.text}`);
  assert.match(thermalF, /64°[\s\S]*Canopy\s+\d+(?:\.\d+)?°F[\s\S]*104°/i, `Thermal profile did not convert to Fahrenheit: ${thermalF}`);

  await page.locator('button[title="Settings"]:visible').click();
  const celsius = page.locator("button:visible").filter({ hasText: /^°C$/ }).first();
  await celsius.click();
  assert.equal(await celsius.getAttribute("aria-pressed"), "true", "°C setting did not become active");
  await page.locator('button[title="Environment"]:visible').first().click();
  await page.getByRole("heading", { name: "Environment", exact: true }).waitFor();
  const airC = await temperatureCardReading(page, "Air temp");
  const soilC = await temperatureCardReading(page, "Soil temp");
  const thermalC = await page.getByText("Thermal profile", { exact: true }).filter({ visible: true }).first().locator("xpath=../..").innerText();
  assert.equal(airC.unit, "°C");
  assert.equal(soilC.unit, "°C");
  assert.ok(airC.value >= 29.5 && airC.value <= 33.5, `Demo air temperature did not return to Celsius: ${airC.text}`);
  assert.ok(soilC.value >= 25.5 && soilC.value <= 29.5, `Demo soil temperature did not return to Celsius: ${soilC.text}`);
  assert.ok(Math.abs(airF.value - (airC.value * 9 / 5 + 32)) < 2, "air °F/°C readings are not a credible conversion pair");
  assert.ok(Math.abs(soilF.value - (soilC.value * 9 / 5 + 32)) < 2, "soil °F/°C readings are not a credible conversion pair");
  assert.match(thermalC, /18°[\s\S]*Canopy\s+\d+(?:\.\d+)?°C[\s\S]*40°/i, `Thermal profile did not return to Celsius: ${thermalC}`);
}


async function semanticContrastContracts(page) {
  await page.locator('button[title="Settings"]:visible').click();
  await page.getByRole("button", { name: "Display settings", exact: true }).filter({ visible: true }).first().click();
  const themes = ["Verdant", "Harvest", "Neural"];
  for (const theme of themes) {
    const themeButton = page.getByRole("button", { name: `${theme} visual direction`, exact: true }).filter({ visible: true }).first();
    await themeButton.click();
    assert.equal(await themeButton.getAttribute("aria-pressed"), "true", `${theme} theme did not become active`);
    const ratios = await page.evaluate(() => {
      const styles = getComputedStyle(document.querySelector("#main-content"));
      const parse = (raw) => {
        const value = String(raw || "").trim();
        if (value.startsWith("#")) {
          const hex = value.slice(1);
          const expanded = hex.length === 3 ? hex.split("").map((part) => part + part).join("") : hex;
          return {
            r: parseInt(expanded.slice(0, 2), 16),
            g: parseInt(expanded.slice(2, 4), 16),
            b: parseInt(expanded.slice(4, 6), 16),
            a: 1,
          };
        }
        const parts = value.match(/[\d.]+/g)?.map(Number) || [];
        return { r: parts[0], g: parts[1], b: parts[2], a: parts.length > 3 ? parts[3] : 1 };
      };
      const token = (name) => parse(styles.getPropertyValue(name));
      const composite = (front, back) => {
        const alpha = front.a + back.a * (1 - front.a);
        if (!alpha) return { r: 0, g: 0, b: 0, a: 0 };
        return {
          r: (front.r * front.a + back.r * back.a * (1 - front.a)) / alpha,
          g: (front.g * front.a + back.g * back.a * (1 - front.a)) / alpha,
          b: (front.b * front.a + back.b * back.a * (1 - front.a)) / alpha,
          a: alpha,
        };
      };
      const luminance = (color) => {
        const channels = [color.r, color.g, color.b].map((channel) => {
          const value = channel / 255;
          return value <= 0.04045 ? value / 12.92 : ((value + 0.055) / 1.055) ** 2.4;
        });
        return channels[0] * 0.2126 + channels[1] * 0.7152 + channels[2] * 0.0722;
      };
      const ratio = (foreground, background) => {
        const lighter = Math.max(luminance(foreground), luminance(background));
        const darker = Math.min(luminance(foreground), luminance(background));
        return (lighter + 0.05) / (darker + 0.05);
      };
      const pageBackground = token("--bg1");
      const surface = composite(token("--surface"), pageBackground);
      const pairs = [
        ["primary button", "--primary-ink", "--primary"],
        ["primary status", "--primary", "--primary-c"],
        ["secondary status", "--secondary", "--secondary-c"],
        ["tertiary status", "--tertiary", "--tertiary-c"],
        ["warning status", "--warn", "--warn-c"],
        ["danger status", "--bad", "--bad-c"],
        ["ok status", "--ok", "--ok-c"],
      ];
      return pairs.map(([label, foregroundToken, backgroundToken]) => {
        const background = composite(token(backgroundToken), surface);
        const foreground = composite(token(foregroundToken), background);
        return { label, ratio: ratio(foreground, background) };
      });
    });
    for (const pair of ratios) {
      assert.ok(pair.ratio >= 4.5, `${theme} ${pair.label} contrast ${pair.ratio.toFixed(2)} is below 4.5:1`);
    }
  }
  await page.getByRole("button", { name: "Verdant visual direction", exact: true }).filter({ visible: true }).first().click();
}


async function persistentStatusAndRingSemantics(page) {
  await page.locator('button[title="Overview"]:visible').first().click();
  await page.getByRole("heading", { name: "Overview", exact: true }).waitFor();
  const liveStatuses = page.locator('[role="status"][aria-live="polite"]');
  assert.equal(await liveStatuses.count(), 1, "dashboard did not expose exactly one persistent polite live status");
  assert.match(await liveStatuses.first().innerText(), /DEMO|SYNTHETIC/i, "persistent live status did not announce the Demo data state");

  const ringSvgs = await page.locator("#main-content svg:visible").evaluateAll((svgs) => svgs
    .filter((svg) => svg.querySelector("circle"))
    .map((svg) => ({ ariaHidden: svg.getAttribute("aria-hidden"), focusable: svg.getAttribute("focusable") })));
  assert.ok(ringSvgs.length >= 2, "overview did not render the expected Ring SVGs");
  for (const [index, svg] of ringSvgs.entries()) {
    assert.deepEqual(svg, { ariaHidden: "true", focusable: "false" }, `Ring SVG ${index + 1} was exposed to assistive technology or keyboard focus`);
  }
}


async function assertNamedControls(controls, scope) {
  const count = await controls.count();
  assert.ok(count > 3, `${scope} smoke found too few interactive controls`);
  for (let i = 0; i < count; i += 1) {
    const snapshot = await controls.nth(i).ariaSnapshot();
    assert.match(
      snapshot,
      /(?:button|slider|checkbox|radio|combobox)\s+"[^"\n]+"/,
      `visible ${scope} control ${i + 1} has no accessible name: ${snapshot}`,
    );
  }
}


async function responsiveDemoIsolation(browser, url, state) {
  state.reset({ meta: baseMeta({ field_ingest_configured: false, field_ready: false }) });
  const { context, page } = await openDemoPage(browser, url, { width: 320, height: 844 });
  const externalRequests = [];
  page.on("request", (request) => {
    const requestUrl = new URL(request.url());
    if (requestUrl.protocol.startsWith("http") && requestUrl.origin !== new URL(url).origin) externalRequests.push(request.url());
  });
  for (const width of [320, 360, 390, 768, 1280]) {
    await page.setViewportSize({ width, height: width < 700 ? 844 : 900 });
    await page.goto(url, { waitUntil: "domcontentloaded" });
    await waitForApp(page);
    await page.waitForFunction(() => document.documentElement.dataset.dataMode === "demo");
    await checkDimensions(page, width);
    if (width === 320) {
      await assertNamedControls(page.locator("button:visible, input:visible, select:visible"), "320px mobile");
    }
  }
  await checkFonts(page);
  await checkHttpLicenses(page);
  await settingsAccessibility(page);
  await demoSettingsContracts(page);
  await semanticContrastContracts(page);
  await persistentStatusAndRingSemantics(page);
  await page.waitForTimeout(300);
  assert.deepEqual(state.requests, [], `Demo mode made API requests: ${state.requests.join(", ")}`);
  assert.deepEqual(externalRequests, [], `canonical dashboard requested external assets: ${externalRequests.join(", ")}`);
  await context.close();
}


async function staticOriginSetupRequired(browser, url, state) {
  state.reset({ meta: { error: "not found" }, metaStatus: 404 });
  const { context, page } = await openDemoPage(browser, url);
  await chooseMode(page, "actual");
  await page.waitForFunction(() => document.body.innerText.includes("SETUP REQUIRED"));
  await page.waitForTimeout(2300);
  assert.equal(state.requests.filter((path) => path === "/api/meta").length, 1, "static-origin 404 kept retrying metadata");
  assert.equal(state.requests.filter((path) => path.startsWith("/api/nodes")).length, 0, "static-origin 404 reached telemetry polling");
  assertNoDemoLeakage(await page.locator("body").innerText());
  await context.close();
}


async function setupRequiredIsolation(browser, url, state) {
  state.reset({
    meta: baseMeta({ field_ingest_configured: false, field_ready: false }),
    nodes: [fieldNode({ display_name: "MUST NOT BE REQUESTED" })],
  });
  const { context, page } = await openDemoPage(browser, url);
  await chooseMode(page, "actual");
  await page.waitForFunction(() => document.body.innerText.includes("SETUP REQUIRED"));
  const text = await page.locator("body").innerText();
  assertNoDemoLeakage(text, ["MUST NOT BE REQUESTED"]);
  assert.ok(state.requests.some((path) => path === "/api/meta"), "Actual never performed metadata handshake");
  assert.equal(state.requests.filter((path) => path.startsWith("/api/nodes")).length, 0, "unconfigured Actual requested nodes");
  await context.close();
}


async function rejectedMetadata(browser, url, state, meta, label) {
  state.reset({ meta, nodes: [fieldNode({ display_name: `${label} LEAK` })] });
  const { context, page } = await openDemoPage(browser, url);
  await chooseMode(page, "actual");
  await page.waitForFunction(() => document.body.innerText.includes("CONNECTION ERROR"));
  const text = await page.locator("body").innerText();
  assert.match(text, /Unsupported telemetry API/i, `${label} metadata was not rejected with a contract error`);
  assert.ok(!text.includes(`${label} LEAK`));
  assert.equal(state.requests.filter((path) => path.startsWith("/api/nodes")).length, 0, `${label} metadata reached telemetry polling`);
  await context.close();
}


function fieldLog(overrides = {}) {
  return {
    time: isoAgo(10),
    node: "field-node-007",
    type: "detect",
    icon: "pest_control",
    sev: "info",
    title: "Exact field log",
    detail: "N_pest 23 | soil 41% | 3970 mV | sprays today 4",
    source_kind: "field",
    payload_version: 1,
    application_id: "bananaguard-production",
    ...overrides,
  };
}


function historyRow(nPest, soil, batt, secondsAgo, overrides = {}) {
  return {
    device_id: "field-node-007",
    n_pest: nPest,
    soil_vwc_pct: soil,
    batt_pct: batt,
    received_at: isoAgo(secondsAgo),
    source_kind: "field",
    payload_version: 1,
    ...overrides,
  };
}


async function successfulFieldMapping(browser, url, state) {
  const simulatorLeak = fieldNode({ device_id: "sim-leak", display_name: "Simulator Leak Node", source_kind: "simulator" });
  const legacyLeak = fieldNode({ device_id: "legacy-leak", display_name: "Legacy Leak Node", source_kind: "legacy_unknown" });
  state.reset({
    meta: baseMeta({ counts: { field: 4, simulator: 2, legacy_unknown: 1, uplinks: 7, nodes: 1, registered_nodes: 1 } }),
    nodes: [simulatorLeak, fieldNode(), legacyLeak],
    logs: [
      fieldLog(),
      fieldLog({ node: "sim-leak", title: "SIMULATOR SECRET", source_kind: "simulator" }),
      fieldLog({ title: "FIELD PAYLOAD V2 MUST NOT RENDER", payload_version: 2 }),
    ],
    history: [
      historyRow(5, 47, 84.1, 180),
      historyRow(11, 44, 86.2, 90),
      historyRow(23, 41, 87.6, 12),
      historyRow(999, 99, 1, 5, { source_kind: "simulator" }),
      historyRow(777, 77, 2, 4, { payload_version: 2 }),
    ],
  });
  const { context, page } = await openDemoPage(browser, url);
  await page.evaluate(() => localStorage.setItem(
    "bg_photos",
    JSON.stringify(["data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="]),
  ));
  await page.reload({ waitUntil: "domcontentloaded" });
  await waitForApp(page);
  await chooseMode(page, "actual");
  await page.waitForFunction(() => document.body.innerText.includes("AUTHENTICATED FIELD TELEMETRY"));
  await page.waitForFunction(() => document.body.innerText.includes("Exact field log"));

  let text = await page.locator("body").innerText();
  for (const expected of ["field-node-007", "Verified Plot Alpha", "23.0", "15 EIL", "41%", "Exact field log · field-node-007", "3970 mV", "FCnt 321"]) {
    assert.ok(text.includes(expected), `field mapping did not render ${expected}`);
  }
  assert.match(text, /88\s*%/, "field battery estimate did not map 87.6 to 88%");
  assert.ok(text.includes("ACTION THRESHOLD EXCEEDED"), "field n_pest > EIL without a spray was not surfaced as actionable");
  assert.ok(!text.includes("The action condition has not been met"), "field n_pest > EIL used the below-threshold explanation");
  assert.ok(!text.includes("FIELD PAYLOAD V2 MUST NOT RENDER"), "Actual rendered a field log with an unsupported payload version");
  assertNoDemoLeakage(text, ["Simulator Leak Node", "Legacy Leak Node", "SIMULATOR SECRET", "999", "777"]);

  await page.getByRole("button", { name: "Simple", exact: true }).filter({ visible: true }).first().click();
  await page.getByText("Battery estimate", { exact: true }).filter({ visible: true }).first().waitFor();
  assert.equal(
    await page.getByText("Battery", { exact: true }).filter({ visible: true }).count(),
    0,
    "Actual Simple view exposed a bare Battery label instead of an estimate",
  );
  await page.getByRole("button", { name: "Advanced", exact: true }).filter({ visible: true }).first().click();

  await page.locator("button.bg-node-picker:visible").click();
  await page.waitForFunction(() => document.body.innerText.includes("Zone 7 · battery est. 88%"));
  await page.keyboard.press("Escape");

  const expectedRequests = [
    "/api/nodes?source=field",
    "/api/logs?node=field-node-007&n=30&source=field",
    "/api/history?node=field-node-007&n=30&source=field",
  ];
  for (const expected of expectedRequests) {
    assert.ok(state.requests.includes(expected), `missing source/node-scoped request ${expected}; got ${state.requests.join(", ")}`);
  }

  await page.locator('button[title="Detections"]:visible').first().click();
  text = await page.locator("body").innerText();
  assert.ok(!text.includes("Reference captures"), "Actual exposed the local Demo photo gallery");
  assert.ok(!text.includes("drop your photos"), "Actual exposed the local photo-upload affordance");
  await page.locator('button[title="Alerts"]:visible').first().click();
  await page.waitForFunction(() => document.body.innerText.includes("Alerts & activity"));
  assert.ok(!(await page.locator("body").innerText()).includes("Mark all read"), "Actual exposed the Demo acknowledgement control");
  await page.keyboard.press("Escape");

  await page.locator('button[title="Analytics"]:visible').first().click();
  await page.waitForFunction(() => document.body.innerText.includes("3 source-backed samples shown"));
  text = await page.locator("body").innerText();
  assert.ok(!text.includes("Detections this week"), "Actual analytics rendered Demo aggregate cards");
  assert.ok(!text.includes("Chemical avoided"), "Actual analytics rendered modeled savings");
  await context.close();
}


async function limitedCapabilityTelemetry(browser, url, state) {
  const currentRow = "Limited-capability current row";
  const logTitle = "Limited-capability field log";
  state.reset({
    meta: baseMeta({
      capabilities: { telemetry: true, remote_commands: false, history: false, export: false },
    }),
    nodes: [fieldNode({ display_name: currentRow })],
    logs: [fieldLog({ title: logTitle })],
    history: [historyRow(999, 99, 1, 1)],
  });
  const { context, page } = await openDemoPage(browser, url);
  await chooseMode(page, "actual");
  await page.waitForFunction((expected) => document.body.innerText.includes(expected), logTitle);

  const text = await page.locator("body").innerText();
  assert.ok(text.includes(currentRow), "limited-capability Actual did not map current telemetry");
  assert.ok(text.includes(logTitle), "limited-capability Actual did not map field logs");
  assert.ok(state.requests.includes("/api/nodes?source=field"), "limited-capability Actual did not request current telemetry");
  assert.ok(state.requests.includes("/api/logs?node=field-node-007&n=30&source=field"), "limited-capability Actual did not request logs");
  assert.equal(
    state.requests.filter((path) => path.startsWith("/api/history")).length,
    0,
    "limited-capability Actual requested unsupported history",
  );

  await page.getByRole("button", { name: "View all", exact: true }).filter({ visible: true }).first().click();
  await page.waitForFunction(() => document.body.innerText.includes("Activity"));
  assert.equal(
    await page.getByRole("button", { name: "Export telemetry CSV", exact: true }).filter({ visible: true }).count(),
    0,
    "limited-capability Actual exposed unsupported CSV export",
  );
  await context.close();
}


async function mixedFleetFaultSemantics(browser, url, state) {
  const freshName = "Mixed fleet fresh node";
  const faultName = "Mixed fleet stale safety fault";
  state.reset({
    meta: baseMeta(),
    nodes: [
      fieldNode({ device_id: "fleet-fresh", display_name: freshName, block: "Fresh block" }),
      fieldNode({
        device_id: "fleet-fault",
        display_name: faultName,
        block: "Fault block",
        status: "fault",
        safety_violation: true,
        camera_fault: true,
        online: false,
        received_at: isoAgo(1800),
        source_time: isoAgo(1800),
        source_age_seconds: 1800,
      }),
    ],
  });
  const { context, page } = await openDemoPage(browser, url);
  await chooseMode(page, "actual");
  await page.waitForFunction((expected) => document.body.innerText.includes(expected), faultName);

  const overviewFaultRow = page.getByText("fleet-fault", { exact: true }).filter({ visible: true }).first().locator("xpath=..");
  assert.match(await overviewFaultRow.innerText(), /\bFAULT\b/i, "overview Fleet downgraded an offline safety fault to a non-fault status");

  await page.locator("button.bg-node-picker:visible").click();
  const pickerFaultRow = page.getByRole("button", { name: new RegExp(`fleet-fault.*${faultName}`, "i") }).filter({ visible: true }).first();
  assert.match(await pickerFaultRow.innerText(), /\bFAULT\b/i, "node picker downgraded an offline safety fault to a non-fault status");
  await pickerFaultRow.click();
  await page.waitForFunction((expected) => document.body.innerText.includes(expected), faultName);

  await page.locator('button[title="Fleet"]:visible').first().click();
  await page.getByRole("heading", { name: "Fleet", exact: true }).waitFor();
  const onlineCard = page.getByText("Nodes online", { exact: true }).filter({ visible: true }).first().locator("xpath=..");
  assert.match(await onlineCard.innerText(), /1\s*\/\s*2/, "selecting the stale node zeroed the fresh fleet-wide online count");
  const fleetFaultCard = page.getByText(faultName, { exact: true }).filter({ visible: true }).first().locator("xpath=../..");
  assert.match(await fleetFaultCard.innerText(), /\bFAULT\b/i, "Fleet card downgraded an offline safety fault to a non-fault status");
  await context.close();
}


async function nodeSelectionCancelsStaleRequests(browser, url, state) {
  const nodeA = "Selection race node A";
  const nodeB = "Selection race node B";
  const nodes = [
    fieldNode({ device_id: "race-a", display_name: nodeA, block: "Race A" }),
    fieldNode({ device_id: "race-b", display_name: nodeB, block: "Race B", n_pest: 8 }),
  ];
  state.reset({
    meta: baseMeta(),
    nodes,
    logs: [fieldLog({ node: "race-a", title: "Initial race confirmation" })],
    history: [historyRow(4, 40, 80, 120), historyRow(5, 41, 81, 60)],
  });
  const { context, page } = await openDemoPage(browser, url);
  await chooseMode(page, "actual");
  await page.waitForFunction(() => document.body.innerText.includes("Initial race confirmation"));
  await page.evaluate(() => {
    window.__BG_UNEXPECTED_CONNECTION_ERRORS__ = [];
    const scan = () => {
      const text = document.body.innerText;
      const match = text.match(/CONNECTION (?:ERROR|LOST)/);
      if (match && !window.__BG_UNEXPECTED_CONNECTION_ERRORS__.includes(match[0])) {
        window.__BG_UNEXPECTED_CONNECTION_ERRORS__.push(match[0]);
      }
    };
    new MutationObserver(scan).observe(document.body, { childList: true, subtree: true, characterData: true });
    scan();
  });

  const nodesBefore = state.requests.filter((path) => path === "/api/nodes?source=field").length;
  state.delayNodesMs = 3000;
  state.logs = [fieldLog({ node: "race-b", title: "Node B immediate confirmation" })];
  state.history = [historyRow(8, 42, 82, 20)];
  await page.locator('[data-testid="data-mode-chip"]:visible').first().click();
  await page.locator("button:visible", { hasText: "Retry now" }).first().click();
  await eventually(
    () => state.requests.filter((path) => path === "/api/nodes?source=field").length > nodesBefore,
    "retry never began the delayed old nodes request",
  );
  state.delayNodesMs = 0;
  await page.keyboard.press("Escape");
  await page.locator("button.bg-node-picker:visible").click();
  await page.getByRole("button", { name: new RegExp(`race-b.*${nodeB}`, "i") }).filter({ visible: true }).first().click();
  await eventually(
    () => state.abortedRequests.includes("/api/nodes?source=field"),
    "selecting a cached node did not abort the old nodes request",
  );
  await eventually(
    () => state.requests.includes("/api/logs?node=race-b&n=30&source=field"),
    "selected node B did not begin its immediate refresh",
    2500,
  );
  await page.waitForFunction(() => document.body.innerText.includes("Node B immediate confirmation"));

  const oldBLogPath = "/api/logs?node=race-b&n=30&source=field";
  const oldBHistoryPath = "/api/history?node=race-b&n=30&source=field";
  const bLogsBefore = state.requests.filter((path) => path === oldBLogPath).length;
  const bHistoryBefore = state.requests.filter((path) => path === oldBHistoryPath).length;
  const aLogsBefore = state.requests.filter((path) => path === "/api/logs?node=race-a&n=30&source=field").length;
  state.delayLogsMs = 3000;
  state.delayHistoryMs = 3000;
  state.logs = [fieldLog({ node: "race-b", title: "OLD DELAYED DETAILS MUST NOT RENDER" })];
  state.history = [historyRow(999, 99, 1, 1)];
  await page.locator('[data-testid="data-mode-chip"]:visible').first().click();
  await page.locator("button:visible", { hasText: "Retry now" }).first().click();
  await eventually(
    () => state.requests.filter((path) => path === oldBLogPath).length > bLogsBefore
      && state.requests.filter((path) => path === oldBHistoryPath).length > bHistoryBefore,
    "retry never began the delayed old log/history requests",
  );
  state.delayLogsMs = 0;
  state.delayHistoryMs = 0;
  state.logs = [fieldLog({ node: "race-a", title: "Node A immediate confirmation" })];
  state.history = [historyRow(4, 40, 80, 10)];
  await page.keyboard.press("Escape");
  await page.locator("button.bg-node-picker:visible").click();
  await page.getByRole("button", { name: new RegExp(`race-a.*${nodeA}`, "i") }).filter({ visible: true }).first().click();
  await eventually(
    () => state.abortedRequests.includes(oldBLogPath) && state.abortedRequests.includes(oldBHistoryPath),
    "selecting node A did not abort the old detail requests",
  );
  await eventually(
    () => state.requests.filter((path) => path === "/api/logs?node=race-a&n=30&source=field").length > aLogsBefore,
    "selected node A did not begin its immediate refresh",
    2500,
  );
  await page.waitForFunction(() => document.body.innerText.includes("Node A immediate confirmation"));
  const text = await page.locator("body").innerText();
  assert.ok(!text.includes("OLD DELAYED DETAILS MUST NOT RENDER"), "aborted old detail data rendered after node selection");
  assert.match(await page.locator('[data-testid="data-mode-chip"]:visible').first().innerText(), /FRESH/i, "selected node refresh did not become fresh");
  assert.deepEqual(
    await page.evaluate(() => window.__BG_UNEXPECTED_CONNECTION_ERRORS__),
    [],
    "intentional selection abort surfaced a connection error/backoff state",
  );
  await context.close();
}


async function failedDetailConfirmationPreservesData(browser, url, state) {
  const confirmedName = "Prior confirmed field row";
  const unconfirmedName = "UNCONFIRMED NODE UPDATE MUST NOT RENDER";
  const confirmedLog = "PRIOR CONFIRMED LOG MUST REMAIN";
  state.reset({
    meta: baseMeta(),
    nodes: [fieldNode({ display_name: confirmedName, n_pest: 7 })],
    logs: [fieldLog({ title: confirmedLog })],
    history: [
      historyRow(5, 47, 84.1, 180),
      historyRow(6, 44, 86.2, 90),
      historyRow(7, 41, 87.6, 12),
    ],
  });
  const { context, page } = await openDemoPage(browser, url);
  await chooseMode(page, "actual");
  await page.waitForFunction((expected) => document.body.innerText.includes(expected), confirmedLog);

  const nodesBefore = state.requests.filter((path) => path === "/api/nodes?source=field").length;
  const logsBefore = state.requests.filter((path) => path.startsWith("/api/logs?")).length;
  const historyBefore = state.requests.filter((path) => path.startsWith("/api/history?")).length;
  state.nodes = [fieldNode({ display_name: unconfirmedName, n_pest: 99 })];
  state.logsStatus = 500;
  state.historyStatus = 500;
  await page.locator('[data-testid="data-mode-chip"]:visible').first().click();
  await page.locator("button:visible", { hasText: "Retry now" }).first().click();
  await page.waitForFunction(() => {
    const chip = document.querySelector('[data-testid="data-mode-chip"]');
    return chip && chip.innerText.includes("ERROR");
  });
  assert.ok(state.requests.filter((path) => path === "/api/nodes?source=field").length > nodesBefore, "detail-failure retry did not receive a successful nodes response");
  assert.ok(state.requests.filter((path) => path.startsWith("/api/logs?")).length > logsBefore, "detail-failure retry never requested logs");
  assert.ok(state.requests.filter((path) => path.startsWith("/api/history?")).length > historyBefore, "detail-failure retry never requested history");

  let text = await page.locator("body").innerText();
  assert.ok(text.includes(confirmedName), "failed detail confirmation discarded the prior confirmed node");
  assert.ok(text.includes(confirmedLog), "failed detail confirmation cleared prior confirmed activity");
  assert.ok(!text.includes(unconfirmedName), "unconfirmed nodes data replaced the prior confirmed sample");
  assert.match(text, /confirm|500/i, "detail failure did not explain that confirmation failed");
  assert.doesNotMatch(await page.locator('[data-testid="data-mode-chip"]:visible').first().innerText(), /FRESH/i, "failed detail confirmation remained fresh");

  await page.keyboard.press("Escape");
  await page.locator('button[title="Analytics"]:visible').first().click();
  await page.waitForFunction(() => document.body.innerText.includes("3 source-backed samples shown"));
  text = await page.locator("body").innerText();
  assert.ok(!text.includes("No source-backed history is available"), "detail failure replaced confirmed history with an empty state");
  await context.close();
}


async function readinessNotAttested(browser, url, state) {
  state.reset({
    meta: baseMeta({ field_ready: false }),
    nodes: [fieldNode({ display_name: "Read-only unattested field row" })],
  });
  const { context, page } = await openDemoPage(browser, url);
  await chooseMode(page, "actual");
  await page.waitForFunction(() => document.body.innerText.includes("Read-only unattested field row"));
  const label = page.getByText("AUTHENTICATED FIELD TELEMETRY · READINESS NOT ATTESTED", { exact: true }).filter({ visible: true }).first();
  await label.waitFor();
  const text = await page.locator("body").innerText();
  assert.ok(text.includes("Read-only unattested field row"));
  const chipText = await page.locator('[data-testid="data-mode-chip"]:visible').first().innerText();
  assert.match(chipText, /ACTUAL/i);
  assert.match(chipText, /FRESH/i);
  assert.ok(text.includes("READINESS NOT ATTESTED"));
  assert.ok(!text.includes("VERIFIED FIELD TELEMETRY"));
  assert.match(text, /read-only|no remote commands/i);
  const colors = await label.evaluate((element) => {
    const resolveColor = (value) => {
      const probe = document.createElement("span");
      probe.style.color = value;
      element.appendChild(probe);
      const color = getComputedStyle(probe).color;
      probe.remove();
      return color;
    };
    return { actual: getComputedStyle(element).color, warn: resolveColor("var(--warn)"), ok: resolveColor("var(--ok)") };
  });
  assert.equal(colors.actual, colors.warn, "unattested readiness was not styled as a warning");
  assert.notEqual(colors.actual, colors.ok, "unattested readiness used the green ready color");
  await context.close();
}


async function lowBatteryAndThresholdSemantics(browser, url, state) {
  state.reset({
    meta: baseMeta(),
    nodes: [fieldNode({
      display_name: "Low estimate field node",
      n_pest: 4,
      batt_pct: 9.4,
      status: "clear",
      action: "NONE",
      action_status: null,
    })],
  });
  let opened = await openDemoPage(browser, url);
  const demoLowLive = await opened.page.getByTestId("telemetry-live-status").innerText();
  assert.match(demoLowLive, /Demo mode/i);
  await chooseMode(opened.page, "actual");
  await opened.page.waitForFunction(() => document.body.innerText.includes("LOW BATTERY ESTIMATE"));
  let text = await opened.page.locator("body").innerText();
  assert.match(text, /dashboard estimate|voltage-derived/i, "low battery state was presented as a firmware power claim");
  assert.ok(!text.includes("ALL CLEAR"), "low battery Actual telemetry was presented as an overall all-clear");
  const actualLowLive = await opened.page.getByTestId("telemetry-live-status").innerText();
  assert.match(actualLowLive, /Actual mode\.\s+FRESH\.\s+LOW BATTERY ESTIMATE/i, "persistent status did not announce the low-battery Actual update");
  assert.notEqual(actualLowLive, demoLowLive, "persistent status did not update when Actual telemetry arrived");
  await opened.context.close();

  state.reset({
    meta: baseMeta(),
    nodes: [fieldNode({
      display_name: "Below-threshold field node",
      n_pest: 4,
      batt_pct: 82,
      status: "clear",
      action: "NONE",
      action_status: null,
    })],
  });
  opened = await openDemoPage(browser, url);
  const demoThresholdLive = await opened.page.getByTestId("telemetry-live-status").innerText();
  await chooseMode(opened.page, "actual");
  await opened.page.waitForFunction(() => document.body.innerText.includes("BELOW ACTION THRESHOLD"));
  text = await opened.page.locator("body").innerText();
  assert.match(text, /limited to pest\/action evidence|not an overall node all-clear/i, "below-threshold state omitted its limited-evidence qualification");
  assert.ok(!text.includes("ALL CLEAR"), "below-threshold Actual telemetry was presented as an overall all-clear");
  const actualThresholdLive = await opened.page.getByTestId("telemetry-live-status").innerText();
  assert.match(actualThresholdLive, /Actual mode\.\s+FRESH\.\s+BELOW ACTION THRESHOLD/i, "persistent status did not announce the below-threshold Actual update");
  assert.notEqual(actualThresholdLive, demoThresholdLive, "persistent status did not update for the below-threshold sample");
  await opened.context.close();
}


async function staleAndFaultSemantics(browser, url, state) {
  state.reset({
    meta: baseMeta(),
    nodes: [fieldNode({ received_at: isoAgo(1800), source_time: isoAgo(1800), source_age_seconds: 1800, online: false, status: "fault", safety_violation: true, camera_fault: true, action: "SPRAY" })],
  });
  let opened = await openDemoPage(browser, url);
  await chooseMode(opened.page, "actual");
  await opened.page.waitForFunction(() => document.body.innerText.includes("STALE SAMPLE"));
  let text = await opened.page.locator("body").innerText();
  assert.ok(text.includes("SAFETY FAULT · STALE SAMPLE"), "stale safety violation lost its fault prominence");
  assert.ok(text.includes("last-confirmed field sample reports a safety invariant violation"));
  assert.ok(text.includes("LAST-CONFIRMED FIELD TELEMETRY"));
  assert.match(text, /Gate\s+LOCKED/i, "stale field sample did not fail the safety gate closed");
  assertNoDemoLeakage(text);
  await opened.context.close();

  state.reset({
    meta: baseMeta(),
    nodes: [fieldNode({ status: "fault", safety_violation: true, camera_fault: true, action: "SPRAY", action_status: "reported" })],
  });
  opened = await openDemoPage(browser, url);
  await chooseMode(opened.page, "actual");
  await opened.page.waitForFunction(() => document.body.innerText.includes("SAFETY FAULT"));
  text = await opened.page.locator("body").innerText();
  assert.match(text, /locked out|cannot command|read-only/i, "fault state omitted safe read-only semantics");
  assert.ok(!text.includes("DEMO SPRAY"), "fault field record was presented as a Demo actuator event");
  await opened.context.close();
}


async function heldAndLockoutSemantics(browser, url, state) {
  state.reset({
    meta: baseMeta(),
    nodes: [fieldNode({ status: "held", treatment_held: true, soil_safe: false, action: "LOCKOUT" })],
  });
  let opened = await openDemoPage(browser, url);
  await chooseMode(opened.page, "actual");
  await opened.page.waitForFunction(() => document.body.innerText.includes("explicitly reports treatment held"));
  let text = await opened.page.locator("body").innerText();
  assert.match(text, /\bHELD\b/);
  assert.ok(!text.includes("LOCKOUT REPORTED"));
  await opened.context.close();

  state.reset({
    meta: baseMeta(),
    nodes: [fieldNode({ status: "blocked", treatment_held: false, soil_safe: true, action: "LOCKOUT" })],
  });
  opened = await openDemoPage(browser, url);
  await chooseMode(opened.page, "actual");
  await opened.page.waitForFunction(() => document.body.innerText.includes("LOCKOUT REPORTED"));
  text = await opened.page.locator("body").innerText();
  assert.ok(text.includes("specific reason is not included"));
  assert.ok(!text.includes("explicitly reports treatment held"));
  await opened.context.close();
}


async function retry404CancelsStalePoll(browser, url, state) {
  state.reset({
    meta: baseMeta(),
    nodes: [fieldNode({ display_name: "STALE POLL MUST NOT RENDER" })],
    delayNodesMs: 2200,
  });
  const { context, page } = await openDemoPage(browser, url);
  await chooseMode(page, "actual");
  await eventually(() => state.requests.includes("/api/nodes?source=field"), "race fixture never began field poll");
  state.meta = { error: "not found" };
  state.metaStatus = 404;
  await chooseMode(page, "demo");
  await chooseMode(page, "actual");
  await page.waitForFunction(() => document.body.innerText.includes("SETUP REQUIRED"));
  await page.locator('[data-testid="data-mode-chip"]:visible').first().click();
  await page.locator("button:visible", { hasText: "Retry now" }).first().click();
  await eventually(() => state.requests.filter((path) => path === "/api/meta").length >= 3, "Retry did not repeat the 404 handshake");
  await page.waitForTimeout(2400);
  const text = await page.locator("body").innerText();
  assert.ok(text.includes("SETUP REQUIRED"));
  assert.ok(!text.includes("STALE POLL MUST NOT RENDER"), "an old poll overwrote the 404 setup state");
  assert.ok(state.abortedRequests.includes("/api/nodes?source=field"), "404 retry race did not abort the old poll");
  assert.equal(state.requests.filter((path) => path.startsWith("/api/nodes")).length, 1, "404 retry started another telemetry poll");
  await context.close();
}


async function rapidMetaGenerationRace(browser, url, state) {
  state.reset({
    meta: baseMeta({ field_ingest_configured: false, field_ready: false }),
    delayMetaMs: 1200,
    nodes: [fieldNode({ display_name: "Newest generation field row" })],
  });
  const { context, page } = await openDemoPage(browser, url);
  await chooseMode(page, "actual");
  await eventually(() => state.requests.filter((path) => path === "/api/meta").length === 1, "first metadata generation did not start");
  await chooseMode(page, "demo");
  state.meta = baseMeta();
  state.delayMetaMs = 0;
  await chooseMode(page, "actual");
  await page.waitForFunction(() => document.body.innerText.includes("Newest generation field row"));
  await page.waitForTimeout(1400);
  const text = await page.locator("body").innerText();
  assert.ok(text.includes("AUTHENTICATED FIELD TELEMETRY"));
  assert.ok(!text.includes("SETUP REQUIRED"), "old metadata generation overwrote the rapid Actual re-entry");
  assert.equal(state.requests.filter((path) => path === "/api/meta").length, 2);
  await context.close();
}


async function toastIsolation(browser, url, state) {
  state.reset({ meta: baseMeta({ field_ingest_configured: false, field_ready: false }) });
  const { context, page } = await openDemoPage(browser, url);
  await page.locator('button[title="Settings"]:visible').click();
  await page.getByRole("button", { name: "Spray demo settings" }).click();
  await page.getByRole("button", { name: "Simulate" }).click();
  await page.waitForFunction(() => document.body.innerText.includes("Simulated test spray"));
  await chooseMode(page, "actual");
  await page.waitForFunction(() => document.body.innerText.includes("SETUP REQUIRED"));
  assert.ok(!(await page.locator("body").innerText()).includes("Simulated test spray"), "Demo toast survived the switch to Actual");
  await context.close();
}


async function pollingCancellation(browser, url, state) {
  state.reset({ meta: baseMeta(), nodes: [], delayNodesMs: 3000 });
  const { context, page } = await openDemoPage(browser, url);
  await chooseMode(page, "actual");
  await eventually(
    () => state.requests.some((path) => path.startsWith("/api/nodes")),
    "Actual never began its delayed telemetry poll",
  );
  await chooseMode(page, "demo");
  await eventually(
    () => state.abortedRequests.some((path) => path.startsWith("/api/nodes")),
    "switching to Demo did not abort the in-flight Actual poll",
  );
  const requestsAfterSwitch = state.requests.filter((path) => path.startsWith("/api/nodes")).length;
  await page.waitForTimeout(5500);
  assert.equal(
    state.requests.filter((path) => path.startsWith("/api/nodes")).length,
    requestsAfterSwitch,
    "Actual polling continued after switching to Demo",
  );
  await context.close();
}


async function pauseCancelsInFlightPoll(browser, url, state) {
  const lateRow = "PAUSE RACE LATE ROW MUST NOT RENDER";
  const secondNode = "Paused cached second node";
  state.reset({
    meta: baseMeta(),
    nodes: [
      fieldNode({ display_name: "Paused cached primary node" }),
      fieldNode({ device_id: "field-node-008", display_name: secondNode, block: "Zone 8", batt_pct: 76.4 }),
    ],
  });
  const { context, page } = await openDemoPage(browser, url);
  await chooseMode(page, "actual");
  await page.waitForFunction(() => document.body.innerText.includes("Paused cached primary node"));
  const initialNodeRequests = state.requests.filter((path) => path.startsWith("/api/nodes")).length;

  state.nodes = [fieldNode({ display_name: lateRow })];
  state.delayNodesMs = 3000;

  await page.locator('[data-testid="data-mode-chip"]:visible').first().click();
  await page.locator("button:visible", { hasText: "Retry now" }).first().click();
  await eventually(
    () => state.requests.filter((path) => path.startsWith("/api/nodes")).length > initialNodeRequests,
    "Actual never began the delayed poll used by the Pause race",
  );
  await page.getByRole("button", { name: "Pause refresh", exact: true }).filter({ visible: true }).first().click();
  await eventually(
    () => state.abortedRequests.includes("/api/nodes?source=field"),
    "Pause did not abort the in-flight Actual poll",
  );
  const requestsAtPause = state.requests.filter((path) => path.startsWith("/api/nodes")).length;

  await page.waitForTimeout(3300);
  const text = await page.locator("body").innerText();
  assert.ok(!text.includes(lateRow), "the aborted poll rendered its late field row after Pause");
  assert.match(
    await page.locator('[data-testid="data-mode-chip"]:visible').first().innerText(),
    /PAUSED/i,
    "Actual connection state did not remain PAUSED",
  );
  assert.equal(
    await page.getByRole("button", { name: "Resume refresh", exact: true }).filter({ visible: true }).first().getAttribute("aria-pressed"),
    "true",
    "Pause control did not remain pressed",
  );
  assert.equal(
    state.requests.filter((path) => path.startsWith("/api/nodes")).length,
    requestsAtPause,
    "Actual polling restarted while PAUSED",
  );

  await page.keyboard.press("Escape");
  await page.locator("button.bg-node-picker:visible").click();
  await page.getByRole("button", { name: new RegExp(`field-node-008.*${secondNode}`, "i") }).filter({ visible: true }).first().click();
  await page.waitForFunction((expected) => document.body.innerText.includes(expected), secondNode);
  await page.waitForTimeout(250);
  assert.match(
    await page.locator('[data-testid="data-mode-chip"]:visible').first().innerText(),
    /PAUSED/i,
    "selecting a cached node changed the PAUSED connection state",
  );
  assert.ok(!(await page.locator("body").innerText()).includes(lateRow), "cached-node selection rendered the aborted late row");
  assert.equal(
    state.requests.filter((path) => path.startsWith("/api/nodes")).length,
    requestsAtPause,
    "selecting a cached node restarted Actual polling while PAUSED",
  );
  await context.close();
}


async function checkEmbeddedLicenses(page) {
  const result = await page.evaluate(async () => {
    const expected = ["LICENSE", "vendor/LICENSES.txt", "vendor/fonts/LICENSES.txt"];
    const manifest = JSON.parse(document.querySelector("#bananaguard-build-manifest").textContent);
    const entries = Object.fromEntries(manifest.files.map((entry) => [entry.path, entry]));
    const anchors = [...document.querySelectorAll("a[href]")];
    const links = {
      LICENSE: anchors.find((candidate) => /Project license/i.test(candidate.textContent || "")),
      "vendor/LICENSES.txt": anchors.find((candidate) => /JavaScript licenses/i.test(candidate.textContent || "")),
      "vendor/fonts/LICENSES.txt": anchors.find((candidate) => /Font licenses/i.test(candidate.textContent || "")),
    };
    const texts = {};
    for (const path of expected) {
      const link = links[path];
      if (!link) throw new Error(`missing readable bundled license ${path}`);
      texts[path] = await (await fetch(link.href)).text();
      const binary = atob(entries[path].data);
      const bytes = new Uint8Array(binary.length);
      for (let i = 0; i < binary.length; i += 1) bytes[i] = binary.charCodeAt(i);
      const manifestText = new TextDecoder().decode(bytes);
      if (texts[path] !== manifestText) throw new Error(`readable license differs from manifest ${path}`);
    }
    return { texts, paths: manifest.files.map((entry) => entry.path) };
  });
  for (const path of ["LICENSE", "vendor/LICENSES.txt", "vendor/fonts/LICENSES.txt"]) {
    assert.ok(result.paths.includes(path), `standalone manifest omitted ${path}`);
  }
  assert.ok(result.texts.LICENSE.includes("Copyright (c) 2026 BananaGuard Team"));
  assert.ok(result.texts["vendor/LICENSES.txt"].includes("Copyright (c) Facebook, Inc. and its affiliates."));
  assert.ok(result.texts["vendor/LICENSES.txt"].includes("Modernizr 3.0.0pre (Custom Build) | MIT"));
  assert.ok(result.texts["vendor/fonts/LICENSES.txt"].includes("SIL OPEN FONT LICENSE Version 1.1"));
  assert.ok(result.texts["vendor/fonts/LICENSES.txt"].includes("Apache License"));
}


async function offlineStandalone(browser, standalone) {
  assert.ok(existsSync(standalone), `standalone artifact missing: ${standalone}`);
  const context = await browser.newContext({ offline: true, viewport: { width: 320, height: 844 } });
  await context.addInitScript(() => {
    try { localStorage.setItem("bg_data_mode", "demo"); } catch (_error) {}
  });
  const page = await context.newPage();
  const externalRequests = [];
  page.on("request", (request) => {
    if (/^https?:/i.test(request.url())) externalRequests.push(request.url());
  });
  const pageErrors = [];
  const consoleErrors = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("console", (message) => {
    if (message.type() === "error") consoleErrors.push(message.text());
  });
  const fileUrl = pathToFileURL(standalone).href;
  for (const width of [320, 390, 1280]) {
    await page.setViewportSize({ width, height: width < 700 ? 844 : 900 });
    await page.goto(fileUrl, { waitUntil: "domcontentloaded" });
    await waitForApp(page);
    await checkDimensions(page, width);
  }
  await checkFonts(page);
  await page.locator('button[title="Settings"]:visible').click();
  await page.getByRole("link", { name: "Open the BananaGuard project license" }).waitFor();
  await checkEmbeddedLicenses(page);
  const build = await page.evaluate(() => window.__BANANAGUARD_BUILD__);
  assert.match(build.build_id, /^[^-]+(?:-dirty)?-[0-9a-f]{16}$/);

  await page.evaluate(() => {
    const bundledFetch = window.fetch;
    window.__BG_FILE_API_FETCHES__ = [];
    window.fetch = (input, init) => {
      const raw = typeof input === "string" ? input : input?.url || String(input);
      if (/\/api\//.test(raw)) window.__BG_FILE_API_FETCHES__.push(raw);
      return bundledFetch(input, init);
    };
  });
  await chooseMode(page, "actual");
  await page.waitForFunction(() => document.body.innerText.includes("SETUP REQUIRED"));
  const text = await page.locator("body").innerText();
  assertNoDemoLeakage(text);
  assert.deepEqual(await page.evaluate(() => window.__BG_FILE_API_FETCHES__), [], "file Actual attempted an API request");
  assert.deepEqual(externalRequests, [], `offline standalone requested network assets: ${externalRequests.join(", ")}`);
  assert.deepEqual(pageErrors, [], `offline standalone page errors: ${pageErrors.join("; ")}`);
  assert.deepEqual(consoleErrors, [], `offline standalone console errors: ${consoleErrors.join("; ")}`);
  await context.close();
}


async function main() {
  const options = args(process.argv.slice(2));
  const fixture = createFixture(options.root);
  const port = await listen(fixture.server);
  const url = `http://127.0.0.1:${port}/index.html`;
  const browser = await chromium.launch({ headless: true });
  try {
    await responsiveDemoIsolation(browser, url, fixture.state);
    await staticOriginSetupRequired(browser, url, fixture.state);
    await setupRequiredIsolation(browser, url, fixture.state);
    await rejectedMetadata(
      browser,
      url,
      fixture.state,
      baseMeta({ read_only: false, capabilities: { remote_commands: true } }),
      "write-capable",
    );
    await rejectedMetadata(
      browser,
      url,
      fixture.state,
      baseMeta({ supported_payload_versions: [2] }),
      "payload-incompatible",
    );
    await readinessNotAttested(browser, url, fixture.state);
    await lowBatteryAndThresholdSemantics(browser, url, fixture.state);
    await successfulFieldMapping(browser, url, fixture.state);
    await limitedCapabilityTelemetry(browser, url, fixture.state);
    await mixedFleetFaultSemantics(browser, url, fixture.state);
    await nodeSelectionCancelsStaleRequests(browser, url, fixture.state);
    await failedDetailConfirmationPreservesData(browser, url, fixture.state);
    await staleAndFaultSemantics(browser, url, fixture.state);
    await heldAndLockoutSemantics(browser, url, fixture.state);
    await retry404CancelsStalePoll(browser, url, fixture.state);
    await rapidMetaGenerationRace(browser, url, fixture.state);
    await toastIsolation(browser, url, fixture.state);
    await pollingCancellation(browser, url, fixture.state);
    await pauseCancelsInFlightPoll(browser, url, fixture.state);
    assert.deepEqual(canonicalRuntimeErrors, [], `canonical dashboard runtime errors:\n${canonicalRuntimeErrors.join("\n")}`);
    await offlineStandalone(browser, options.standalone);
  } finally {
    await browser.close();
    await new Promise((resolveClose) => fixture.server.close(resolveClose));
  }
  console.log("dashboard smoke passed: Demo zero-request isolation and working settings; canonical runtime clean; setup and metadata gates; exact and limited-capability field mapping; truthful battery/threshold/FCnt/live-status semantics; fleet fault/freshness semantics; conservative detail confirmation and selection-race cancellation; stale/fault safety; Pause and mode-switch poll cancellation; responsive offline fonts/licenses; file Actual no-network setup");
}


main().catch((error) => {
  console.error(error.stack || error);
  process.exitCode = 1;
});
