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
