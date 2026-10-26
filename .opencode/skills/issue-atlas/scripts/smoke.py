#!/usr/bin/env python3
"""Optional real-browser smoke: uv run --with playwright python SKILL/scripts/smoke.py HTML."""

import argparse
from pathlib import Path


def main():
    from playwright.sync_api import sync_playwright

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path)
    parser.add_argument("--screenshot", type=Path)
    args = parser.parse_args()
    errors = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": 1600, "height": 1000})
        page.on("pageerror", lambda error: errors.append(str(error)))
        # A self-contained export must not require network access to function.
        page.route("https://**/*", lambda route: route.abort())
        page.goto(args.html.resolve().as_uri())
        page.wait_for_selector(".node")
        page.locator("#freeze").click()
        total = page.locator(".node").count()
        assert total > 0
        number = page.evaluate("DATA.links.find(e=>e.kind==='dependency')?.target || DATA.nodes[0].id")
        page.locator("#search").fill("#" + number)
        page.locator(".issue-row").filter(has_text="#" + number + " ").first.click()
        assert "direct prerequisites" in page.locator("#details").inner_text().lower()
        assert page.locator(".node").evaluate_all(
            "nodes => nodes.some(n => Number(n.getAttribute('opacity')) === 1)")
        assert "Full dependency chain" in page.locator("#details").inner_text()
        page.locator("#references").check()
        with page.expect_download() as download:
            page.locator("#export").click()
        assert download.value.suggested_filename.endswith(".svg")
        closed_count = page.evaluate("DATA.nodes.filter(n=>n.state==='closed').length")
        page.locator("#closed").uncheck()
        assert page.locator(".node").count() == total - closed_count
        page.locator("#closed").check()
        page.locator("#search").fill("")
        roadmap_count = page.evaluate("DATA.nodes.filter(n=>n.kind!=='issue').length")
        page.locator("#scope").select_option("roadmap")
        assert page.locator(".node").count() == roadmap_count
        page.locator("#scope").select_option("all")
        page.locator("#references").uncheck()
        page.locator("#search").fill("#" + number)
        page.locator(".issue-row").filter(has_text="#" + number + " ").first.click()
        page.locator("#fit-chain").click()
        # Allow the zoom transition to finish before capturing the export.
        page.wait_for_function("document.querySelector('#graph').__transition === undefined")
        if args.screenshot:
            page.screenshot(path=str(args.screenshot), full_page=True)
        assert errors == [], errors
        browser.close()
    print(f"PASS: {total} nodes; offline selection, highlighting, filters, SVG export; no JS errors")


if __name__ == "__main__":
    main()
