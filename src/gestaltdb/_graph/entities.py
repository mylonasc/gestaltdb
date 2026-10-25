"""Graph entities and pure value codecs (Phase 1 extraction).

This module owns ``Node``, ``Edge``, ``TimeIndexedEdge``,
``GraphEntityDictSerializer``, and the pure property-index/datetime helpers
previously defined in ``gestaltdb.graphdb``. It performs no store I/O.

Pickle compatibility: historical streams record
``gestaltdb.graphdb.Node`` (and friends). The classes below preserve
``__module__ == "gestaltdb.graphdb"`` so existing pickles keep loading and
new pickles keep the historical path. ``gestaltdb.graphdb`` re-exports every
public name; no import path change is required for callers.
"""

from __future__ import annotations

import base64
import datetime
import json
import struct
import uuid
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..serializers import Serializer

from ..serializers import temporal_tagged_value
from ..temporal import (
    TemporalDate,
    TemporalDuration,
    TemporalInstant,
    TemporalLocalDateTime,
    TemporalLocalTime,
    TemporalTime,
)


def _normalize_labels(labels):
    """Return deterministic string labels without duplicates.

    Args:
        labels: Optional iterable of label values.

    Returns:
        Tuple of string labels preserving first-seen order.

    Examples:
        >>> _normalize_labels(["Drug", "Drug", "Compound"])
        ('Drug', 'Compound')
    """
    if labels is None:
        return tuple()
    return tuple(dict.fromkeys(str(label) for label in labels))


def node_key_to_bytes(node_key) -> bytes:
    """Normalize a node key to bytes.

    Args:
        node_key: String or bytes node key.

    Returns:
        UTF-8 encoded bytes.

    Examples:
        >>> node_key_to_bytes("drug-1")
        b'drug-1'
    """
    if isinstance(node_key, bytes):
        return node_key
    return node_key.encode('utf-8')


def edge_key_to_bytes(edge_key) -> bytes:
    """Normalize an edge key to bytes.

    Args:
        edge_key: String or bytes edge key.

    Returns:
        UTF-8 encoded bytes.

    Examples:
        >>> edge_key_to_bytes("d1-p1")
        b'd1-p1'
    """
    if isinstance(edge_key, bytes):
        return edge_key
    return edge_key.encode('utf-8')


def key_to_string(key) -> str:
    """Normalize a key to a string.

    Args:
        key: String or UTF-8 bytes key.

    Returns:
        String key.

    Examples:
        >>> key_to_string(b"drug-1")
        'drug-1'
    """
    if isinstance(key, bytes):
        return key.decode('utf-8')
    return key


def _property_value_to_index_bytes(value) -> bytes:
    """Encode a property value for exact-match sorted indexes.

    Args:
        value: JSON-like property value, with bytes supported through tagging.

    Returns:
        Stable UTF-8 bytes suitable for sorted exact-match index keys.

    Examples:
        >>> _property_value_to_index_bytes("drug")
        b'"drug"'
    """
    def normalize(item):
        if isinstance(item, (
            TemporalDate,
            TemporalDuration,
            TemporalInstant,
            TemporalLocalDateTime,
            TemporalLocalTime,
            TemporalTime,
        )):
            return normalize(temporal_tagged_value(item, canonical_time=True))
        if isinstance(item, bytes):
            return {"__gestaltdb_type__": "bytes", "value": base64.b64encode(item).decode("ascii")}
        if isinstance(item, tuple):
            return [normalize(value) for value in item]
        if isinstance(item, list):
            return [normalize(value) for value in item]
        if isinstance(item, dict):
            return {str(key): normalize(value) for key, value in sorted(item.items())}
        return item

    return json.dumps(normalize(value), sort_keys=True, separators=(",", ":")).encode("utf-8")


