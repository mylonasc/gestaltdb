"""Pinned indexed rule catalog with horizon binding and versioned migration.

A ``RuleCatalog`` pins an exact set of rule versions: each ``CatalogEntry``
binds a rule key to its content digest, and the catalog digest binds the
whole set. ``pin_generation`` binds that catalog digest together with a rule
program digest and a fact horizon into one ``PinnedGeneration`` identity, so
a materialization can prove which ruleset and which inputs it was computed
from. Any mismatch fails closed instead of silently mixing generations.

Catalogs are versioned dictionaries: ``export_dict``/``import_dict`` carry a
``format_version`` and ``migrate_dict`` upgrades v1 snapshots to v2 without
touching historical rule versions. Unknown versions fail closed.

Legacy ``run_rules``/``maintain_truth`` are intentionally not adapted here:
the parity gates (interval selection, all-support justifications, limits,
IDs, publication) are documented as unmet, so the compatibility path stays
the default and this catalog is opt-in binding metadata only.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Mapping

from .model import ReasoningError, _text


CATALOG_FORMAT_VERSION = 2


@dataclass(frozen=True)
class CatalogEntry:
    """One pinned rule version: key, content digest and ordinal."""

    rule_key: str
    version_id: str
    content_digest: str
    ordinal: int

    def __post_init__(self) -> None:
        _text(self.rule_key, "rule_key")
        _text(self.version_id, "version_id")
        _text(self.content_digest, "content_digest")
        if not self.content_digest.startswith("sha256:"):
            raise ReasoningError("content_digest must be a sha256: digest")
        if isinstance(self.ordinal, bool) or not isinstance(self.ordinal, int) or self.ordinal < 1:
            raise ReasoningError("ordinal must be a positive integer")

    def to_dict(self) -> dict[str, object]:
        return {"rule_key": self.rule_key, "version_id": self.version_id,
                "content_digest": self.content_digest, "ordinal": self.ordinal}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "CatalogEntry":
        if not isinstance(value, Mapping):
            raise ReasoningError("catalog entry must be a mapping")
        try:
            return cls(str(value["rule_key"]), str(value["version_id"]),
                       str(value["content_digest"]), int(value["ordinal"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise ReasoningError(f"invalid catalog entry: {exc}") from exc


@dataclass(frozen=True)
class RuleCatalog:
    """An exact pinned set of rule versions with a binding digest."""

    entries: tuple[CatalogEntry, ...]
    format_version: int = CATALOG_FORMAT_VERSION

    def __post_init__(self) -> None:
        if not isinstance(self.entries, (tuple, list)):
            raise ReasoningError("entries must be a tuple or list")
        if not all(isinstance(item, CatalogEntry) for item in self.entries):
            raise ReasoningError("entries must contain only CatalogEntry values")
        if isinstance(self.format_version, bool) or self.format_version != CATALOG_FORMAT_VERSION:
            raise ReasoningError(f"format_version must be {CATALOG_FORMAT_VERSION}")
        keys = [item.rule_key for item in self.entries]
        if len(set(keys)) != len(keys):
            raise ReasoningError("catalog rule keys must be unique")
        object.__setattr__(self, "entries", tuple(sorted(self.entries, key=lambda item: item.rule_key)))

    @property
    def digest(self) -> str:
        import hashlib
        import json

        payload = ["gestaltdb-rule-catalog-v2", self.format_version,
                   [item.to_dict() for item in self.entries]]
        return "sha256:" + hashlib.sha256(
            json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")).hexdigest()

    def lookup(self, rule_key: str) -> CatalogEntry | None:
        _text(rule_key, "rule_key")
        for item in self.entries:
            if item.rule_key == rule_key:
                return item
        return None

    def to_dict(self) -> dict[str, object]:
        return {"format_version": self.format_version,
                "entries": [item.to_dict() for item in self.entries]}

    @classmethod
    def from_dict(cls, value: Mapping[str, object]) -> "RuleCatalog":
        if not isinstance(value, Mapping):
            raise ReasoningError("catalog must be a mapping")
        version = value.get("format_version", 1)
        if version == 1:
            value = migrate_dict(dict(value))
            version = value["format_version"]
        if version != CATALOG_FORMAT_VERSION:
            raise ReasoningError(f"unsupported catalog format_version: {version!r}")
        try:
            entries = [CatalogEntry.from_dict(item) for item in value["entries"]]
        except (KeyError, TypeError) as exc:
            raise ReasoningError(f"invalid catalog: {exc}") from exc
        return cls(tuple(entries))

    def with_entry(self, entry: CatalogEntry) -> "RuleCatalog":
        """Return a new catalog with ``entry`` added or replacing its key."""
        if not isinstance(entry, CatalogEntry):
            raise ReasoningError("entry must be a CatalogEntry")
        return RuleCatalog(tuple(item for item in self.entries if item.rule_key != entry.rule_key) + (entry,))


def migrate_dict(value: dict[str, object]) -> dict[str, object]:
    """Upgrade a v1 catalog dictionary to v2 without touching rule versions.

    v1 entries carry ``key``/``digest`` pairs with an implicit ordinal of 1;
    v2 names them ``rule_key``/``content_digest`` and records the ordinal
    explicitly. Unknown versions fail closed; historical version IDs and
    digests pass through byte-identical.
    """
    if not isinstance(value, dict):
        raise ReasoningError("catalog dictionary must be a dict")
    version = value.get("format_version", 1)
    if version == CATALOG_FORMAT_VERSION:
        return dict(value)
    if version != 1:
        raise ReasoningError(f"unsupported catalog format_version: {version!r}")
    try:
        raw = list(value["entries"])
    except (KeyError, TypeError) as exc:
        raise ReasoningError(f"invalid v1 catalog: {exc}") from exc
    entries = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ReasoningError("v1 catalog entries must be mappings")
        try:
            entries.append({"rule_key": str(item["key"]), "version_id": str(item.get("version_id", "v1")),
                            "content_digest": str(item["digest"]), "ordinal": 1})
        except KeyError as exc:
            raise ReasoningError(f"invalid v1 catalog entry, missing {exc}") from exc
    return {"format_version": CATALOG_FORMAT_VERSION, "entries": entries}


@dataclass(frozen=True)
class PinnedGeneration:
    """One bound identity: catalog digest, program digest and fact horizon.

    Readers verify a stored generation against the live catalog and horizon
    before trusting it; any drift fails closed and demands rematerialization
    or explicit migration. Historical pinned generations stay readable: only
    the binding check, never the record, changes across catalog versions.
    """

    generation_id: str
    catalog_digest: str
    program_digest: str
    horizon: str

    def __post_init__(self) -> None:
        for name in ("generation_id", "program_digest", "horizon"):
            _text(getattr(self, name), name)
        _text(self.catalog_digest, "catalog_digest")
        if not self.catalog_digest.startswith("sha256:"):
            raise ReasoningError("catalog_digest must be a sha256: digest")

    def verify(self, catalog: RuleCatalog, horizon: str) -> None:
        """Fail closed unless ``catalog`` and ``horizon`` match the pin."""
        if not isinstance(catalog, RuleCatalog):
            raise ReasoningError("catalog must be a RuleCatalog")
        _text(horizon, "horizon")
        if catalog.digest != self.catalog_digest:
            raise ReasoningError("rule catalog drifted since this generation was pinned")
        if horizon != self.horizon:
            raise ReasoningError("fact horizon moved since this generation was pinned")

    def rebind(self, catalog: RuleCatalog) -> "PinnedGeneration":
        """Adopt an equivalent catalog (e.g. after dict migration) explicitly.

        The program digest and horizon must be unchanged; only catalog
        metadata may move, and only to a catalog the caller names. Rule
        content changes alter the digest and fail here by design.
        """
        if not isinstance(catalog, RuleCatalog):
            raise ReasoningError("catalog must be a RuleCatalog")
        if catalog.digest == self.catalog_digest:
            return self
        raise ReasoningError("catalog content changed; rematerialize instead of rebinding")


def pin_generation(generation_id: str, catalog: RuleCatalog, program_digest: str,
                   horizon: str) -> PinnedGeneration:
    """Bind one generation identity to an exact catalog, program and horizon."""
    if not isinstance(catalog, RuleCatalog):
        raise ReasoningError("catalog must be a RuleCatalog")
    _text(generation_id, "generation_id")
    _text(program_digest, "program_digest")
    _text(horizon, "horizon")
    return PinnedGeneration(generation_id, catalog.digest, program_digest, horizon)


def catalog_legacy_parity_gates() -> tuple[str, ...]:
    """Gates that must pass before legacy run_rules/maintain_truth adapt.

    Each gate names a behavior that must reach parity first; until then the
    compatibility path stays the default and this catalog remains opt-in
    binding metadata. No gate is claimed here.
    """
    return ("interval selection parity",
            "all-support justifications parity",
            "limit/budget parity",
            "identity and provenance parity",
            "publication-path parity")
