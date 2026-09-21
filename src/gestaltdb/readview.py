"""Consistent temporal read views and reproducible source provenance."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from pathlib import Path

from .temporal import TemporalInstant
from .versioning import canonical_json_bytes, normalize_temporal_instant


class ProvenanceMismatchError(ValueError):
    """Raised when a source graph does not match captured read provenance."""


@dataclass(frozen=True)
class ReadViewProvenance:
    """JSON-serializable identity of one immutable temporal graph view."""

    database_id: str
    commit_horizon: int
    commit_system_time_us: int | None
    commit_marker_sha256: str | None
    visibility_sha256: str
    valid_time_us: int
    backend_name: str
    backend_layout_version: int
    serializer_name: str
    serializer_format_version: int
    snapshot_kind: str = "commit_visibility"
    format_version: int = 1

    @property
    def token(self) -> str:
        """Return a stable digest over all provenance fields."""
        return hashlib.sha256(canonical_json_bytes(self._payload())).hexdigest()

    def _payload(self) -> dict:
        return {
            "format_version": self.format_version,
            "database_id": self.database_id,
            "commit_horizon": self.commit_horizon,
            "commit_system_time_us": self.commit_system_time_us,
            "commit_marker_sha256": self.commit_marker_sha256,
            "visibility_sha256": self.visibility_sha256,
            "valid_time_us": self.valid_time_us,
            "backend": {
                "name": self.backend_name,
                "layout_version": self.backend_layout_version,
                "snapshot_kind": self.snapshot_kind,
            },
            "serializer": {
                "name": self.serializer_name,
                "format_version": self.serializer_format_version,
            },
        }

    def to_dict(self) -> dict:
        """Return canonical JSON-compatible provenance including its token."""
        return {**self._payload(), "token": self.token}

    def to_json(self) -> str:
        """Serialize provenance deterministically."""
        return canonical_json_bytes(self.to_dict()).decode("utf-8")

    @classmethod
    def from_dict(cls, value: dict) -> "ReadViewProvenance":
        """Parse and authenticate a provenance mapping."""
        if not isinstance(value, dict):
            raise ProvenanceMismatchError("read-view provenance must be an object")
        try:
            backend = value["backend"]
            serializer = value["serializer"]
            provenance = cls(
                format_version=value["format_version"],
                database_id=value["database_id"],
                commit_horizon=value["commit_horizon"],
                commit_system_time_us=value["commit_system_time_us"],
                commit_marker_sha256=value["commit_marker_sha256"],
                visibility_sha256=value["visibility_sha256"],
                valid_time_us=value["valid_time_us"],
                backend_name=backend["name"],
                backend_layout_version=backend["layout_version"],
                serializer_name=serializer["name"],
                serializer_format_version=serializer["format_version"],
                snapshot_kind=backend["snapshot_kind"],
            )
            token = value["token"]
        except (KeyError, TypeError) as exc:
            raise ProvenanceMismatchError("read-view provenance is incomplete") from exc
        if not isinstance(token, str) or token != provenance.token:
            raise ProvenanceMismatchError("read-view provenance token mismatch")
        if provenance.format_version != 1:
            raise ProvenanceMismatchError("unsupported read-view provenance format")
        try:
            database_id = str(uuid.UUID(provenance.database_id))
        except (AttributeError, TypeError, ValueError) as exc:
            raise ProvenanceMismatchError("invalid provenance database identity") from exc
        if database_id != provenance.database_id:
            raise ProvenanceMismatchError("non-canonical provenance database identity")
        integer_fields = (
            provenance.commit_horizon,
            provenance.valid_time_us,
            provenance.backend_layout_version,
            provenance.serializer_format_version,
        )
        if any(isinstance(item, bool) or not isinstance(item, int) for item in integer_fields):
            raise ProvenanceMismatchError("invalid provenance integer field")
        if provenance.commit_horizon < 0:
            raise ProvenanceMismatchError("invalid provenance commit horizon")
        if provenance.commit_system_time_us is not None and (
            isinstance(provenance.commit_system_time_us, bool)
            or not isinstance(provenance.commit_system_time_us, int)
        ):
            raise ProvenanceMismatchError("invalid provenance commit time")
        marker = provenance.commit_marker_sha256
        if marker is not None and (
            not isinstance(marker, str)
            or len(marker) != 64
            or any(character not in "0123456789abcdef" for character in marker)
        ):
            raise ProvenanceMismatchError("invalid provenance commit marker")
        visibility = provenance.visibility_sha256
        if (
            not isinstance(visibility, str)
            or len(visibility) != 64
            or any(character not in "0123456789abcdef" for character in visibility)
        ):
            raise ProvenanceMismatchError("invalid provenance visibility digest")
        if provenance.commit_horizon == 0 and (marker is not None or provenance.commit_system_time_us is not None):
            raise ProvenanceMismatchError("empty-history provenance contains commit metadata")
        if provenance.commit_horizon > 0 and (marker is None or provenance.commit_system_time_us is None):
            raise ProvenanceMismatchError("committed provenance lacks commit metadata")
        if provenance.snapshot_kind != "commit_visibility":
            raise ProvenanceMismatchError("unsupported provenance snapshot kind")
        return provenance

    @classmethod
    def from_json(cls, value: str) -> "ReadViewProvenance":
        try:
            decoded = json.loads(value)
        except (TypeError, json.JSONDecodeError) as exc:
            raise ProvenanceMismatchError("invalid read-view provenance JSON") from exc
        return cls.from_dict(decoded)

    def verify_source(self, graph) -> None:
        """Verify that ``graph`` still represents this source view."""
        if graph.database_id != self.database_id:
            raise ProvenanceMismatchError("source database identity mismatch")
        manifest = graph.manifest
        backend = manifest["backend"]
        serializer = manifest["serializer"]
        if (backend["name"], backend["layout_version"]) != (
            self.backend_name,
            self.backend_layout_version,
        ):
            raise ProvenanceMismatchError("source backend identity mismatch")
        if (serializer["name"], serializer["format_version"]) != (
            self.serializer_name,
            self.serializer_format_version,
        ):
            raise ProvenanceMismatchError("source serializer identity mismatch")
        if self.commit_horizon == 0:
            if self.commit_marker_sha256 is not None or self.commit_system_time_us is not None:
                raise ProvenanceMismatchError("empty-history provenance contains commit metadata")
            expected = hashlib.sha256(b"").hexdigest()
            if self.visibility_sha256 != expected:
                raise ProvenanceMismatchError("source visibility digest mismatch")
            return
        commit = graph.get_temporal_commit(self.commit_horizon)
        if commit is None:
            raise ProvenanceMismatchError("source temporal commit is no longer visible")
        marker = graph.store.get_metadata(graph._temporal_visible_key(self.commit_horizon))
        if marker is None or marker.hex() != self.commit_marker_sha256:
            raise ProvenanceMismatchError("source temporal commit marker mismatch")
        if commit.system_time.epoch_microseconds != self.commit_system_time_us:
            raise ProvenanceMismatchError("source temporal commit time mismatch")
        markers = []
        for commit_id in range(1, self.commit_horizon + 1):
            if graph.get_temporal_commit(commit_id, _validate_supersession=False) is None:
                raise ProvenanceMismatchError("source visible commit prefix mismatch")
            commit_marker = graph.store.get_metadata(graph._temporal_visible_key(commit_id))
            markers.append(commit_id.to_bytes(8, "big") + commit_marker)
        if hashlib.sha256(b"".join(markers)).hexdigest() != self.visibility_sha256:
            raise ProvenanceMismatchError("source visibility digest mismatch")


class _MaterializedStore:
    def __init__(self, nodes: dict[bytes, object], edges: dict[bytes, object]):
        self.nodes = nodes
        self.edges = edges

    def get_node_keys_generator(self):
        yield from sorted(self.nodes)

    def get_edge_keys_generator(self):
        yield from sorted(self.edges)


class _MaterializedGraph:
    def __init__(self, graph, nodes: dict[bytes, object], edges: dict[bytes, object]):
        self._graph = graph
        self.store = _MaterializedStore(nodes, edges)

    def get_node(self, key):
        return self.store.nodes.get(key)

    def get_edge(self, key):
        return self.store.edges.get(key)

    def key_to_string(self, value):
        return self._graph.key_to_string(value)


class GraphReadView:
    """A fixed temporal commit horizon and default valid-time instant."""

    def __init__(self, graph, provenance: ReadViewProvenance):
        self._graph = graph
        self.provenance = provenance
        self.valid_time = TemporalInstant(provenance.valid_time_us)

    @property
    def commit_horizon(self) -> int:
        return self.provenance.commit_horizon

    def get_node_version(self, version_id):
        return self._graph.get_node_version(version_id, through_commit=self.commit_horizon)

    def get_edge_version(self, version_id):
        return self._graph.get_edge_version(version_id, through_commit=self.commit_horizon)

    def get_claim_version(self, version_id):
        return self._graph.get_claim_version(version_id, through_commit=self.commit_horizon)

    def iter_node_versions(self, logical_id=None):
        return self._graph.iter_node_versions(logical_id, through_commit=self.commit_horizon)

    def iter_edge_versions(self, logical_id=None):
        return self._graph.iter_edge_versions(logical_id, through_commit=self.commit_horizon)

    def iter_claim_versions(self, claim_id=None):
        return self._graph.iter_claim_versions(claim_id, through_commit=self.commit_horizon)

    def get_node_as_of(self, logical_id, *, valid_time=None):
        return self._graph.get_node_as_of(
            logical_id,
            valid_time=self.valid_time if valid_time is None else valid_time,
            through_commit=self.commit_horizon,
        )

    def get_edge_as_of(self, logical_id, *, valid_time=None):
        return self._graph.get_edge_as_of(
            logical_id,
            valid_time=self.valid_time if valid_time is None else valid_time,
            through_commit=self.commit_horizon,
        )

    def get_claim_as_of(self, claim_id, *, valid_time=None):
        return self._graph.get_claim_as_of(
            claim_id,
            valid_time=self.valid_time if valid_time is None else valid_time,
            through_commit=self.commit_horizon,
        )

    def iter_claims_as_of(self, *, valid_time=None, **kwargs):
        return self._graph.iter_claims_as_of(
            valid_time=self.valid_time if valid_time is None else valid_time,
            through_commit=self.commit_horizon,
            **kwargs,
        )

    def claim_status(self, subject, predicate, object, *, valid_time=None, **kwargs):
        return self._graph.claim_status(
            subject,
            predicate,
            object,
            valid_time=self.valid_time if valid_time is None else valid_time,
            through_commit=self.commit_horizon,
            **kwargs,
        )

    def evaluate_modal(self, expression, *, world, max_depth=4, max_states=1_000):
        """Evaluate a bounded modal expression at this view's fixed horizon."""
        from .modal import evaluate_modal

        return evaluate_modal(
            self, expression, world=world, max_depth=max_depth, max_states=max_states
        )

    def iter_edges_as_of(self, *, valid_time=None, **kwargs):
        return self._graph.iter_edges_as_of(
            valid_time=self.valid_time if valid_time is None else valid_time,
            through_commit=self.commit_horizon,
            **kwargs,
        )

    def iter_nodes_as_of(self, *, valid_time=None):
        return self._graph.iter_nodes_as_of(
            valid_time=self.valid_time if valid_time is None else valid_time,
            through_commit=self.commit_horizon,
        )

    def get_node(self, key):
        logical_id = self._graph.key_to_string(key) if isinstance(key, bytes) else str(key)
        version = self.get_node_as_of(logical_id)
        return None if version is None else version.node

    def get_edge(self, key):
        logical_id = self._graph.key_to_string(key) if isinstance(key, bytes) else str(key)
        version = self.get_edge_as_of(logical_id)
        return None if version is None else version.edge

    def build_sampler_snapshot(self, output_path, **kwargs):
        """Build a sampler snapshot from this exact source view."""
        from .sampling import SamplerSnapshot

        temporal = kwargs.pop("temporal", False)
        source_db = kwargs.pop("source_db", None)
        manifest_path = (
            None
            if self._graph._store_path is None
            else self._graph._store_path / "gestaltdb_manifest.json"
        )
        if source_db is None and manifest_path is not None and manifest_path.exists():
            source_db = {
                "path": str(self._graph._store_path),
                "path_type": "absolute",
                "backend": self._graph._backend_name,
                "serializer": self._graph._serializer_name,
            }
        if temporal:
            return SamplerSnapshot.build_temporal(
                self, output_path, source_db=source_db, **kwargs
            )

        node_ids = sorted({version.logical_id for version in self.iter_node_versions()})
        edge_ids = sorted({version.logical_id for version in self.iter_edge_versions()})
        nodes = {}
        for logical_id in node_ids:
            version = self.get_node_as_of(logical_id)
            if version is not None:
                nodes[self._graph.node_key_to_bytes(logical_id)] = version.node
        edges = {}
        for logical_id in edge_ids:
            version = self.get_edge_as_of(logical_id)
            if version is not None:
                edges[self._graph.node_key_to_bytes(logical_id)] = version.edge

        return SamplerSnapshot.build(
            _MaterializedGraph(self._graph, nodes, edges),
            output_path,
            source_db=source_db,
            source_provenance=self.provenance.to_dict(),
            **kwargs,
        )

    def verify_source(self) -> None:
        self.provenance.verify_source(self._graph)
