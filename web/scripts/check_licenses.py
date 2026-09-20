#!/usr/bin/env python3
"""License gate for the gestaltdb-viz JS bundle (VIZ-02).

Fails unless every production dependency vendored into the prebuilt bundle
uses a permissively licensed component. Stdlib only so CI can run it
without extra tooling.

Usage:
    python3 scripts/check_licenses.py            # from web/
    npm run check:licenses
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

HERE = Path(__file__).resolve()
WEB_DIR = HERE.parents[1]

ALLOWED = {
    "MIT",
    "ISC",
    "Apache-2.0",
    "BSD",
    "BSD-2-Clause",
    "BSD-3-Clause",
    "CC0-1.0",
    "CC-BY-3.0",
    "CC-BY-4.0",
    "Unlicense",
}

# SPDX expressions seen in the React/D3 ecosystem that are allowlisted as a
# whole. Deliberately narrow: anything else fails loudly for human review.
ALLOWED_EXPRESSIONS = {
    "(MIT AND CC-BY-3.0)",  # d3 meta-package README convention
    "(MIT AND BSD-3-Clause)",  # common d3 submodule dual notice
}


def _normalize(license_value) -> str:
    if isinstance(license_value, dict):
        return str(license_value.get("type", "")).strip()
    return str(license_value or "").strip()


def _is_allowed(expression: str) -> bool:
    if expression in ALLOWED or expression in ALLOWED_EXPRESSIONS:
        return True
    # "MIT OR Apache-2.0" style dual licensing is fine when every option is
    # allowlisted; anything with AND (beyond the known notices above) fails.
    if " AND " in expression:
        return False
    options = [part.strip(" ()") for part in expression.split(" OR ")]
    return len(options) > 1 and all(option in ALLOWED for option in options)


def iter_production_packages() -> dict[str, Path]:
    pkg = json.loads((WEB_DIR / "package.json").read_text())
    wanted = set(pkg.get("dependencies", {}))
    found: dict[str, Path] = {}
    for node_modules in (WEB_DIR / "node_modules",):
        if not node_modules.is_dir():
            continue
        for name in wanted:
            for candidate in (node_modules / name,):
                manifest = candidate / "package.json"
                if manifest.is_file():
                    found[name] = candidate
        # Transitive production deps ship inside the bundle too. Subpath
        # export stubs (e.g. nanoid/async, rollup/dist/es) carry no "name"
        # and are not packages, so they are skipped.
        for manifest in node_modules.rglob("package.json"):
            if "node_modules" not in manifest.parts:
                continue
            try:
                data = json.loads(manifest.read_text())
            except (OSError, ValueError):
                continue
            if not data.get("name"):
                continue
            found.setdefault(data["name"], manifest.parent)
    return found


def iter_lockfile_packages() -> dict[str, str]:
    """Return {package name: license expression} from package-lock.json.

    Lets CI gate licenses without installing node_modules.
    """
    lockfile = WEB_DIR / "package-lock.json"
    data = json.loads(lockfile.read_text())
    result: dict[str, str] = {}
    for path, entry in (data.get("packages") or {}).items():
        if not path or not path.startswith("node_modules/"):
            continue
        # Skip nested subpath stubs the same way as the node_modules walk.
        name = path[len("node_modules/"):]
        if "/" in name and not name.startswith("@"):
            continue
        result[name] = _normalize(entry.get("license"))
    return result


def main(argv: list[str]) -> int:
    if "--lockfile" in argv:
        licenses = iter_lockfile_packages()
        if not licenses:
            print("check_licenses: package-lock.json missing or empty", file=sys.stderr)
            return 2
        violations = [f"{name}: {expression or 'UNKNOWN'}" for name, expression in sorted(licenses.items()) if not _is_allowed(expression)]
        print(f"check_licenses: inspected {len(licenses)} locked packages")
        if violations:
            print("check_licenses: FORBIDDEN licenses:", file=sys.stderr)
            for violation in violations:
                print(f"  - {violation}", file=sys.stderr)
            return 1
        print("check_licenses: all locked licenses allowlisted")
        return 0
    packages = iter_production_packages()
    if not packages:
        print("check_licenses: node_modules not found; run `npm ci` first", file=sys.stderr)
        return 2
    violations: list[str] = []
    for name in sorted(packages):
        manifest = packages[name] / "package.json"
        try:
            data = json.loads(manifest.read_text())
        except (OSError, ValueError) as exc:
            violations.append(f"{name}: unreadable package.json ({exc})")
            continue
        expression = _normalize(data.get("license") or data.get("licenses"))
        if not expression or not _is_allowed(expression):
            violations.append(f"{name}: {expression or 'UNKNOWN'}")
    print(f"check_licenses: inspected {len(packages)} packages")
    if violations:
        print("check_licenses: FORBIDDEN licenses:", file=sys.stderr)
        for violation in violations:
            print(f"  - {violation}", file=sys.stderr)
        return 1
    print("check_licenses: all licenses allowlisted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
