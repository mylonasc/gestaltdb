"""Descriptive test-category markers and the heuristics that apply them.
Categories (markers describe tests; they never gate or skip anything):

- ``backend``: opens a real backend store (LMDB/LevelDB/PyRex or a managed
  graph with a real manifest).
- ``framework``: requires a real optional framework conversion
  (torch/DGL/TensorFlow/PyG/PyArrow adapters). Stub tests that fake those
  modules stay ``unit``.
- ``native``: exercises Arrow/Polars or other native handles.
- ``fault``: fault injection, cancellation, CAS contention, lock behavior,
  or crash windows.
- ``subprocess``: spawns child processes or coordinates across processes.
- ``unit``: residual category for tests carrying none of the above markers;
  reported by the summary script, not applied as a marker.

The rules below are heuristics over file names, fixture names, and module
attributes. They are intentionally conservative: a missed label understates
integration evidence rather than overstating it. Files with unusual needs
should add an explicit ``pytestmark`` instead of extending the heuristics.
"""

from __future__ import annotations

import re
BACKEND_FIXTURES = frozenset({
    "graph_db",
    "lmdb_graph_db",
    "db_path",
    "managed_graph",
    "memory_graph",
})

BACKEND_FILES = ("kvstore", "backend", "temporal_compatibility", "release_backends")

FRAMEWORK_FILES = ("real_frameworks",)

NATIVE_FILES = ("arrow", "native", "ingest")

FAULT_FILES = ("crash", "fault", "cancel", "concurr", "contention", "reclaim", "stale")

FAULT_TEST_HINTS = ("sigkill", "cancel", "concurrent", "race", "stale", "lock", "kill")

SUBPROCESS_FILES = ()

FRAMEWORK_MODULES = {"torch", "dgl", "tensorflow", "torch_geometric", "pyarrow"}
NATIVE_MODULES = {"pyarrow", "polars"}


def _module_imports(module, names):
    seen = set()
    for value in vars(module).values():
        modname = getattr(value, "__name__", "")
        if modname in names:
            seen.add(modname)
    return seen


def categories_for(filename, fixturenames=(), test_id="", module=None, source=None):
    """Return the category set for one test item.

    Args:
        filename: Test file name, e.g. ``test_cypher.py``.
        fixturenames: Fixture names the test requests.
        test_id: Node ID or test name for fault-hint matching.
        module: Imported test module, used for import detection.
        source: Test function source. When available, ``subprocess`` is only
            applied if the test itself references a child process; otherwise
            the module import alone would label unrelated tests in the file.
    """
    name = filename.lower()
    fixtures = set(fixturenames or ())
    # Test-ID hints match whole tokens so "blocks" never implies "lock".
    tokens = set(re.split(r"[^a-z0-9]+", test_id.lower()))
    categories = set()
    if fixtures & set(BACKEND_FIXTURES) or any(hint in name for hint in BACKEND_FILES):
        categories.add("backend")
    if any(hint in name for hint in FRAMEWORK_FILES):
        categories.add("framework")
    if any(hint in name for hint in NATIVE_FILES):
        categories.add("native")
    if any(hint in name for hint in FAULT_FILES) or tokens & set(FAULT_TEST_HINTS):
        categories.add("fault")
    if any(hint in name for hint in SUBPROCESS_FILES):
        categories.add("subprocess")
    elif hasattr(module, "subprocess"):
        if source is None or "subprocess" in source or "Popen" in source:
            categories.add("subprocess")
    if module is not None:
        imported = _module_imports(module, FRAMEWORK_MODULES | NATIVE_MODULES)
        if imported & (FRAMEWORK_MODULES - {"pyarrow"}):
            categories.add("framework")
        if imported & NATIVE_MODULES:
            categories.add("native")
    return categories
