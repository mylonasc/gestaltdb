"""Validate the reviewed register locally; never writes or calls GitHub."""

import hashlib
import json
from pathlib import Path
import posixpath
import re
import xml.etree.ElementTree as ET
import zipfile
from sync_tracker import load_review


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

    # Freeze canonical original audit rows inside the workbook. This remains
    # verifiable in shallow CI/source exports, without fetching old git objects.
    # The historical binary hash/revision remain provenance, not a live-file pin.
    historical = plan["historical_workbook"]
    original_rows = workbook_rows(workbook, sheet_name="Original audit")
    original_digest = hashlib.sha256(json.dumps(original_rows, ensure_ascii=False,
        separators=(",", ":")).encode()).hexdigest()
    assert original_digest == historical["original_audit_rows_sha256"], "Original audit rows changed"
    review = load_review(root)
    assert plan["status_authority"] == "EPIC_REASONING_ENGINE.md"
    assert plan["reviewed_source"] == review.revision and plan["reviewed_at"] == review.timestamp
    # openpyxl is optional: the normal gate remains stdlib-only. The explicit
    # sync_tracker --check gate checks every generated cell when installed.
    current_rows = workbook_rows(workbook)
    assert len(current_rows) == len(original_rows) == 137
    for current, previous, row in zip(current_rows[1:], original_rows[1:], review.features):
        for column in (0, 1, 2, 4, 5, 6):
            assert current[column] == previous[column], (row[0], "Original acceptance/identity changed", column)
        assert current[3] == row[2], (row[0], "Workbook status drift")
        assert current[11] == previous[3], (row[0], "Original audit status drift")
        assert current[12:14] == [review.timestamp, review.revision], (row[0], "Review identity drift")
        assert row[4] in current[8], (row[0], "Boundary drift")
    assert all(item["status"] == "integrated" for item in plan["delivery"].values())
    for key, _, _, _, pr_commit, _ in review.roadmap[:27]:
        delivery = plan["delivery"][key]
        assert pr_commit == f"#{delivery['pr']} / {delivery['integration_commit']}", (key, "Delivery metadata drift")

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
    audit = json.loads((here / "baseline-review.json").read_text())
    assert audit["baseline"] == plan["baseline"]
    audited = []
    for group in audit["groups"]:
        assert group["status"] in {"available", "partial", "not-implemented", "deferred"}
        assert group["note"] and group["evidence"]
        audited.extend(group["gr"])
        for evidence in group["evidence"]:
            assert (root / evidence).is_file(), evidence
    assert sorted(audited) == workbook_ids, "Audit must cover each GR exactly once"
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
    print("Validated: historical source hashes, live workbook statuses/acceptance/review identity, "
          "136 GR IDs, 5 epics, 27 delivered features, Markdown index and dependency DAG.")


def workbook_rows(path, sheet_name=None):
    """Read the first register sheet without an Excel dependency (both encodings)."""
    ns = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}
    with zipfile.ZipFile(path) as archive:
        strings = []
        if "xl/sharedStrings.xml" in archive.namelist():
            strings = ["".join(item.itertext()) for item in ET.fromstring(
                archive.read("xl/sharedStrings.xml")).findall("m:si", ns)]
        sheet_path = "xl/worksheets/sheet1.xml"
        if sheet_name is not None:
            book = ET.fromstring(archive.read("xl/workbook.xml"))
            rel_id = next(item for item in book.findall("m:sheets/m:sheet", ns)
                          if item.get("name") == sheet_name).get(
                              "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
            relationships = ET.fromstring(archive.read("xl/_rels/workbook.xml.rels"))
            target = next(item.get("Target") for item in relationships if item.get("Id") == rel_id)
            sheet_path = (target.lstrip("/") if target.startswith("/")
                          else posixpath.normpath(posixpath.join("xl", target)))
        sheet = ET.fromstring(archive.read(sheet_path))
        rows = []
        for row in sheet.findall("m:sheetData/m:row", ns):
            values = [None] * 14
            for cell in row.findall("m:c", ns):
                letters = re.match(r"[A-Z]+", cell.get("r")).group()
                index = 0
                for letter in letters:
                    index = index * 26 + ord(letter) - ord("A") + 1
                if index > len(values):
                    values.extend([None] * (index - len(values)))
                value = cell.find("m:v", ns)
                if cell.get("t") == "inlineStr":
                    decoded = "".join(cell.find("m:is", ns).itertext())
                elif value is None:
                    decoded = None
                elif cell.get("t") == "s":
                    decoded = strings[int(value.text)]
                else:
                    decoded = value.text
                values[index - 1] = decoded
            rows.append(values)
        return rows


if __name__ == "__main__":
    main()
