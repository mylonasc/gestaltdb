"""Opt-in Python allocation diagnostic, not native allocation/RSS accounting.

Run this file by absolute path from each source checkout to compare revisions.
The current working directory selects the implementation under measurement.
"""

import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import tracemalloc


def main():
    sys.path.insert(0, str(Path.cwd()))
    from benchmarks.reasoning_locator_pipeline import fixture, worker
    from gestaltdb.reasoning.adapters.native_locator_claims import PinnedNativeLocatorClaimSource

    original = PinnedNativeLocatorClaimSource._read_winners
    measurements = []

    def measured(source, cancel):
        tracemalloc.start()
        try:
            result = original(source, cancel)
            current, peak = tracemalloc.get_traced_memory()
            measurements.append({"retained_python_bytes": current, "peak_python_bytes": peak})
            return result
        finally:
            tracemalloc.stop()

    PinnedNativeLocatorClaimSource._read_winners = measured
    try:
        with tempfile.TemporaryDirectory(dir="/tmp/opencode", prefix="hsa-alloc-") as root:
            path = Path(root) / "db"
            loaded = fixture(path, 1000)
            (path.parent / "fixture.json").write_text(json.dumps(loaded))
            samples = [worker(path, 1000, "native-authenticated-locator", "all", 50, "first")
                       for _ in range(3)]
            print(json.dumps({
                "revision": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
                "script_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                "adapter_sha256": hashlib.sha256(Path(
                    "src/gestaltdb/reasoning/adapters/native_locator_claims.py").read_bytes()).hexdigest(),
                "measurements": measurements,
                "samples": samples,
                "boundary": "tracemalloc around _read_winners only; same-process repeated fresh handles, "
                            "uncontrolled OS caches, native buffers excluded, not RSS or a memory guarantee",
            }, indent=2))
    finally:
        PinnedNativeLocatorClaimSource._read_winners = original


if __name__ == "__main__":
    main()
