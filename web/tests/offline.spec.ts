import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, readFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";

let directory: string;
let artifact: string;
test.beforeAll(() => {
  directory = mkdtempSync(join(tmpdir(), "gestaltdb-viz-"));
  artifact = join(directory, "graph.html");
  const html = execFileSync(process.env.VIZ_TEST_PYTHON ?? "python3", ["scripts/browser_fixture.py"], {
    env: { ...process.env, PYTHONPATH: resolve("../src") }, encoding: "utf8",
  });
  writeFileSync(artifact, html);
});
test.afterAll(() => rmSync(directory, { recursive: true, force: true }));

test.beforeEach(async ({ page, context }) => {
  await context.setOffline(true);
  await page.goto(pathToFileURL(artifact).href);
  await expect(page.locator(".node")).toHaveCount(3);
  await page.getByRole("button", { name: "Pause layout" }).click();
  // The initial viewport fit is a 250ms D3 transition.
  await page.waitForTimeout(300);
});

test("labels and every relationship remain independently inspectable offline", async ({ page }) => {
  await expect(page.locator(".node-label").filter({ hasText: "Alice" })).toBeVisible();
  const paths = await page.locator(".edge").evaluateAll(elements => elements.map(element => element.getAttribute("d")));
  expect(paths).toHaveLength(6);
  expect(new Set(paths).size).toBe(6);
  await page.getByRole("button", { name: "Edge loop (SELF)", exact: true }).focus();
  await page.keyboard.press("Enter");
  await expect(page.getByRole("heading", { name: "Edge inspector" })).toBeVisible();
  await expect(page.locator(".gdviz-props").last()).toContainText("0.75");
  await page.getByLabel("Edge labels", { exact: true }).selectOption("selected");
  await expect(page.locator(".edge-label:visible")).toHaveCount(1);
});

test("dragging while paused updates node and incident-edge positions", async ({ page }) => {
  const node = page.getByRole("button", { name: "Node a", exact: true });
  const before = await node.getAttribute("transform");
  const edgeBefore = await page.locator(".edge").first().getAttribute("d");
  const bounds = (await node.boundingBox())!;
  await page.mouse.move(bounds.x + bounds.width / 2, bounds.y + bounds.height / 2);
  await page.mouse.down();
  await page.mouse.move(bounds.x + 100, bounds.y + 50, { steps: 5 });
  await page.mouse.up();
  expect(await node.getAttribute("transform")).not.toBe(before);
  expect(await page.locator(".edge").first().getAttribute("d")).not.toBe(edgeBefore);
});

test("fit-visible and resize keep filtered content in the canvas", async ({ page }) => {
  await page.getByLabel("Toggle node group Person", { exact: true }).uncheck();
  await expect(page.locator(".node:visible")).toHaveCount(1);
  await expect(page.locator(".edge:visible")).toHaveCount(0);
  await page.getByRole("button", { name: "Fit visible graph" }).click();
  await page.waitForTimeout(300);
  await page.setViewportSize({ width: 1000, height: 800 });
  await page.waitForTimeout(300);
  const canvas = (await page.locator(".gdviz-canvas").boundingBox())!;
  const node = (await page.getByRole("button", { name: "Node c", exact: true }).boundingBox())!;
  expect(node.x).toBeGreaterThan(canvas.x);
  expect(node.x + node.width).toBeLessThan(canvas.x + canvas.width);
  expect(node.y).toBeGreaterThan(canvas.y);
  expect(node.y + node.height).toBeLessThan(canvas.y + canvas.height);
});

test("dark SVG and PNG downloads work under the production CSP", async ({ page }) => {
  const violations: string[] = [];
  page.on("console", message => { if (message.type() === "error") violations.push(message.text()); });
  await page.getByRole("button", { name: "Dark theme" }).click();
  await page.getByText("Export and saved view", { exact: true }).click();
  const svgDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export SVG", exact: true }).click();
  const svg = await svgDownload;
  const text = readFileSync((await svg.path())!, "utf8");
  expect(text).toContain("Alice");
  expect(text).toContain("rgb(232, 232, 230)");
  expect(text).not.toContain("edge-hit");
  const pngDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export PNG", exact: true }).click();
  const png = readFileSync((await (await pngDownload).path())!);
  expect(png.subarray(0, 8).toString("hex")).toBe("89504e470d0a1a0a");
  expect(violations).toEqual([]);
});

