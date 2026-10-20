"""Validate the reviewed register locally; never writes or calls GitHub."""

import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET
import zipfile


def main():
    here = Path(__file__).resolve().parent
    root = here.parent.parent
    plan = json.loads((here / "roadmap.json").read_text())
    issues = json.loads((here / "issues.json").read_text())
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    workbook = root / "gestaltdb_reasoning_feature_tracker.xlsx"
    with zipfile.ZipFile(workbook) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(t.itertext()) for t in ET.fromstring(
                archive.read("xl/sharedStrings.xml")
            ).findall("m:si", ns)]
        sheet = ET.fromstring(archive.read("xl/worksheets/sheet1.xml"))
        workbook_ids = []
        for row in sheet.findall("m:sheetData/m:row", ns)[1:]:
            cell = row.find("m:c", ns)
            if cell.get("t") == "inlineStr":
                value = "".join(cell.find("m:is", ns).itertext())
            else:
                value = cell.find("m:v", ns).text
                if cell.get("t") == "s":
                    value = strings[int(value)]
            workbook_ids.append(int(value.removeprefix("GR-")))
    assert workbook_ids == list(range(1, 137)), "Unexpected original workbook IDs"

    for name, expected in plan["source_sha256"].items():
        path = here / name if name == "source-assessment.md" else root / name
        assert hashlib.sha256(path.read_bytes()).hexdigest() == expected, name

    epics = {item["id"]: item for item in plan["epics"]}
    features = {item["id"]: item for item in plan["features"]}
    assert len(epics) == len(plan["epics"]) == 5
    assert len(features) == len(plan["features"]) == 27
    assert set(epics) == set(issues["epics"])
    assert set(features) == set(issues["features"])
    numbers = [issues["roadmap"], *issues["epics"].values(), *issues["features"].values()]
    assert len(set(numbers)) == len(numbers), "Duplicate issue number"

    all_items = epics | features
    visited, active = set(), set()

    def visit(key):
        assert key not in active, f"Dependency cycle at {key}"
        if key in visited:
            return
        active.add(key)
        item = all_items[key]
        assert len(item["depends"]) == len(set(item["depends"]))
        for dep in item["depends"]:
            assert dep in all_items, (key, dep)
            visit(dep)
        active.remove(key)
        visited.add(key)

    for key in all_items:
        visit(key)
    for feature in features.values():
        assert feature["epic"] in epics
        assert feature["acceptance"] and feature["gr"]
        assert set(feature["depends"]) <= features.keys()
    expected_epics = list(epics)
    for index, epic in enumerate(epics.values()):
        assert epic["depends"] == ([] if not index else [expected_epics[index - 1]])

    coverage = set(plan["baseline_preserve_gr"])
    coverage.update(g for f in features.values() for g in f["gr"])
    assert coverage == set(workbook_ids), f"Coverage mismatch: {coverage ^ set(workbook_ids)}"
    assert plan["integration_branch"] == "feature/onto-dev-epic-2"
    assert plan["baseline"] == "c71f0433fc40251b7dcec08b60370e80b40d36d8"
    markdown = (root / "EPIC_REASONING_ENGINE.md").read_text()
    rows = re.findall(
        r"^\| (RE-\d{2}) \| \[#(\d+)\]\([^)]+\) \| [^|]+ \| ([^|]+) \|$",
        markdown, re.MULTILINE,
    )
    assert len(rows) == len(features), "Markdown feature index is incomplete"
    for key, number, dependencies in rows:
        assert int(number) == issues["features"][key], key
        expected = features[key]["depends"]
        actual = [] if dependencies == "None" else [
            "RE-" + value.strip() for value in dependencies.split(",")
        ]
        assert actual == expected, (key, actual, expected)
    print("Validated: original source hashes, all 136 GR IDs, 5 sequential epics, "
          "27 feature issues, Markdown index and acyclic dependency graph.")


if __name__ == "__main__":
    main()
