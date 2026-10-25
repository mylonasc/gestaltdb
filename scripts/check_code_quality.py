"""Bound library module size; legacy exceptions are exact, shrinking budgets."""

from __future__ import annotations

import argparse
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MAX_MODULE_LINES = 800
BASELINE = Path("planning/code-quality/module-size-baseline.json")


def check_module_sizes(root: Path) -> list[str]:
    """Return actionable violations, including stale or malformed exceptions.

    Count physical lines, including comments/docstrings, so splitting statements
    or hiding code in giant classes does not evade the module boundary. Tests,
    benchmarks and generated non-Python bundles are outside the library budget.
    """
    errors = []
    try:
        baseline = json.loads((root / BASELINE).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return [f"{BASELINE}: cannot read baseline: {exc}"]
    if not isinstance(baseline, dict):
        return [f"{BASELINE}: expected a path-to-line-budget object"]
    sizes = {
        path.relative_to(root).as_posix(): len(path.read_text(encoding="utf-8").splitlines())
        for path in sorted((root / "src/gestaltdb").rglob("*.py"))
    }
    if not sizes:
        errors.append("src/gestaltdb: no Python modules found")
    for path, budget in baseline.items():
        if type(budget) is not int or budget <= MAX_MODULE_LINES:
            errors.append(f"{path}: exception must be an integer above {MAX_MODULE_LINES}")
            continue
        if path not in sizes:
            errors.append(f"{path}: stale exception; remove it from {BASELINE}")
            continue
        if sizes[path] < budget:
            action = "remove its exception" if sizes[path] <= MAX_MODULE_LINES else f"lower its budget to {sizes[path]}"
            errors.append(f"{path}: shrank from {budget} to {sizes[path]} lines; {action} in {BASELINE}")
    for path, count in sizes.items():
        budget = baseline.get(path, MAX_MODULE_LINES)
        if type(budget) is not int or budget <= 0:
            continue  # Invalid baseline already reported above.
        if count > budget:
            errors.append(
                f"{path}: {count} lines exceeds {budget}; extract a cohesive module "
                "instead of increasing the budget"
            )
    return errors


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    errors = check_module_sizes(ROOT)
    if errors:
        for error in errors:
            print(error)
        return 1
    print(f"Module-size gate passed (new modules <= {MAX_MODULE_LINES} physical lines).")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
