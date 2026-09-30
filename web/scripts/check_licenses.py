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
import argparse
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


def lock_entries() -> dict[str, dict]:
    """Retain lockfile paths/versions, including nested and scoped packages."""
    pkg = json.loads((WEB_DIR / "package.json").read_text())
    lock = json.loads((WEB_DIR / "package-lock.json").read_text())
    entries = {path: entry for path, entry in lock["packages"].items() if path}
    for name in {**pkg.get("dependencies", {}), **pkg.get("devDependencies", {})}:
        if f"node_modules/{name}" not in entries:
            raise ValueError(f"direct dependency {name!r} missing from lockfile")
    for path, entry in entries.items():
        if not path.startswith("node_modules/") or ".." in Path(path).parts or not entry.get("version"):
            raise ValueError(f"invalid locked package {path!r}")
    return entries


def installed_packages(*, production_only: bool = False) -> dict[str, Path]:
    found: dict[str, Path] = {}
    for path, entry in lock_entries().items():
        if production_only and entry.get("dev"):
            continue
        directory = WEB_DIR / path
        manifest = directory / "package.json"
        if not manifest.is_file() and entry.get("optional"):
            continue  # npm omits platform-specific optional packages.
        data = json.loads(manifest.read_text())  # Missing/unreadable dependencies fail closed.
        if data.get("name") != path.rsplit("node_modules/", 1)[-1] or data.get("version") != entry["version"]:
            raise ValueError(f"{path}: installed version differs from package-lock.json; run npm ci")
        found[path] = directory
    return found


def iter_production_packages() -> dict[str, Path]:
    return installed_packages(production_only=True)


def iter_lockfile_packages() -> dict[str, str]:
    """Return {package path: license expression} from package-lock.json.

    Lets CI gate licenses without installing node_modules.
    """
    return {path: _normalize(entry.get("license")) for path, entry in lock_entries().items()}


def write_notices(destination: Path) -> None:
    """Conservatively include full notices for the complete runtime dependency set."""
    sections = ["GestaltDB visualization — third-party notices\n\n"
                "Runtime dependencies are bundled locally. This file includes the full runtime\n"
                "dependency set, including modules that may be removed by tree shaking.\n"]
    for path, directory in sorted(iter_production_packages().items()):
        data = json.loads((directory / "package.json").read_text())
        files = sorted(file for file in directory.iterdir() if file.is_file() and
                       file.name.upper().startswith(("LICENSE", "LICENCE", "COPYING", "NOTICE")))
        if not files:
            raise ValueError(f"{path}: no license/notice text found")
        sections.append(f"\n{'=' * 72}\n{data['name']} {data['version']} ({path})\n"
                        f"License: {_normalize(data.get('license'))}\n")
        for file in files:
            sections.append(f"\n--- {file.name} ---\n{file.read_text(encoding='utf-8').rstrip()}\n")
    destination.write_text("".join(sections), encoding="utf-8")


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lockfile", action="store_true")
    parser.add_argument("--notices", type=Path, help="Write full runtime notices after installed-license validation")
    args = parser.parse_args(argv)
    if args.lockfile:
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
    packages = installed_packages()
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
    if args.notices:
        write_notices(args.notices)
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main(sys.argv[1:]))
    except (OSError, ValueError, KeyError) as exc:
        print(f"check_licenses: {exc}", file=sys.stderr)
        raise SystemExit(2) from exc
