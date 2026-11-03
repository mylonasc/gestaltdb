"""Check rendered documentation table widths with an optional Playwright browser.

Example: uv run --with playwright python scripts/check_docs_layout.py docs/_build/html
Uses installed Chrome when available; otherwise install Playwright's Chromium.
This authoring-time check does not add a library or Sphinx build dependency.
"""

from __future__ import annotations

import argparse
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
from threading import Thread


PAGES = ["index", "quickstart", "storage-backends", "serializers", "data-access",
         "ingestion", "cypher-tutorial", "typed-sampling", "performance"]


class QuietHandler(SimpleHTTPRequestHandler):
    def log_message(self, format, *args):
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("html", type=Path, help="Sphinx HTML output directory")
    parser.add_argument("--screenshots", type=Path, help="optional viewport screenshot directory")
    args = parser.parse_args()
    for page in PAGES:
        if not (args.html / f"{page}.html").is_file():
            parser.error(f"missing built page: {page}.html")
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        parser.error("install the authoring extra with: uv run --with playwright python scripts/check_docs_layout.py ...")
    if args.screenshots:
        args.screenshots.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", 0), partial(QuietHandler, directory=str(args.html.resolve())))
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_port}"
    failures = []
    checked = 0
    try:
        with sync_playwright() as playwright:
            chrome = shutil.which("google-chrome") or shutil.which("chromium")
            browser = playwright.chromium.launch(headless=True, **({"executable_path": chrome} if chrome else {}))
            try:
                page = browser.new_page()
                # Layout must work without third-party script/font/CDN access.
                page.route("**/*", lambda route: route.continue_() if route.request.url.startswith(base + "/") else route.abort())
                for width in (1366, 900, 390):
                    page.set_viewport_size({"width": width, "height": 900})
                    for name in PAGES:
                        page.goto(f"{base}/{name}.html", wait_until="networkidle")
                        tables = page.locator(".wy-table-responsive table.docutils")
                        for index in range(tables.count()):
                            unprepared = tables.nth(index).evaluate("""table => {
                                const simple = table.querySelectorAll('thead tr').length === 1 &&
                                    table.querySelectorAll('tbody tr').length > 0 &&
                                    [...table.querySelectorAll('thead th')].every(cell => cell.textContent.trim()) &&
                                    [...table.querySelectorAll('th,td')].every(cell => cell.colSpan === 1 && cell.rowSpan === 1);
                                return simple && !table.classList.contains('responsive-cards');
                            }""")
                            if unprepared:
                                failures.append(f"{name} at {width}px: simple table {index} was not prepared")
                            issue = tables.nth(index).evaluate("""table => {
                                const bounds = table.getBoundingClientRect();
                                const parent = table.closest('.wy-table-responsive').getBoundingClientRect();
                                return bounds.width > parent.width + 2 || table.scrollWidth > table.clientWidth + 2;
                            }""")
                            if issue:
                                failures.append(f"{name} at {width}px: table {index} overflows")
                            if width <= 600:
                                missing_labels = tables.nth(index).evaluate("""table => {
                                    if (!table.classList.contains('responsive-cards')) return false;
                                    return [...table.querySelectorAll('tbody td')].some(cell => {
                                        const label = cell.querySelector('.mobile-column-label');
                                        return !label || !label.textContent.trim() || getComputedStyle(label).display === 'none';
                                    });
                                }""")
                                if missing_labels:
                                    failures.append(f"{name} at {width}px: table {index} lacks mobile labels")
                            checked += 1
                        if page.evaluate("document.documentElement.scrollWidth > innerWidth + 2"):
                            failures.append(f"{name} at {width}px: page overflows viewport")
                        if args.screenshots:
                            if tables.count():
                                tables.first.locator("tbody td").first.scroll_into_view_if_needed()
                            page.screenshot(path=str(args.screenshots / f"{name}-{width}.png"))
                        print(f"{name}: {width}px, {tables.count()} tables")
            finally:
                browser.close()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
    if failures:
        print("\n".join(failures))
        return 1
    print(f"Layout passed: {checked} table checks across {len(PAGES)} pages and 3 viewports.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
