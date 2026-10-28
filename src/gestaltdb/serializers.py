# =========================================
# 1) Serializer Interfaces and Implementations
# =========================================
import pickle
import json
import base64
from datetime import date, datetime
from typing import Any, cast

from .temporal import (
    TemporalDate,
    TemporalDuration,
    TemporalInstant,
    TemporalLocalDateTime,
    TemporalLocalTime,
    TemporalTime,
)


_TYPE_KEY = "__gestaltdb_type__"
_VALUE_KEY = "value"
_TEMPORAL_TYPES = (
    TemporalDate,
    TemporalDuration,
    TemporalInstant,
    TemporalLocalDateTime,
    TemporalLocalTime,
    TemporalTime,
)
_TEMPORAL_VALUE_TYPES = {
    "temporal_date",
    "temporal_duration",
    "temporal_instant",
    "temporal_local_datetime",
    "temporal_local_time",
    "temporal_time",
}
_TAGGED_VALUE_TYPES = _TEMPORAL_VALUE_TYPES | {"escaped_dict"}


def temporal_tagged_value(value: TemporalDate | TemporalDuration | TemporalInstant | TemporalLocalDateTime | TemporalLocalTime | TemporalTime, *, canonical_time: bool = False) -> dict[str, object]:
    """Return a portable tagged representation for a temporal scalar."""
    value_type: str
    payload: object
    if isinstance(value, TemporalDate):
        value_type, payload = "temporal_date", value.value.isoformat()
    elif isinstance(value, TemporalLocalTime):
        value_type, payload = "temporal_local_time", value.microseconds
    elif isinstance(value, TemporalTime):
        value_type = "temporal_time"
        payload = value.utc_microseconds if canonical_time else {
            "local_microseconds": value.local_microseconds,
            "offset_seconds": value.offset_seconds,
        }
    elif isinstance(value, TemporalInstant):
        value_type, payload = "temporal_instant", value.epoch_microseconds
    elif isinstance(value, TemporalLocalDateTime):
        value_type, payload = "temporal_local_datetime", value.value.isoformat(timespec="microseconds")
    elif isinstance(value, TemporalDuration):
        value_type, payload = "temporal_duration", value.total_microseconds
    else:
        raise TypeError("value must be a GestaltDB temporal scalar")
    return {_TYPE_KEY: value_type, _VALUE_KEY: payload}


