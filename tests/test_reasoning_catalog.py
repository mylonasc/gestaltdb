"""RE-17 pinned rule catalog, horizon binding and versioned migration."""

import pytest

from gestaltdb.reasoning.catalog import (
    CATALOG_FORMAT_VERSION,
    CatalogEntry,
    PinnedGeneration,
    RuleCatalog,
    catalog_legacy_parity_gates,
    migrate_dict,
    pin_generation,
)
from gestaltdb.reasoning.model import ReasoningError


def entry(key="rule-a", version="v3", digest="sha256:" + "ab" * 32, ordinal=2):
    return CatalogEntry(key, version, digest, ordinal)


def test_entry_and_catalog_validation():
    assert entry() == entry()
    with pytest.raises(ReasoningError):
        CatalogEntry("", "v", "sha256:" + "ab" * 32, 1)
    with pytest.raises(ReasoningError):
        CatalogEntry("k", "v", "not-a-digest", 1)
    with pytest.raises(ReasoningError):
        CatalogEntry("k", "v", "sha256:" + "ab" * 32, 0)
    with pytest.raises(ReasoningError):
        RuleCatalog((entry("k"), entry("k")))
    with pytest.raises(ReasoningError):
        RuleCatalog("not-entries")
    with pytest.raises(ReasoningError):
        RuleCatalog((), format_version=1)
    first = RuleCatalog((entry("b"), entry("a")))
    assert [item.rule_key for item in first.entries] == ["a", "b"]
    assert first == RuleCatalog((entry("a"), entry("b")))
    assert first.digest != RuleCatalog((entry("a"),)).digest
    assert first.lookup("a") == entry("a")
    assert first.lookup("missing") is None
    assert first.with_entry(entry("a", version="v9")).lookup("a").version_id == "v9"
    with pytest.raises(ReasoningError):
        first.with_entry("not-an-entry")
    assert CatalogEntry.from_dict(entry().to_dict()) == entry()
    with pytest.raises(ReasoningError):
        CatalogEntry.from_dict({"rule_key": "k"})
    with pytest.raises(ReasoningError):
        CatalogEntry.from_dict("not-a-mapping")


def test_pin_verify_and_rebind():
    catalog = RuleCatalog((entry("a"), entry("b"),))
    pinned = pin_generation("gen-1", catalog, "sha256:prog", "sha256:hor")
    assert isinstance(pinned, PinnedGeneration)
    pinned.verify(catalog, "sha256:hor")
    assert pinned.rebind(catalog) is pinned
    drifted = catalog.with_entry(entry("a", version="v9"))
    with pytest.raises(ReasoningError, match="drifted"):
        pinned.verify(drifted, "sha256:hor")
    with pytest.raises(ReasoningError, match="horizon moved"):
        pinned.verify(catalog, "sha256:other")
    with pytest.raises(ReasoningError, match="rematerialize"):
        pinned.rebind(drifted)
    with pytest.raises(ReasoningError):
        pin_generation("", catalog, "sha256:prog", "sha256:hor")
    with pytest.raises(ReasoningError):
        pinned.verify("not-a-catalog", "sha256:hor")
    assert PinnedGeneration("g", catalog.digest, "p", "h").catalog_digest == catalog.digest


def test_dict_migration_preserves_history():
    v1 = {"entries": [{"key": "r1", "digest": "sha256:" + "cd" * 32, "version_id": "v7"},
                      {"key": "r2", "digest": "sha256:" + "ef" * 32}]}
    migrated = migrate_dict(v1)
    assert migrated["format_version"] == CATALOG_FORMAT_VERSION
    assert migrated["entries"][0] == {"rule_key": "r1", "version_id": "v7",
                                      "content_digest": "sha256:" + "cd" * 32, "ordinal": 1}
    assert migrated["entries"][1]["version_id"] == "v1"  # implicit v1 default
    catalog = RuleCatalog.from_dict(v1)
    assert catalog.lookup("r1").version_id == "v7"
    assert RuleCatalog.from_dict(catalog.to_dict()) == catalog
    assert migrate_dict(catalog.to_dict()) == catalog.to_dict()  # v2 idempotent
    with pytest.raises(ReasoningError, match="unsupported"):
        migrate_dict({"format_version": 99, "entries": []})
    with pytest.raises(ReasoningError):
        migrate_dict({"entries": [{"key": "r1"}]})
    with pytest.raises(ReasoningError):
        migrate_dict("not-a-dict")
    with pytest.raises(ReasoningError):
        RuleCatalog.from_dict({"format_version": 99, "entries": []})


def test_parity_gates_are_explicit_and_unclaimed():
    gates = catalog_legacy_parity_gates()
    assert len(gates) == 5 and all(isinstance(gate, str) and gate for gate in gates)
    assert gates == catalog_legacy_parity_gates()  # deterministic


def test_core_runs_without_site_packages():
    from pathlib import Path
    import subprocess
    import sys

    path = Path(__file__).parents[1] / "src/gestaltdb/reasoning/__init__.py"
    script = f"""\
import importlib.util, sys
spec = importlib.util.spec_from_file_location('standalone', {str(path)!r})
module = importlib.util.module_from_spec(spec)
sys.modules['standalone'] = module
spec.loader.exec_module(module)
from standalone.catalog import RuleCatalog, migrate_dict, pin_generation
from standalone.maintenance import DependencyIndex
catalog = RuleCatalog.from_dict({{'entries': [{{'key': 'r', 'digest': 'sha256:{"00" * 32}'}}]}})
pinned = pin_generation('g', catalog, 'prog', 'hor')
pinned.verify(catalog, 'hor')
assert not any(name.startswith(('gestaltdb', 'numpy')) for name in sys.modules)
"""
    subprocess.run([sys.executable, "-I", "-S", "-c", script], check=True, capture_output=True, text=True)
