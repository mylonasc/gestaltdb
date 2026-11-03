"""Keep temporal learning routes and selection boundaries visible.

Every displayed backend/codec alternative is executed separately by
test_documentation_variants.py, including all four new database setups.
"""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_temporal_introduction_defines_clocks_before_code():
    text = (ROOT / "docs/temporal-introduction.rst").read_text()
    introduction = text.split(".. code-block:: python", 1)[0]
    for term in ("Valid time", "System time", "half-open", "S1", "S2", "mutable"):
        assert term in introduction
    for page in ("temporal-updates", "temporal-reads", "temporal-versioning",
                 "reasoning-temporal", "operations", "sampling-temporal"):
        assert f":doc:`{page}`" in text
    assert "first.system_time, corrected.system_time" in text
    assert 'graph.get_node("alice").properties["department"] == "current-only"' in text


def test_temporal_paths_have_bidirectional_navigation():
    navigation = (ROOT / "docs/index.rst").read_text()
    reference = (ROOT / "docs/temporal-versioning.rst").read_text()
    for page in ("temporal-introduction", "temporal-updates", "temporal-reads"):
        assert f"   {page}\n" in navigation
        assert f":doc:`{page}`" in reference
    for path in ("README.md", "docs/quickstart.rst", "src/gestaltdb/agent_skill/references/temporal.md"):
        assert "temporal-introduction" in (ROOT / path).read_text()


def test_temporal_read_and_recovery_limits_are_explicit():
    updates = (ROOT / "docs/temporal-updates.rst").read_text()
    reads = (ROOT / "docs/temporal-reads.rst").read_text()
    for term in ("ordinal", "visibility marker", "stale", "TTL", "temporal-recovery-operations"):
        assert term in updates
    for term in ("own historical query view", "does not independently pin", "discard partial",
                 "current read view", "not every instant", "both"):
        assert term in reads