def _property_value_to_range_index_bytes(value) -> bytes | None:
    """Encode supported scalar values for sorted range indexes."""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        packed = bytearray(struct.pack(">d", float(value)))
        if packed[0] & 0x80:
            packed = bytearray(byte ^ 0xFF for byte in packed)
        else:
            packed[0] ^= 0x80
        return b"n" + bytes(packed).hex().encode("ascii")
    if isinstance(value, str):
        return b"s" + value.encode("utf-8").hex().encode("ascii")
    temporal_value = None
    temporal_kind = None
    if isinstance(value, TemporalDate):
        temporal_kind, temporal_value = b"d", value.value.toordinal()
    elif isinstance(value, TemporalLocalTime):
        temporal_kind, temporal_value = b"l", value.microseconds
    elif isinstance(value, TemporalTime):
        temporal_kind, temporal_value = b"t", value.utc_microseconds
    elif isinstance(value, TemporalInstant):
        temporal_kind, temporal_value = b"i", value.epoch_microseconds
    elif isinstance(value, TemporalLocalDateTime):
        clock = value.value
        micros = ((clock.hour * 60 + clock.minute) * 60 + clock.second) * 1_000_000 + clock.microsecond
        temporal_kind, temporal_value = b"c", clock.date().toordinal() * 86_400_000_000 + micros
    elif isinstance(value, TemporalDuration):
        temporal_kind, temporal_value = b"u", value.total_microseconds
    if temporal_value is not None and -(1 << 127) <= temporal_value < (1 << 127):
        return b"t" + temporal_kind + (temporal_value + (1 << 127)).to_bytes(16, "big")
    return None

def datetime_to_bytes(dt: datetime.datetime, tzinfo = datetime.timezone.utc) -> bytes:
    """Convert a datetime to big-endian microseconds since the Unix epoch.

    Args:
        dt: Datetime at or after 1970-01-01.
        tzinfo: Time zone used for the epoch reference.

    Returns:
        Eight bytes containing the timestamp as an unsigned integer.

    Examples:
        >>> datetime_to_bytes(datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc))
        b'\\x00\\x00\\x00\\x00\\x00\\x00\\x00\\x00'
    """
    # Make sure `dt` is in UTC, or at least consistently handled.
    # (If dt has no tzinfo, Python treats it as local time for .timestamp().)
    epoch = datetime.datetime(1970, 1, 1, tzinfo=tzinfo)
    delta = dt - epoch
    # Convert to integer microseconds
    microseconds = int(delta.total_seconds() * 1_000_000)
    # Pack as an unsigned 64-bit integer in big-endian order
    return struct.pack('>Q', microseconds)

def bytes_to_datetime(b: bytes, tzinfo = datetime.timezone.utc) -> datetime.datetime:
    """Convert bytes produced by ``datetime_to_bytes`` back to a datetime.

    Args:
        b: Eight-byte timestamp generated by ``datetime_to_bytes``.
        tzinfo: Time zone used for the epoch reference.

    Returns:
        Decoded datetime.

    Examples:
        >>> bytes_to_datetime(b'\\x00' * 8)
        datetime.datetime(1970, 1, 1, 0, 0, tzinfo=datetime.timezone.utc)
    """
    epoch = datetime.datetime(1970, 1, 1, tzinfo=tzinfo)
    (microseconds,) = struct.unpack('>Q', b)
    return epoch + datetime.timedelta(microseconds=microseconds)


