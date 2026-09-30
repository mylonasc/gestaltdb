"""Self-contained offline HTML artifacts (VIZ-03).

Builds shareable ``.html`` files that inline the committed JS/CSS bundle
plus the visualization payload. Standard library only: no new runtime
dependency, no network, no template engine.
"""

from __future__ import annotations

import hashlib
import html as html_module
import json
import warnings
from pathlib import Path
from typing import Any, Mapping

STATIC_DIR = Path(__file__).resolve().parent / "static"
BUNDLE_JS_NAME = "gestaltdb-viz.js"
BUNDLE_CSS_NAME = "gestaltdb-viz.css"
MANIFEST_NAME = "viz-manifest.json"
NOTICES_NAME = "third-party-notices.txt"


def _source_is_fresh(manifest: Mapping[str, Any]) -> bool:
    """Check checkout build inputs; installed wheels have no web/ source tree."""
    web = STATIC_DIR.parent.parent.parent.parent / "web"
    if not (web / "package.json").is_file():
        return True
    paths = ["package.json", "package-lock.json", "tsconfig.json", "vite.config.ts", "index.html",
             "scripts/build_inputs.mjs", "scripts/write_manifest.mjs", "scripts/check_licenses.py"]
    paths.extend(path.relative_to(web).as_posix() for path in (web / "src").rglob("*") if path.is_file())
    try:
        hashes = {path: hashlib.sha256((web / path).read_bytes()).hexdigest() for path in sorted(paths)}
    except OSError:
        return False
    digest = hashlib.sha256("".join(f"{path}\0{value}\n" for path, value in hashes.items()).encode()).hexdigest()
    return hashes == manifest.get("source_files") and digest == manifest.get("source_sha256")


def read_manifest() -> dict[str, Any]:
    """Return the committed bundle manifest.

    Raises:
        FileNotFoundError: If the manifest is absent (e.g. source checkout
            where ``web/`` was never built).
    """
    path = STATIC_DIR / MANIFEST_NAME
    return json.loads(path.read_text(encoding="utf-8"))


def is_bundle_fresh() -> bool:
    """Verify asset hashes and, in a checkout, frontend source provenance."""
    try:
        manifest = read_manifest()
    except (OSError, ValueError):
        return False
    for key, filename in (("bundle_js_sha256", BUNDLE_JS_NAME), ("bundle_css_sha256", BUNDLE_CSS_NAME)):
        expected = manifest.get(key)
        path = STATIC_DIR / filename
        if not expected or not path.is_file():
            return False
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            return False
    if manifest.get("manifest_version") == 2:
        notices = STATIC_DIR / NOTICES_NAME
        if not notices.is_file() or hashlib.sha256(notices.read_bytes()).hexdigest() != manifest.get("third_party_notices_sha256"):
            return False
        if not _source_is_fresh(manifest):
            return False
    return True


def ensure_bundle_fresh(*, warn: bool = True) -> bool:
    """Warn (once per call) when the bundle does not match the manifest.

    Returns:
        True when fresh, False otherwise. Never raises for staleness: stale
        output is still preferable to no output, but it must be loud.
    """
    fresh = is_bundle_fresh()
    if not fresh and warn:
        warnings.warn(
            "gestaltdb viz bundle does not match viz-manifest.json; "
            "assets or frontend build inputs changed. Rebuild web/ with "
            "`npm run build` to refresh the committed bundle.",
            RuntimeWarning,
            stacklevel=3,
        )
    return fresh


def _payload_json(payload: Mapping[str, Any]) -> str:
    """Serialize the payload for safe embedding in a ``<script>`` tag.

    ``</`` becomes ``<\\/`` so a ``</script>`` inside node/edge strings can
    never close the embedding tag; ``JSON.parse`` semantics are unchanged.
    """
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True).replace("</", "<\\/")


def build_html(
    viz_graph: Any,
    *,
    title: str = "GestaltDB graph",
    warn_on_stale_bundle: bool = True,
    view_overrides: Mapping[str, Any] | None = None,
) -> str:
    """Build a self-contained offline HTML document for a payload.

    Args:
        viz_graph: ``VizGraph`` (or any object exposing ``to_dict()``) or an
            already-serialized payload mapping.
        title: Document title (HTML-escaped).
        warn_on_stale_bundle: Emit a ``RuntimeWarning`` when the committed
            bundle does not match its manifest.
        view_overrides: Optional front-end initial-state overrides (theme,
            charge, link distance, label/property visibility) injected as
            ``window.__GESTALTDB_VIZ_OPTIONS__``.

    Returns:
        Complete HTML document as a string.
    """
    ensure_bundle_fresh(warn=warn_on_stale_bundle)
    if hasattr(viz_graph, "to_dict"):
        payload = viz_graph.to_dict()
    elif isinstance(viz_graph, Mapping):
        payload = dict(viz_graph)
    else:
        raise TypeError(f"Cannot build viz HTML from {type(viz_graph).__name__}")
    js = (STATIC_DIR / BUNDLE_JS_NAME).read_text(encoding="utf-8")
    css = (STATIC_DIR / BUNDLE_CSS_NAME).read_text(encoding="utf-8")
    safe_title = html_module.escape(str(title), quote=True)
    safe_payload = _payload_json(payload)
    safe_overrides = _payload_json(dict(view_overrides or {}))
    notices_path = STATIC_DIR / NOTICES_NAME
    safe_notices = _payload_json({"text": notices_path.read_text(encoding="utf-8") if notices_path.is_file() else ""})
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1" />
<meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; img-src data: blob:; connect-src 'none';" />
<title>{safe_title}</title>
<style>{css}</style>
</head>
<body>
<div id="root"></div>
<script>window.__GESTALTDB_VIZ__ = {safe_payload};window.__GESTALTDB_VIZ_OPTIONS__ = {safe_overrides};</script>
<script id="gdviz-notices" type="application/json">{safe_notices}</script>
<script>{js}</script>
</body>
</html>
"""


def save_html(
    viz_graph: Any,
    path: str | Path,
    *,
    title: str = "GestaltDB graph",
    warn_on_stale_bundle: bool = True,
    view_overrides: Mapping[str, Any] | None = None,
) -> Path:
    """Write a self-contained HTML artifact to ``path``.

    Creates parent directories as needed. Returns the resolved path.
    """
    document = build_html(
        viz_graph, title=title, warn_on_stale_bundle=warn_on_stale_bundle, view_overrides=view_overrides
    )
    resolved = Path(path).expanduser().resolve()
    resolved.parent.mkdir(parents=True, exist_ok=True)
    resolved.write_text(document, encoding="utf-8")
    return resolved
