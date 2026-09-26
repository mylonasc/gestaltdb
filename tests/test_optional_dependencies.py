import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.kvstores import LMDBStore, LevelDBStore, PyRexStore
from gestaltdb.serializers import MessagePackSerializer, ProtobufSerializer

from .conftest import blocked_import


def assert_missing_dependency_error(callable_obj, package_name, extra_name):
    with pytest.raises(
        ImportError,
        match=rf"Missing optional dependency '{package_name}'.*gestaltdb\[{extra_name}\]",
    ):
        callable_obj()


def test_lmdb_store_reports_missing_lmdb_when_used():
    with blocked_import("lmdb"):
        assert_missing_dependency_error(lambda: LMDBStore(), "lmdb", "lmdb")


def test_leveldb_store_reports_missing_plyvel_when_used():
    with blocked_import("plyvel"):
        assert_missing_dependency_error(lambda: LevelDBStore(), "plyvel", "leveldb")


def test_pyrex_store_reports_missing_pyrex_when_used():
    with blocked_import("pyrex"):
        assert_missing_dependency_error(lambda: PyRexStore(), "pyrex", "rocksdb")


def test_messagepack_serializer_reports_missing_msgpack_when_used():
    with blocked_import("msgpack"):
        assert_missing_dependency_error(lambda: MessagePackSerializer().serialize({"name": "Alice"}), "msgpack", "msgpack")


def test_protobuf_serializer_reports_missing_protobuf_when_used():
    with blocked_import("google.protobuf"):
        assert_missing_dependency_error(lambda: ProtobufSerializer().serialize({"name": "Alice"}), "protobuf", "protobuf")


def test_create_preflights_backend_before_overwrite(tmp_path):
    path = tmp_path / "existing"
    path.mkdir()
    sentinel = path / "keep.txt"
    sentinel.write_text("keep", encoding="utf-8")

    with blocked_import("plyvel"):
        assert_missing_dependency_error(
            lambda: GraphDB.create(path, backend="leveldb", overwrite=True),
            "plyvel",
            "leveldb",
        )

    assert sentinel.read_text(encoding="utf-8") == "keep"


def test_create_preflights_serializer_before_creating_directory(tmp_path):
    path = tmp_path / "new"

    with blocked_import("msgpack"):
        assert_missing_dependency_error(
            lambda: GraphDB.create(path, backend="leveldb", serializer="messagepack"),
            "msgpack",
            "msgpack",
        )

    assert not path.exists()