class Node:
    """Graph node with an ID, native labels, and arbitrary properties.

    Args:
        node_id: Optional stable node identifier. A UUID is generated when omitted.
        properties: Optional dictionary of node attributes.
        labels: Optional iterable of node labels. Labels are stored natively and
            maintained in the label index by ``GraphDB``.

    Examples:
        >>> Node(node_id="drug-1", labels=["Drug"], properties={"kind": "drug"}).get_id
        'drug-1'
        >>> Node(node_id="drug-1", labels=["Drug", "Drug"]).labels
        ('Drug',)
    """

    def __init__(self, node_id=None, properties=None, labels=None):
        """Initialize a node, generating a UUID when ``node_id`` is omitted."""
        self._id = node_id or str(uuid.uuid4())
        self.properties = properties or {}
        self.labels = _normalize_labels(labels)

    @property
    def get_id(self):
        """Unique identifier for this node."""
        return self._id

    @property
    def get_id_bytes(self):
        """Return the node ID encoded as UTF-8 bytes.

        Examples:
            >>> Node(node_id="drug-1").get_id_bytes
            b'drug-1'
        """
        return self._id.encode('utf-8')

    def to_dict(self):
        """Convert to a dictionary form for serialization.

        Returns:
            Dictionary containing ``id``, ``properties``, and ``labels``.

        Examples:
            >>> Node(node_id="n1", labels=["Drug"]).to_dict()["labels"]
            ['Drug']
        """
        return {
            'id': self._id,
            'properties': self.properties,
            'labels': list(self.labels),
        }

    @classmethod
    def from_dict(cls, data: dict):
        """Create a node from serialized dictionary data.

        Args:
            data: Dictionary produced by ``to_dict``. Older dictionaries without
                ``labels`` deserialize with an empty label tuple.

        Returns:
            ``Node`` instance.

        Examples:
            >>> Node.from_dict({"id": "n1", "properties": {}}).labels
            ()
        """
        return cls(node_id=data['id'], properties=data['properties'], labels=data.get('labels'))

class Edge:
    """Directed graph edge with source, target, and properties.

    Args:
        edge_id: Optional stable edge identifier. A UUID is generated when omitted.
        source: Source node ID or ``Node`` instance.
        target: Target node ID or ``Node`` instance.
        properties: Optional edge attributes. Typed traversal reads ``properties["type"]``.

    Examples:
        >>> Edge(edge_id="d1-p1", source="drug-1", target="protein-1").source
        'drug-1'
    """

    def __init__(self, edge_id=None, source=None, target=None, properties=None):
        """If no edge_id is provided, generate a UUID."""
        self._id = edge_id or str(uuid.uuid4())
        self.source = source  # node_id or Node instance
        self.target = target  # node_id or Node instance
        self.properties = properties or {}

    @property
    def get_id(self):
        """Unique identifier for this edge."""
        return self._id

    @property
    def get_id_bytes(self):
        """Return the edge ID encoded as UTF-8 bytes.

        Examples:
            >>> Edge(edge_id="d1-p1").get_id_bytes
            b'd1-p1'
        """
        return self._id.encode('utf-8')

    @property
    def get_type(self):
        """Return the typed traversal edge type.

        Examples:
            >>> Edge(properties={"type": "drug-to-protein"}).get_type
            'drug-to-protein'
        """
        return self.properties.get('type')

    def to_dict(self):
        """Convert to a dictionary for serialization."""
        return {
            'id': self._id,
            'source': self.source if isinstance(self.source, str) else self.source.get_id,
            'target': self.target if isinstance(self.target, str) else self.target.get_id,
            'properties': self.properties
        }

    @classmethod
    def from_dict(cls, data: dict):
        """Factory from dictionary."""
        return cls(edge_id=data['id'],
                   source=data['source'],
                   target=data['target'],
                   properties=data['properties'])

