"""Tests for the VIZ-02 committed JS bundle and license gate.

These tests need neither npm nor network: they verify the checked-in
artifacts under ``src/gestaltdb/viz/static/`` and gate the committed
``web/package-lock.json`` licenses with the stdlib-only checker.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC_DIR = ROOT / "src" / "gestaltdb" / "viz" / "static"
WEB_DIR = ROOT / "web"


def test_committed_bundle_exists():
    assert (STATIC_DIR / "gestaltdb-viz.js").is_file()
    assert (STATIC_DIR / "gestaltdb-viz.css").is_file()
    assert (STATIC_DIR / "viz-manifest.json").is_file()
    assert (STATIC_DIR / "gestaltdb-viz.js").stat().st_size > 10_000


def test_manifest_hashes_match_bundle():
    manifest = json.loads((STATIC_DIR / "viz-manifest.json").read_text())
    assert manifest["bundle_js"] == "gestaltdb-viz.js"
    assert manifest["bundle_css"] == "gestaltdb-viz.css"
    for key, filename in (("bundle_js_sha256", "gestaltdb-viz.js"), ("bundle_css_sha256", "gestaltdb-viz.css")):
        digest = hashlib.sha256((STATIC_DIR / filename).read_bytes()).hexdigest()
        assert manifest[key] == digest, filename


def test_bundle_has_no_external_requests():
    bundle = (STATIC_DIR / "gestaltdb-viz.js").read_text(errors="replace")
    lowered = bundle.lower()
    # Namespace constants (SVG/MathML) and React error-decoder links are
    # inert strings, not requests; assert on real fetch vectors instead.
    for marker in ("cdn.", "googleapis", "unpkg", "jsdelivr", "xmlhttprequest", "websocket", "eventsource("):
        assert marker not in lowered, marker


def test_license_gate_passes_from_lockfile():
    completed = subprocess.run(
        [sys.executable, "scripts/check_licenses.py", "--lockfile"],
        cwd=WEB_DIR,
        capture_output=True,
        text=True,
    )
    assert completed.returncode == 0, completed.stderr + completed.stdout


def test_web_source_declares_permissive_direct_deps():
    pkg = json.loads((WEB_DIR / "package.json").read_text())
    assert pkg["license"] == "MIT"
    assert set(pkg["dependencies"]) == {"d3", "react", "react-dom"}
