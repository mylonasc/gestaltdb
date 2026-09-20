"""Tests for VIZ-08 packaging: the prebuilt bundle ships with the wheel/sdist."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_package_data_declares_viz_static():
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert "viz/static/*" in pyproject


def test_static_dir_matches_manifest():
    import hashlib
    import json

    static = ROOT / "src" / "gestaltdb" / "viz" / "static"
    manifest = json.loads((static / "viz-manifest.json").read_text(encoding="utf-8"))
    for key, filename in (("bundle_js_sha256", "gestaltdb-viz.js"), ("bundle_css_sha256", "gestaltdb-viz.css")):
        assert hashlib.sha256((static / filename).read_bytes()).hexdigest() == manifest[key]


def test_importable_without_js_toolchain():
    # gestaltdb.viz resolves its static dir relative to the package, so it
    # works from any install layout (checkout, wheel, sdist) with no npm.
    from gestaltdb.viz import html as html_module

    assert html_module.STATIC_DIR.is_dir()
    assert (html_module.STATIC_DIR / "gestaltdb-viz.js").is_file()