class TimeIndexedEdge(Edge):
    """Deprecated edge whose byte key is prefixed by a timestamp.

    This legacy current-state layout is not the immutable temporal storage
    model. Use :meth:`GraphDB.migrate_time_indexed_edges` to append equivalent
    edge history before adopting temporal reads or snapshots.

    Args:
        timestamp_dat: Datetime used as the sortable key prefix.
        *args: Positional arguments passed to ``Edge``.
        **kwargs: Keyword arguments passed to ``Edge``.

    Examples:
        >>> edge = TimeIndexedEdge(datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc), edge_id="e1")
        >>> edge.get_id_bytes.endswith(b':e1')
        True
    """

    def __init__(self, timestamp_dat, *args, **kwargs):
        """Initialize a timestamp-prefixed edge.

        Args:
            timestamp_dat: Datetime used as the sortable key prefix.
            *args: Positional arguments passed to ``Edge``.
            **kwargs: Keyword arguments passed to ``Edge``.
        """
        super().__init__(*args, **kwargs)
        self.timestamp_dat = timestamp_dat
        self.id_string = self.get_id

    @property
    def get_id_bytes(self):
        """Return timestamp-prefixed edge ID bytes.

        Examples:
            >>> edge = TimeIndexedEdge(datetime.datetime(1970, 1, 1, tzinfo=datetime.timezone.utc), edge_id="e1")
            >>> edge.get_id_bytes.endswith(b':e1')
            True
        """
        b2 = self.id_string.encode('utf-8')
        sep = b':'
        b1 = datetime_to_bytes(self.timestamp_dat)
        return b1 + sep + b2

    def to_dict(self):
        """Convert to a dictionary for serialization."""
        return {
            'timestamp_dat' : self.timestamp_dat,
            'id': self._id,
            'source': self.source if isinstance(self.source, str) else self.source.get_id,
            'target': self.target if isinstance(self.target, str) else self.target.get_id,
            'properties': self.properties
        }

    @classmethod
    def from_dict(cls, data: dict):
        """Factory from dictionary."""
        return cls(timestamp_dat = data['timestamp_dat'],
                   edge_id=data['id'],
                   source=data['source'],
                   target=data['target'],
                   properties=data['properties'])

class GraphEntityDictSerializer:
    """Serialize graph entities through a dictionary-compatible serializer.

    Args:
        serializer: Serializer used for the final bytes conversion.

    Examples:
        >>> from gestaltdb.serializers import JSONSerializer
        >>> s = GraphEntityDictSerializer(JSONSerializer())
        >>> s.deserialize(s.serialize(Node(node_id="n1"), "Node"), "Node").get_id
        'n1'
    """

    _ent_type_encoder = {
        'Edge' : lambda x : x.to_dict(),
        'Node' : lambda x : x.to_dict(),
        'AdjacencyList' : lambda x : x
    }

    _ent_type_decoder = {
        'Edge' : lambda x : TimeIndexedEdge.from_dict(x) if 'timestamp_dat' in x else Edge.from_dict(x),
        'Node' : lambda x : Node.from_dict(x),
        'AdjacencyList' : lambda x : x
    }

    def __init__(self, serializer : Serializer):
        """Initialize the entity serializer wrapper.

        Args:
            serializer: Serializer used to encode dictionaries as bytes.
        """
        self.serializer = serializer

    def serialize(self, entity, entity_type : str):
        """Serialize a graph entity by entity type.

        Args:
            entity: ``Node``, ``Edge``, or adjacency-list object.
            entity_type: One of ``"Node"``, ``"Edge"``, or ``"AdjacencyList"``.

        Returns:
            Serialized bytes.

        Examples:
            >>> from gestaltdb.serializers import PickleSerializer
            >>> GraphEntityDictSerializer(PickleSerializer()).serialize(Node("n1"), "Node")[:1]
            b'\\x80'
        """
        enc_obj = self._ent_type_encoder[entity_type](entity)
        return self.serializer.serialize(enc_obj)

    def deserialize(self, val, entity_type : str):
        """ Deserializer (conditional on entity type)

        Args:
            val: bytes containing the data
            entity_type : (str) is Edge, Node, AdjacencyList
        """
        deser_val = self.serializer.deserialize(val)
        return self._ent_type_decoder[entity_type](deser_val)


# Preserve historical pickle paths: streams recorded when these classes lived
# in ``gestaltdb.graphdb`` must keep loading, and new streams should keep the
# historical path until a dedicated persistence migration is designed.
Node.__module__ = "gestaltdb.graphdb"
Edge.__module__ = "gestaltdb.graphdb"
TimeIndexedEdge.__module__ = "gestaltdb.graphdb"
GraphEntityDictSerializer.__module__ = "gestaltdb.graphdb"
