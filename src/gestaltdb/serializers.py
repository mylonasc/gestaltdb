# =========================================
# 1) Serializer Interfaces and Implementations
# =========================================
import pickle
import json
import base64


def _missing_dependency_error(package_name, install_name=None, feature_name=None):
    """Build a consistent optional dependency error.

    Args:
        package_name: Import package that is missing.
        install_name: Optional package name to show in install commands.
        feature_name: Feature that requires the package.

    Returns:
        ImportError describing how to install the dependency.

    Examples:
        >>> "msgpack" in str(_missing_dependency_error("msgpack"))
        True
    """
    install_name = install_name or package_name
    feature_name = feature_name or package_name
    return ImportError(
        f"Missing optional dependency '{package_name}' required for {feature_name}. "
        f"Install it with `python -m pip install {install_name}` or `uv add {install_name}`."
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
        return pickle.dumps(obj)
    
    def deserialize(self, data: bytes) -> dict:
        """Deserialize pickle bytes.

        Examples:
            >>> PickleSerializer().deserialize(PickleSerializer().serialize({"a": 1}))
            {'a': 1}
        """
        return pickle.loads(data)


class JSONSerializer(Serializer):
    """Uses JSON for serialization."""
    def serialize(self, obj: dict) -> bytes:
        """Serialize a JSON-compatible object.

        Examples:
            >>> JSONSerializer().serialize({"a": 1})
            b'{"a": 1}'
        """
        return json.dumps(obj).encode('utf-8')
    
    def deserialize(self, data: bytes) -> dict:
        """Deserialize JSON bytes.

        Examples:
            >>> JSONSerializer().deserialize(b'{"a": 1}')
            {'a': 1}
        """
        return json.loads(data.decode('utf-8'))


class MessagePackSerializer(Serializer):
    """Uses MessagePack for serialization."""
    def serialize(self, obj: dict) -> bytes:
        """Serialize an object with MessagePack.

        Raises:
            ImportError: If the optional ``msgpack`` package is missing.

        Examples:
            >>> MessagePackSerializer().deserialize(MessagePackSerializer().serialize({"a": 1}))
            {'a': 1}
        """
        try:
            import msgpack
        except ImportError as exc:
            raise _missing_dependency_error("msgpack", feature_name="MessagePackSerializer") from exc
        return msgpack.packb(obj, use_bin_type=True)

    def deserialize(self, data: bytes) -> dict:
        """Deserialize MessagePack bytes.

        Raises:
            ImportError: If the optional ``msgpack`` package is missing.

        Examples:
            >>> MessagePackSerializer().deserialize(MessagePackSerializer().serialize({"a": 1}))
            {'a': 1}
        """
        try:
            import msgpack
        except ImportError as exc:
            raise _missing_dependency_error("msgpack", feature_name="MessagePackSerializer") from exc
        return msgpack.unpackb(data, raw=False)


class ProtobufSerializer(Serializer):
    """Uses google.protobuf Struct for JSON-like dictionaries.

    Struct does not have native integer or bytes types. This serializer tags those
    values before encoding so Python dictionaries round-trip without losing them.
    """

    _TYPE_KEY = "__gestaltdb_type__"
    _VALUE_KEY = "value"

    def serialize(self, obj: dict) -> bytes:
        """Serialize a JSON-like dictionary with protobuf Struct.

        Args:
            obj: Dictionary containing JSON-like values plus tagged ints/bytes.

        Returns:
            Protobuf binary payload.

        Raises:
            ImportError: If the optional ``protobuf`` package is missing.
        """
        try:
            from google.protobuf import json_format, struct_pb2
        except ImportError as exc:
            raise _missing_dependency_error("protobuf", feature_name="ProtobufSerializer") from exc

        message = struct_pb2.Struct()
        json_format.ParseDict(self._to_struct_compatible(obj), message)
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
        try:
            from google.protobuf import json_format, struct_pb2
        except ImportError as exc:
            raise _missing_dependency_error("protobuf", feature_name="ProtobufSerializer") from exc

        message = struct_pb2.Struct()
        message.ParseFromString(data)
        return self._from_struct_compatible(json_format.MessageToDict(message))

    def _to_struct_compatible(self, obj):
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

    def _from_struct_compatible(self, obj):
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
