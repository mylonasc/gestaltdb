import { test, expect } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync, writeFileSync, rmSync } from "node:fs";
import { tmpdir } from "node:os";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";

let directory: string;
let artifact: string;
test.beforeAll(() => {
  directory = mkdtempSync(join(tmpdir(), "gestaltdb-viz-tree-"));
  artifact = join(directory, "proof.html");
  const html = execFileSync(process.env.VIZ_TEST_PYTHON ?? "python3", ["scripts/browser_proof_fixture.py"], {
    env: { ...process.env, PYTHONPATH: resolve("../src") }, encoding: "utf8",
  });
  writeFileSync(artifact, html);
});
test.afterAll(() => rmSync(directory, { recursive: true, force: true }));

test.beforeEach(async ({ page, context }) => {
  await context.setOffline(true);
  await page.goto(pathToFileURL(artifact).href);
  await expect(page.locator(".node")).toHaveCount(5);
  // The initial viewport fit is a 250ms D3 transition.
  await page.waitForTimeout(300);
});

function yOf(transform: string | null): number {
  return Number(transform!.replace("translate(", "").replace(")", "").split(",")[1]);
}

test("reasoning artifacts open in a deterministic pinned tree without force layout", async ({ page }) => {
  // No clicks: payload meta suggested the tree arrangement on load.
  const conclusion = 'Node ["a","ancestor","c"]';
  const premise = 'Node ["a","parent","b"]';
  const conclusionY = yOf(await page.getByRole("button", { name: conclusion }).getAttribute("transform"));
  const premiseY = yOf(await page.getByRole("button", { name: premise }).getAttribute("transform"));
  // SVG y grows downward: inputs on top rows, the conclusion at the bottom.
  expect(premiseY).toBeLessThan(conclusionY);
  // Shape vocabulary: input facts are circles, derived facts are triangles,
  // and every inference step renders dotted.
  await expect(page.locator(".node polygon")).toHaveCount(3);
  await expect(page.locator(".node circle")).toHaveCount(2);
  const dashes = await page.locator(".edge").evaluateAll(edges => edges.map(edge => edge.getAttribute("stroke-dasharray")));
  expect(dashes).toHaveLength(4);
  expect(dashes.every(dash => dash === "6 4")).toBe(true);
  // Reset layout hands control back to the force simulation (unpinned, reseeded).
  await page.getByRole("button", { name: "Reset layout" }).click();
  await page.waitForTimeout(300);
  // Tree layout re-pins the same deterministic arrangement on demand.
  await page.getByRole("button", { name: "Tree layout" }).click();
  await page.waitForTimeout(300);
  const afterY = yOf(await page.getByRole("button", { name: conclusion }).getAttribute("transform"));
  const afterPremiseY = yOf(await page.getByRole("button", { name: premise }).getAttribute("transform"));
  expect(afterPremiseY).toBeLessThan(afterY);
  expect(afterY).toBe(conclusionY);
});

test("shape visibility toggles hide whole node layers", async ({ page }) => {
  await expect(page.locator(".node")).toHaveCount(5);
  await page.getByLabel("Toggle node shape triangle", { exact: true }).uncheck();
  await expect(page.locator(".node:visible")).toHaveCount(2);
  await expect(page.locator(".node:visible polygon")).toHaveCount(0);
  await page.getByLabel("Toggle node shape triangle", { exact: true }).check();
  await expect(page.locator(".node:visible")).toHaveCount(5);
  await page.getByLabel("Toggle node shape circle", { exact: true }).uncheck();
  await expect(page.locator(".node:visible")).toHaveCount(3);
});

test("releasing a pinned tree node eases instead of flinging", async ({ page }) => {
  const target = page.getByRole("button", { name: 'Node ["a","ancestor","c"]' });
  const start = await target.getAttribute("transform");
  await target.dblclick();
  let peak = 0;
  let previous = start;
  for (let i = 0; i < 8; i++) {
    await page.waitForTimeout(300);
    const current = await target.getAttribute("transform");
    const [px, py] = previous!.replace("translate(", "").replace(")", "").split(",").map(Number);
    const [cx, cy] = current!.replace("translate(", "").replace(")", "").split(",").map(Number);
    peak = Math.max(peak, Math.hypot(cx - px, cy - py));
    previous = current;
  }
  const [sx, sy] = start!.replace("translate(", "").replace(")", "").split(",").map(Number);
  const [ex, ey] = previous!.replace("translate(", "").replace(")", "").split(",").map(Number);
  // A reheated force layout glides; it never catapults across the canvas.
  expect(peak).toBeLessThan(200);
  expect(Math.hypot(ex - sx, ey - sy)).toBeLessThan(400);
});