def _to_storage_compatible(obj: object) -> object:
    if isinstance(obj, _TEMPORAL_TYPES):
        return temporal_tagged_value(obj)
    if isinstance(obj, dict):
        if (
            set(obj) == {_TYPE_KEY, _VALUE_KEY}
            and isinstance(obj[_TYPE_KEY], str)
            and obj[_TYPE_KEY] in _TAGGED_VALUE_TYPES
        ):
            return {
                _TYPE_KEY: "escaped_dict",
                _VALUE_KEY: {key: _to_storage_compatible(value) for key, value in obj.items()},
            }
        return {key: _to_storage_compatible(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_to_storage_compatible(value) for value in obj]
    if isinstance(obj, tuple):
        return tuple(_to_storage_compatible(value) for value in obj)
    return obj


def _decode_temporal_tag(value_type: str, value: Any) -> object:
    try:
        if value_type == "temporal_date":
            return TemporalDate(date.fromisoformat(value))
        if value_type == "temporal_local_time":
            return TemporalLocalTime(value)
        if value_type == "temporal_time":
            return TemporalTime(value["local_microseconds"], value["offset_seconds"])
        if value_type == "temporal_instant":
            return TemporalInstant(value)
        if value_type == "temporal_local_datetime":
            return TemporalLocalDateTime(datetime.fromisoformat(value))
        if value_type == "temporal_duration":
            return TemporalDuration(value)
    except (AttributeError, KeyError, OverflowError, TypeError, ValueError) as exc:
        raise ValueError(f"malformed {value_type} payload in serialized data") from exc
    raise AssertionError(f"unhandled tagged value type {value_type!r}")


def _from_storage_compatible(obj: object) -> object:
    if isinstance(obj, dict):
        if set(obj) == {_TYPE_KEY, _VALUE_KEY}:
            value_type = obj[_TYPE_KEY]
            value = obj[_VALUE_KEY]
            if value_type == "escaped_dict":
                if not isinstance(value, dict):
                    raise ValueError("malformed escaped_dict payload in serialized data")
                return {key: _from_storage_compatible(item) for key, item in value.items()}
            if isinstance(value_type, str) and value_type in _TEMPORAL_VALUE_TYPES:
                return _decode_temporal_tag(value_type, value)
        return {key: _from_storage_compatible(value) for key, value in obj.items()}
    if isinstance(obj, list):
        return [_from_storage_compatible(value) for value in obj]
    if isinstance(obj, tuple):
        return tuple(_from_storage_compatible(value) for value in obj)
    return obj


def _missing_dependency_error(package_name: str, *, extra_name: str, feature_name: str | None = None) -> ImportError:
    """Build a consistent optional dependency error.

    Args:
        package_name: Import package that is missing.
        extra_name: GestaltDB extra that installs the package.
        feature_name: Feature that requires the package.

    Returns:
        ImportError describing how to install the dependency.

    Examples:
        >>> "msgpack" in str(_missing_dependency_error("msgpack", extra_name="msgpack"))
        True
    """
    feature_name = feature_name or package_name
    return ImportError(
        f"Missing optional dependency '{package_name}' required for {feature_name}. "
        f"Install it with `python -m pip install 'gestaltdb[{extra_name}]'` "
        f"or `uv add 'gestaltdb[{extra_name}]'`."
    )

class Serializer:
    """Abstract base for serialization/deserialization."""
    def serialize(self, obj: dict) -> bytes:
        """Serialize a dictionary-like object to bytes.

        Args:
            obj: Object to serialize.

        Returns:
            Serialized bytes.
        """
        raise NotImplementedError
    
    def deserialize(self, data: bytes) -> dict:
        """Deserialize bytes into a dictionary-like object.

        Args:
            data: Serialized bytes.

        Returns:
            Decoded object.
        """
        raise NotImplementedError


class PickleSerializer(Serializer):
    """Uses Python's pickle for serialization."""
    def serialize(self, obj: dict) -> bytes:
        """Serialize an object with pickle.

        Examples:
            >>> PickleSerializer().deserialize(PickleSerializer().serialize({"a": 1}))
            {'a': 1}
        """
        return pickle.dumps(_to_storage_compatible(obj))
    
    def deserialize(self, data: bytes) -> dict:
        """Deserialize pickle bytes.

        Examples:
            >>> PickleSerializer().deserialize(PickleSerializer().serialize({"a": 1}))
            {'a': 1}
        """
        return cast(dict, _from_storage_compatible(pickle.loads(data)))


class JSONSerializer(Serializer):
    """Encode supported property payloads as UTF-8 JSON bytes.

    Examples:
        Round-trip a dictionary independently of any storage backend::

            >>> from gestaltdb.serializers import JSONSerializer
            >>> serializer = JSONSerializer()
            >>> payload = {"name": "Alice", "scores": [1, 2]}
            >>> encoded = serializer.serialize(payload)
            >>> assert isinstance(encoded, bytes)
            >>> assert serializer.deserialize(encoded) == payload

    ``GraphDB.create(..., serializer="json")`` selects this implementation.
    See the serializers guide for temporal property values and optional encodings.
    """
    def serialize(self, obj: dict) -> bytes:
        """Serialize a JSON-compatible object.

        Examples:
            >>> JSONSerializer().serialize({"a": 1})
            b'{"a": 1}'
        """
        return json.dumps(_to_storage_compatible(obj)).encode('utf-8')
    
    def deserialize(self, data: bytes) -> dict:
        """Deserialize JSON bytes.

        Examples:
            >>> JSONSerializer().deserialize(b'{"a": 1}')
            {'a': 1}
        """
        return cast(dict, _from_storage_compatible(json.loads(data.decode('utf-8'))))


class MessagePackSerializer(Serializer):
    """Uses MessagePack for serialization."""

    @staticmethod
    def ensure_available() -> Any:
        """Import and return MessagePack, or raise installation guidance."""
        try:
            import msgpack
        except ImportError as exc:
            raise _missing_dependency_error("msgpack", extra_name="msgpack", feature_name="MessagePackSerializer") from exc
        return msgpack

    def serialize(self, obj: dict) -> bytes:
        """Serialize an object with MessagePack.

        Raises:
            ImportError: If the optional ``msgpack`` package is missing.

        Examples:
            >>> MessagePackSerializer().deserialize(MessagePackSerializer().serialize({"a": 1}))
            {'a': 1}
        """
        msgpack = self.ensure_available()
        return msgpack.packb(_to_storage_compatible(obj), use_bin_type=True)

    def deserialize(self, data: bytes) -> dict:
        """Deserialize MessagePack bytes.

        Raises:
            ImportError: If the optional ``msgpack`` package is missing.

        Examples:
            >>> MessagePackSerializer().deserialize(MessagePackSerializer().serialize({"a": 1}))
            {'a': 1}
        """
        msgpack = self.ensure_available()
        return cast(dict, _from_storage_compatible(msgpack.unpackb(data, raw=False)))


class ProtobufSerializer(Serializer):
    """Uses google.protobuf Struct for JSON-like dictionaries.

    Struct does not have native integer or bytes types. This serializer tags those
    values before encoding so Python dictionaries round-trip without losing them.
    """

    _TYPE_KEY = _TYPE_KEY
    _VALUE_KEY = _VALUE_KEY

    @staticmethod
    def ensure_available() -> Any:
        """Import and return protobuf helpers, or raise installation guidance."""
        try:
            from google.protobuf import json_format, struct_pb2
        except ImportError as exc:
            raise _missing_dependency_error("protobuf", extra_name="protobuf", feature_name="ProtobufSerializer") from exc
        return json_format, struct_pb2

    def serialize(self, obj: dict) -> bytes:
        """Serialize a JSON-like dictionary with protobuf Struct.

        Args:
            obj: Dictionary containing JSON-like values plus tagged ints/bytes.

        Returns:
            Protobuf binary payload.

        Raises:
            ImportError: If the optional ``protobuf`` package is missing.
        """
        json_format, struct_pb2 = self.ensure_available()

        message = struct_pb2.Struct()
        json_format.ParseDict(self._to_struct_compatible(_to_storage_compatible(obj)), message)
        return message.SerializeToString()

    def deserialize(self, data: bytes) -> dict:
        """Deserialize protobuf Struct bytes.

        Args:
            data: Protobuf binary payload.

        Returns:
            Decoded dictionary.

        Raises:
            ImportError: If the optional ``protobuf`` package is missing.
        """
        json_format, struct_pb2 = self.ensure_available()

        message = struct_pb2.Struct()
        message.ParseFromString(data)
        decoded = self._from_struct_compatible(json_format.MessageToDict(message))
        return cast(dict, _from_storage_compatible(decoded))

    def _to_struct_compatible(self, obj: object) -> object:
        """Convert Python-only values into protobuf Struct-compatible values.

        Args:
            obj: Value to convert recursively.

        Returns:
            Struct-compatible value.
        """
        if isinstance(obj, bytes):
            return {
                self._TYPE_KEY: "bytes",
                self._VALUE_KEY: base64.b64encode(obj).decode("ascii"),
            }
        if isinstance(obj, int) and not isinstance(obj, bool):
            return {
                self._TYPE_KEY: "int",
                self._VALUE_KEY: str(obj),
            }
        if isinstance(obj, dict):
            return {key: self._to_struct_compatible(value) for key, value in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [self._to_struct_compatible(value) for value in obj]
        return obj

    def _from_struct_compatible(self, obj: object) -> object:
        """Restore Python-only values from Struct-compatible tagged values.

        Args:
            obj: Value to convert recursively.

        Returns:
            Restored Python value.
        """
        if isinstance(obj, dict):
            if set(obj) == {self._TYPE_KEY, self._VALUE_KEY}:
                value_type = obj[self._TYPE_KEY]
                value = obj[self._VALUE_KEY]
                if value_type == "bytes":
                    return base64.b64decode(value.encode("ascii"))
                if value_type == "int":
                    return int(value)
            return {key: self._from_struct_compatible(value) for key, value in obj.items()}
        if isinstance(obj, list):
            return [self._from_struct_compatible(value) for value in obj]
        return obj
