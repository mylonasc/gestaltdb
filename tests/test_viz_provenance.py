"""Build provenance must detect changed inputs without requiring Node at runtime."""
import importlib.util
import json
import shutil
from pathlib import Path

import pytest

from gestaltdb.viz import html

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.parametrize("change", ["source", "new-file", "notices"])
def test_checkout_freshness_detects_changed_sources_and_notices(tmp_path, monkeypatch, change):
    original = html.STATIC_DIR
    manifest = html.read_manifest()
    static = tmp_path / "src" / "gestaltdb" / "viz" / "static"
    shutil.copytree(original, static)
    for relative in manifest["source_files"]:
        destination = tmp_path / "web" / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / "web" / relative, destination)
    monkeypatch.setattr(html, "STATIC_DIR", static)
    assert html.is_bundle_fresh()
    if change == "source":
        source = tmp_path / "web" / "src" / "App.tsx"
        source.write_text(source.read_text() + "\n// changed\n")
    elif change == "new-file":
        (tmp_path / "web" / "src" / "extra.ts").write_text("export const changed = true;\n")
    else:
        (static / html.NOTICES_NAME).write_text("changed notices")
    assert not html.is_bundle_fresh()


def test_installed_assets_verify_without_frontend_sources(tmp_path, monkeypatch):
    static = tmp_path / "installed" / "gestaltdb" / "viz" / "static"
    shutil.copytree(html.STATIC_DIR, static)
    monkeypatch.setattr(html, "STATIC_DIR", static)
    assert html.is_bundle_fresh()


def test_manifest_records_resolved_versions_and_embedded_notices():
    manifest = html.read_manifest()
    lock = json.loads((ROOT / "web" / "package-lock.json").read_text())
    for name, key in (("d3", "d3_version"), ("react", "react_version"), ("react-dom", "react_dom_version")):
        assert manifest[key] == lock["packages"][f"node_modules/{name}"]["version"]
    assert manifest["toolchain"]["node"].startswith("v")
    document = html.build_html({"version": 1, "nodes": [], "edges": []})
    assert 'id="gdviz-notices"' in document
    assert "Permission is hereby granted" in document


def license_checker(tmp_path, monkeypatch, entries, dependencies=None):
    spec = importlib.util.spec_from_file_location("viz_license_checker", ROOT / "web" / "scripts" / "check_licenses.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "WEB_DIR", tmp_path)
    (tmp_path / "package.json").write_text(json.dumps({"dependencies": dependencies or {"example": "1"}}))
    (tmp_path / "package-lock.json").write_text(json.dumps({"packages": {"": {}, **entries}}))
    return module


def test_license_gate_includes_nested_versions_and_scoped_packages(tmp_path, monkeypatch, capsys):
    entries = {"node_modules/example": {"version": "1", "license": "MIT"},
               "node_modules/example/node_modules/example": {"version": "2", "license": "GPL-3.0"},
               "node_modules/@scope/pkg": {"version": "1", "license": "MIT"}}
    checker = license_checker(tmp_path, monkeypatch, entries)
    assert set(checker.iter_lockfile_packages()) == set(entries)
    assert checker.main(["--lockfile"]) == 1
    assert "node_modules/example/node_modules/example" in capsys.readouterr().err


def test_license_gate_rejects_missing_direct_or_installed_dependencies(tmp_path, monkeypatch):
    checker = license_checker(tmp_path, monkeypatch, {})
    with pytest.raises(ValueError, match="missing from lockfile"):
        checker.iter_lockfile_packages()
    checker = license_checker(tmp_path, monkeypatch, {"node_modules/example": {"version": "1", "license": "MIT"}})
    with pytest.raises(FileNotFoundError):
        checker.iter_production_packages()
