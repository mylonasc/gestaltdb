"""Validate the explicit tracking snapshot offline; optionally diff live GitHub."""
import json
import subprocess
import sys
from pathlib import Path


def load():
    here = Path(__file__).resolve().parent
    return json.loads((here / "tracking-status.json").read_text())


def check_offline(snapshot):
    assert snapshot["reviewed_integration"] == "22a8aa823d3c4fd088610e0f18feff0530f56a83"
    assert set(snapshot["phases"]) == {"RE-E1", "RE-E2", "RE-E3", "RE-E4", "RE-E5"}
    assert snapshot["phases"]["RE-E1"]["state"] == "closed"
    assert snapshot["phases"]["RE-E2"]["state"] == "closed"
    for key in ("RE-E3", "RE-E4", "RE-E5"):
        assert snapshot["phases"][key]["state"] == "open", key
    for key in ("194", "200", "370", "371", "373"):
        assert snapshot["followups"][key]["state"] == "open", key
    assert snapshot["followups"]["374"]["state"] == "closed"
    for key in ("248", "249", "250", "251", "252", "253", "254", "255", "256"):
        assert snapshot["followups"][key]["state"] == "closed", key
    assert snapshot["expected_sub_issues"]["159"][-1] == 370
    assert snapshot["expected_sub_issues"]["200"] == [371, 373]
    assert snapshot["removed_stale_blockers"] == {"249": [194], "254": [194], "255": [194]}
    for issue, blockers in snapshot["expected_blocked_by"].items():
        assert 194 not in blockers, issue
    print("Tracking snapshot offline validation passed.")


def live_numbers(endpoint):
    out = subprocess.check_output(["gh", "api", endpoint], text=True)
    return sorted(x["number"] for x in json.loads(out))


def main():
    snapshot = load()
    check_offline(snapshot)
    if "--github" not in sys.argv:
        return
    drifts = []
    for parent, expected in snapshot["expected_sub_issues"].items():
        actual = live_numbers(f"repos/mylonasc/gestaltdb/issues/{parent}/sub_issues?per_page=100")
        if actual != sorted(expected):
            drifts.append(f"sub_issues {parent}: expected {sorted(expected)}, live {actual}")
    for issue, expected in snapshot["expected_blocked_by"].items():
        actual = live_numbers(f"repos/mylonasc/gestaltdb/issues/{issue}/dependencies/blocked_by?per_page=100")
        if actual != sorted(expected):
            drifts.append(f"blocked_by {issue}: expected {sorted(expected)}, live {actual}")
    live_states = {}
    for number in {156, 157, 158, 159, 160, 161, 194, 200, 248, 249, 250, 251, 252, 253, 254, 255, 256, 370, 371, 373, 374}:
        data = json.loads(subprocess.check_output(
            ["gh", "api", f"repos/mylonasc/gestaltdb/issues/{number}", "--jq", "{number,state}"], text=True))
        live_states[str(data["number"])] = data["state"]
    expected_states = {"156": "open"}
    for phase in snapshot["phases"].values():
        expected_states[str(phase["issue"])] = phase["state"]
    for number, info in snapshot["followups"].items():
        expected_states[number] = info["state"]
    for number, expected in expected_states.items():
        if live_states.get(number) != expected:
            drifts.append(f"state {number}: expected {expected}, live {live_states.get(number)}")
    if drifts:
        print("Tracking drift detected:")
        for drift in drifts:
            print(" - " + drift)
        raise SystemExit(1)
    print("Live GitHub tracking matches the snapshot; no drift.")


if __name__ == "__main__":
    main()
