import pytest

from gestaltdb.kvstores import LMDBStore, LevelDBStore, PyRexStore
from gestaltdb.serializers import MessagePackSerializer, ProtobufSerializer

from .conftest import blocked_import


def assert_missing_dependency_error(callable_obj, package_name):
    with pytest.raises(
        ImportError,
        match=f"Missing optional dependency '{package_name}'.*python -m pip install {package_name}.*uv add {package_name}",
    ):
        callable_obj()


def test_lmdb_store_reports_missing_lmdb_when_used():
    with blocked_import("lmdb"):
        assert_missing_dependency_error(lambda: LMDBStore(), "lmdb")


def test_leveldb_store_reports_missing_plyvel_when_used():
    with blocked_import("plyvel"):
        assert_missing_dependency_error(lambda: LevelDBStore(), "plyvel")


def test_pyrex_store_reports_missing_pyrex_when_used():
    with blocked_import("pyrex"):
        with pytest.raises(
            ImportError,
            match="Missing optional dependency 'pyrex'.*python -m pip install pyrex-rocksdb.*uv add pyrex-rocksdb",
        ):
            PyRexStore()


def test_messagepack_serializer_reports_missing_msgpack_when_used():
    with blocked_import("msgpack"):
        assert_missing_dependency_error(lambda: MessagePackSerializer().serialize({"name": "Alice"}), "msgpack")


def test_protobuf_serializer_reports_missing_protobuf_when_used():
    with blocked_import("google.protobuf"):
        assert_missing_dependency_error(lambda: ProtobufSerializer().serialize({"name": "Alice"}), "protobuf")