test("custom transparent exports fit visible content and retain requested dimensions", async ({ page }) => {
  await page.getByText("Export and saved view", { exact: true }).click();
  await page.getByLabel("Image scope", { exact: true }).selectOption("visible");
  await page.getByLabel("Image background", { exact: true }).selectOption("transparent");
  await page.getByLabel("Image width", { exact: true }).fill("640");
  await page.getByLabel("Image height", { exact: true }).fill("480");
  await page.getByLabel("PNG scale", { exact: true }).selectOption("1");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export SVG", exact: true }).click();
  const svg = readFileSync((await (await download).path())!, "utf8");
  expect(svg).toContain('width="640"');
  expect(svg).toContain('height="480"');
  expect(svg).not.toContain("<rect");
  expect(svg).not.toContain('class="viewport" transform=');
  const pngDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export PNG", exact: true }).click();
  const png = readFileSync((await (await pngDownload).path())!);
  expect(png.readUInt32BE(16)).toBe(640);
  expect(png.readUInt32BE(20)).toBe(480);
  // Decode with the same offline browser to verify an actually transparent background.
  const alpha = await page.evaluate(async data => {
    const image = new Image(); image.src = `data:image/png;base64,${data}`;
    await image.decode();
    const canvas = document.createElement("canvas"); canvas.width = image.width; canvas.height = image.height;
    const context = canvas.getContext("2d")!; context.drawImage(image, 0, 0);
    return context.getImageData(0, 0, 1, 1).data[3];
  }, png.toString("base64"));
  expect(alpha).toBe(0);
  await page.getByLabel("Image background", { exact: true }).selectOption("theme");
  const opaqueDownload = page.waitForEvent("download");
  await page.getByRole("button", { name: "Export SVG", exact: true }).click();
  const opaque = readFileSync((await (await opaqueDownload).path())!, "utf8").replace(/background-color:[^;"']*;?/g, "");
  const corner = await page.evaluate(async data => {
    const image = new Image(); image.src = `data:image/svg+xml;base64,${data}`;
    await image.decode();
    const canvas = document.createElement("canvas"); canvas.width = image.width; canvas.height = image.height;
    const context = canvas.getContext("2d")!; context.drawImage(image, 0, 0);
    return [...context.getImageData(0, 0, 1, 1).data];
  }, Buffer.from(opaque).toString("base64"));
  expect(corner).toEqual([255, 255, 255, 255]);
});

test("saved views restore coordinates, pins, zoom, settings and filters atomically", async ({ page }) => {
  await page.getByText("Export and saved view", { exact: true }).click();
  await page.waitForTimeout(300);
  await page.getByLabel("Select visible entity", { exact: true }).selectOption(JSON.stringify(["node", "a"]));
  await page.getByRole("button", { name: "Pin selected node", exact: true }).click();
  await page.getByRole("button", { name: "Dark theme" }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Save view state", exact: true }).click();
  const saved = readFileSync((await (await download).path())!, "utf8");
  const state = JSON.parse(saved);
  const transform = await page.getByRole("button", { name: "Node a", exact: true }).getAttribute("transform");
  await page.getByRole("button", { name: "Light theme" }).click();
  await page.getByRole("button", { name: "Reset layout" }).click();
  await page.getByLabel("Toggle node group Person", { exact: true }).uncheck();
  await page.getByLabel("Load view state", { exact: true }).setInputFiles({ name: "view.json", mimeType: "application/json", buffer: Buffer.from(saved) });
  await expect(page.getByRole("status")).toContainText("View state restored");
  await expect(page.locator(".gdviz")).toHaveAttribute("data-theme", "dark");
  await expect(page.getByRole("button", { name: "Node a", exact: true })).toHaveAttribute("transform", transform!);
  await expect(page.getByRole("button", { name: "Unpin selected node", exact: true })).toBeVisible();
  const zoom = await page.locator(".viewport").getAttribute("transform");
  expect(zoom).toBe(`translate(${state.layout.zoom.x},${state.layout.zoom.y}) scale(${state.layout.zoom.k})`);
  state.layout.nodes[0].x = "invalid";
  state.settings.theme = "light";
  await page.getByLabel("Load view state", { exact: true }).setInputFiles({ name: "bad.json", mimeType: "application/json", buffer: Buffer.from(JSON.stringify(state)) });
  await expect(page.getByRole("alert")).toContainText("finite numbers");
  await expect(page.locator(".gdviz")).toHaveAttribute("data-theme", "dark");
});

test("property search, secondary labels, directed focus and styling are usable offline", async ({ page }) => {
  await page.getByText("Search and neighborhood", { exact: true }).click();
  await page.getByLabel("Search nodes", { exact: true }).fill("Alice");
  await expect(page.locator(".node:visible")).toHaveCount(1);
  await page.getByLabel("Search nodes", { exact: true }).fill("");
  await page.getByLabel("Native label", { exact: true }).selectOption("Partner");
  await expect(page.locator(".node:visible")).toHaveCount(1);
  await page.getByLabel("Native label", { exact: true }).selectOption("");
  await page.getByLabel("Select visible entity", { exact: true }).selectOption(JSON.stringify(["node", "a"]));
  await page.getByRole("button", { name: "Focus neighborhood", exact: true }).click();
  await page.getByLabel("Focus direction", { exact: true }).selectOption("out");
  await expect(page.locator(".node:visible")).toHaveCount(2);
  await page.getByLabel("Focus hops", { exact: true }).selectOption("2");
  await expect(page.locator(".node:visible")).toHaveCount(3);
  await page.getByLabel("Toggle edge type LINK", { exact: true }).uncheck();
  await expect(page.locator(".node:visible")).toHaveCount(2);
  await page.getByText("Labels and attribute styling", { exact: true }).click();
  await page.getByLabel("Node size property", { exact: true }).selectOption("mass");
  await expect(page.getByRole("button", { name: "Node a", exact: true }).locator("circle")).toHaveAttribute("r", "4");
  await expect(page.getByRole("button", { name: "Node b", exact: true }).locator("circle")).toHaveAttribute("r", "24");
  await page.getByLabel("Edge width property", { exact: true }).selectOption("score");
  const widths = await page.locator(".edge").evaluateAll(edges => edges.map(edge => Number(edge.getAttribute("stroke-width"))));
  expect(widths).toContain(6);
  await page.setViewportSize({ width: 500, height: 800 });
  await page.getByRole("button", { name: "Hide inspector", exact: true }).click();
  await expect(page.getByRole("complementary")).toHaveCount(0);
  await page.getByRole("button", { name: "Show inspector", exact: true }).click();
  await expect(page.getByRole("complementary")).toBeVisible();
});

test("visible-only simulation membership survives saved views and excludes disabled links", async ({ page }) => {
  await page.getByLabel("Toggle edge type LINK", { exact: true }).uncheck();
  await page.getByLabel("Toggle node group Hidden", { exact: true }).uncheck();
  await page.getByRole("button", { name: "Relayout visible graph", exact: true }).click();
  // Changing force controls must not bring filtered relationships back into the simulation.
  await page.getByLabel("Link distance", { exact: true }).focus();
  await page.keyboard.press("ArrowRight");
  await page.getByText("Export and saved view", { exact: true }).click();
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Save view state", exact: true }).click();
  const data = readFileSync((await (await download).path())!, "utf8");
  const state = JSON.parse(data);
  expect(state.layout.simulation.nodes).toEqual(["a", "b"]);
  expect(state.layout.simulation.edges).not.toContain("link");
  await page.getByRole("button", { name: "Reset layout", exact: true }).click();
  await page.getByLabel("Load view state", { exact: true }).setInputFiles({ name: "view.json", mimeType: "application/json", buffer: Buffer.from(data) });
  await expect(page.getByRole("status")).toContainText("View state restored");
  const restored = page.waitForEvent("download");
  await page.getByRole("button", { name: "Save view state", exact: true }).click();
  const restoredState = JSON.parse(readFileSync((await (await restored).path())!, "utf8"));
  expect(restoredState.layout.simulation).toEqual(state.layout.simulation);
});

test("export limits provide feedback and full dependency notices are downloadable", async ({ page }) => {
  await page.getByText("Export and saved view", { exact: true }).click();
  await page.getByLabel("Image width", { exact: true }).fill("8192");
  await page.getByLabel("Image height", { exact: true }).fill("8192");
  await page.getByLabel("PNG scale", { exact: true }).selectOption("4");
  await page.getByRole("button", { name: "Export PNG", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("32 megapixels");
  const download = page.waitForEvent("download");
  await page.getByRole("button", { name: "Download third-party notices", exact: true }).click();
  const notices = readFileSync((await (await download).path())!, "utf8");
  expect(notices).toContain("react 18.3.1");
  expect(notices).toContain("Copyright 2010-2023 Mike Bostock");
  expect(notices).toContain("Permission is hereby granted");
});
