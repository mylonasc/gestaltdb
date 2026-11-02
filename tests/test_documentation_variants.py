"""Each declared tab is real executable Python, not an untested string swap."""

import ast
from pathlib import Path
import runpy

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = runpy.run_path(str(ROOT / "docs/_ext/database_examples.py"))


def all_specs():
    return [spec for page in HELPER["declarations"]()
            for spec in HELPER["page_specifications"]((ROOT / f"docs/{page}.rst").read_text(), page)]


SPECS = all_specs()
CASES = [(spec, variant) for spec in SPECS if spec.get("render", True)
         for variant in HELPER["variants"](spec)]


def test_every_explicit_database_or_codec_example_has_a_disposition():
    expected = set()
    for path in (ROOT / "docs").glob("*.rst"):
        for index, match in enumerate(HELPER["ROOT_PYTHON"].finditer(path.read_text())):
            import textwrap
            try:
                tree = ast.parse(textwrap.dedent(match[1]))
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and (
                    isinstance(node.func, ast.Attribute) and ast.unparse(node.func) == "GraphDB.create"
                    or isinstance(node.func, ast.Name) and node.func.id in {
                        "GraphDB", "JSONSerializer", "PickleSerializer", "MessagePackSerializer", "ProtobufSerializer",
                    }
                ):
                    expected.add(f"{path.stem}-{index}")
    assert {spec["id"] for spec in SPECS} == expected
    assert all(spec["reason"] for spec in SPECS)


@pytest.mark.parametrize("spec,variant", CASES,
                         ids=[f"{spec['id']}-{v['backend']}-{v['codec']}" for spec, v in CASES])
def test_documentation_tab(spec, variant, tmp_path, monkeypatch):
    ast.parse(variant["code"])
    if spec["fragment"]:
        # Notebook/setup fragments retain caller state and are not falsely
        # executed as independent programs or allowed to create fixed paths.
        return
    backend = variant["backend"]
    if backend:
        dependency = {"lmdb": "lmdb", "leveldb": "plyvel", "pyrex": "pyrex", "pyrex-transactional": "pyrex"}[backend]
        module = pytest.importorskip(dependency)
        if backend == "pyrex-transactional" and not getattr(module, "has_transactions", False):
            pytest.skip("PyRex runtime has no transaction capability")
    if variant["codec"] in {"messagepack", "protobuf"}:
        pytest.importorskip("msgpack" if variant["codec"] == "messagepack" else "google.protobuf")
    for extra in spec["extras"]:
        pytest.importorskip({"arrow": "pyarrow", "polars": "polars", "networkx": "networkx"}[extra])
    monkeypatch.chdir(tmp_path)
    exec(compile(variant["code"], spec["id"], "exec"), {})
    assert not list(tmp_path.iterdir()), "A standalone tab should clean its disposable stores/artifacts"


def test_capability_boundaries_are_explicit():
    by_id = {spec["id"]: spec for spec in SPECS}
    for name in ("storage-backends-2", "storage-backends-3", "cypher-tutorial-1", "reasoning-current-admission-0"):
        assert set(by_id[name]["backends"]) == {"lmdb", "pyrex-transactional"}
    for name in ("reasoning-native-claims-0", "reasoning-native-locator-claims-0"):
        assert by_id[name]["backends"] == ["pyrex"]
    assert "json" not in by_id["serializers-1"]["codecs"]
    assert by_id["performance-0"]["render"] is False


def test_variant_rewrite_does_not_change_unrelated_strings_or_unicode():
    code = 'from gestaltdb.graphdb import GraphDB\nname = "α"\nnote = "lmdb"\ng = GraphDB.create("db", backend="lmdb", serializer="json")'
    changed = HELPER["render_code"](code, "pyrex-transactional", "messagepack")
    assert 'note = "lmdb"' in changed and 'name = "α"' in changed
    assert "transactional" in changed and "messagepack" in changed
    ast.parse(changed)
