"""Tests for the VIZ-03 offline HTML artifact builder."""

from __future__ import annotations

import json
import re

import pytest

from gestaltdb.graphdb import Edge, Node
from gestaltdb.viz.html import STATIC_DIR, build_html, is_bundle_fresh, save_html
from gestaltdb.viz.ir import VizGraph


def _viz():
    return VizGraph.from_nodes_edges(
        [Node(node_id="alice", labels=["Person"], properties={"name": "Alice"})],
        [],
    )


def test_build_html_inlines_bundle_and_payload():
    document = build_html(_viz(), title="Drug graph")
    assert "<title>Drug graph</title>" in document
    assert "window.__GESTALTDB_VIZ__" in document
    bundle_js = (STATIC_DIR / "gestaltdb-viz.js").read_text(encoding="utf-8")
    assert bundle_js in document
    assert '<div id="root"></div>' in document


def test_build_html_shell_has_no_external_references():
    document = build_html(_viz())
    head, _, _ = document.partition("window.__GESTALTDB_VIZ__")
    assert "http://" not in head
    assert "https://" not in head
    assert "src=" not in head and "href=" not in head


def test_xss_probe_is_inert_and_payload_parses():
    evil = '</script><script>alert("pwned")</script>'
    viz = VizGraph.from_nodes_edges(
        [Node(node_id="n1", labels=[evil], properties={"p": evil})],
        [Edge(edge_id="e1", source="n1", target="n1", properties={"type": evil})],
    )
    document = build_html(viz)
    assert "</script><script>" not in document
    match = re.search(r"window\.__GESTALTDB_VIZ__ = (.*?);window\.__GESTALTDB_VIZ_OPTIONS__", document, re.DOTALL)
    assert match is not None
    payload = json.loads(match.group(1))
    assert payload["nodes"][0]["labels"] == [evil]
    assert payload["edges"][0]["type"] == evil


def test_title_is_escaped():
    document = build_html(_viz(), title='<b>"quoted" & raw</b>')
    assert "<b>" not in document
    assert "&lt;b&gt;" in document


def test_save_html_round_trip(tmp_path):
    viz = _viz()
    first = save_html(viz, tmp_path / "nested" / "graph.html", title="T")
    second = save_html(viz, tmp_path / "nested" / "graph.html", title="T")
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8") == build_html(viz, title="T")
    assert first.is_absolute()


def test_save_html_rejects_non_payload():
    with pytest.raises(TypeError):
        save_html(object(), "/tmp/never-written.html")


def test_bundle_fresh_against_manifest():
    assert is_bundle_fresh() is True


def test_stale_bundle_warns(tmp_path, monkeypatch):
    import gestaltdb.viz.html as html_module

    monkeypatch.setattr(html_module, "STATIC_DIR", tmp_path)
    (tmp_path / "gestaltdb-viz.js").write_text("stale", encoding="utf-8")
    (tmp_path / "gestaltdb-viz.css").write_text("stale", encoding="utf-8")
    (tmp_path / "viz-manifest.json").write_text(
        json.dumps({"bundle_js_sha256": "x", "bundle_css_sha256": "y"}), encoding="utf-8"
    )
    assert html_module.is_bundle_fresh() is False
    with pytest.warns(RuntimeWarning, match="does not match viz-manifest"):
        html_module.build_html(_viz())
