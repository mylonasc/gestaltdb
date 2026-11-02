"""Capability-declared sphinx-tabs alternatives over a single source example.

The database-options directive immediately precedes an unchanged Python block.
Tests and non-Sphinx readers can still execute that canonical block. Sphinx
replaces its display with tabs generated from explicitly declared alternatives.
Only literal factory/codec selections are rewritten, never arbitrary strings.
"""

from __future__ import annotations

import ast
import json
from pathlib import Path
import re
import textwrap


BACKENDS = {
    "lmdb": ("LMDB", "lmdb", "LMDBStore", "lmdb"),
    "leveldb": ("LevelDB", "leveldb", "LevelDBStore", "leveldb"),
    "pyrex": ("PyRex", "pyrex", "PyRexStore", "rocksdb"),
    "pyrex-transactional": ("PyRex transactional", "pyrex", "PyRexStore", "rocksdb"),
}
CODECS = {
    "json": ("JSON", "JSONSerializer", None),
    "pickle": ("Pickle (trusted data)", "PickleSerializer", None),
    "messagepack": ("MessagePack", "MessagePackSerializer", "msgpack"),
    "protobuf": ("Protobuf", "ProtobufSerializer", "protobuf"),
}
ROOT_PYTHON = re.compile(r"^\.\. code-block:: python\n\n((?:[ \t]+[^\n]*\n|\n)+)", re.MULTILINE)


def declarations() -> dict:
    return json.loads(Path(__file__).with_name("example_options.json").read_text(encoding="utf-8"))


def page_specifications(source: str, page: str) -> list[dict]:
    blocks = list(ROOT_PYTHON.finditer(source))
    result = []
    for entry in declarations().get(page, []):
        index = entry["index"]
        if index >= len(blocks):
            raise ValueError(f"{page}: missing example {index}")
        block = blocks[index]
        result.append({**entry, "id": f"{page}-{index}",
                       "code": textwrap.dedent(block[1]).rstrip(), "start": block.start(),
                       "backends": entry.get("backends", []), "codecs": entry.get("codecs", []),
                       "extras": entry.get("extras", []), "fragment": entry.get("fragment", False)})
    return result


def variants(spec: dict) -> list[dict]:
    if not spec["backends"] and not spec["codecs"]:
        raise ValueError(f"{spec['id']}: declare at least one variant axis")
    if set(spec["backends"]) - BACKENDS.keys() or set(spec["codecs"]) - CODECS.keys():
        raise ValueError(f"{spec['id']}: unknown backend or serializer")
    if not spec["reason"]:
        raise ValueError(f"{spec['id']}: explain the capability boundary")
    result = []
    for backend in spec["backends"] or [None]:
        for codec in spec["codecs"] or [None]:
            extras = set(spec["extras"])
            if backend:
                extras.add(BACKENDS[backend][3])
            if codec and CODECS[codec][2]:
                extras.add(CODECS[codec][2])
            requirement = "gestaltdb" + ("[" + ",".join(sorted(extras)) + "]" if extras else "")
            result.append({"backend": backend, "codec": codec, "requirement": requirement,
                           "code": render_code(spec["code"], backend, codec)})
    return result


def render_code(code: str, backend: str | None, codec: str | None) -> str:
    """Replace declared factory selections using AST source spans, preserving formatting."""
    tree = ast.parse(code)
    lines = code.splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    edits: dict[tuple[int, int], str] = {}

    def span(node):
        # AST columns are UTF-8 byte offsets, not character offsets.
        start = offsets[node.lineno - 1] + len(lines[node.lineno - 1].encode()[:node.col_offset].decode())
        end = offsets[node.end_lineno - 1] + len(lines[node.end_lineno - 1].encode()[:node.end_col_offset].decode())
        return start, end

    def replace(node, value):
        edits[span(node)] = value

    backend_classes = {value[2] for value in BACKENDS.values()}
    codec_classes = {value[1] for value in CODECS.values()}
    backend_hits = codec_hits = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            factory = isinstance(node.func, ast.Attribute) and isinstance(node.func.value, ast.Name) and node.func.value.id == "GraphDB" and node.func.attr == "create"
            if factory:
                keywords = {item.arg: item for item in node.keywords}
                if backend:
                    value = keywords.get("backend")
                    if value is None or not isinstance(value.value, ast.Constant):
                        raise ValueError("Tabbed factories require an explicit literal backend")
                    replace(value.value, repr(BACKENDS[backend][1]))
                    backend_hits += 1
                    if backend == "pyrex-transactional":
                        if "backend_options" in keywords:
                            raise ValueError("Do not override explicit backend_options; declare a fixed backend")
                        end = span(value.value)[1]
                        edits[(end, end)] = ', backend_options={"transactional": True}'
                if codec:
                    value = keywords.get("serializer")
                    if value is None or not isinstance(value.value, ast.Constant):
                        raise ValueError("Tabbed factories require an explicit literal serializer")
                    replace(value.value, repr(codec))
                    codec_hits += 1
            if backend and isinstance(node.func, ast.Name) and node.func.id in backend_classes:
                if node.keywords and any(item.arg not in {"path"} for item in node.keywords):
                    raise ValueError("Tuned direct-store examples must declare a fixed backend")
                backend_hits += 1
                if backend == "pyrex-transactional":
                    end = span(node)[1] - 1
                    edits[(end, end)] = ', transactional=True'
        if isinstance(node, ast.Name):
            if backend and node.id in backend_classes:
                replace(node, BACKENDS[backend][2])
            if codec and node.id in codec_classes:
                replace(node, CODECS[codec][1])
                if isinstance(node.ctx, ast.Load):
                    codec_hits += 1
        if isinstance(node, ast.ImportFrom):
            for alias in node.names:
                if alias.asname:
                    continue
                if backend and alias.name in backend_classes:
                    replace(alias, BACKENDS[backend][2])
                if codec and alias.name in codec_classes:
                    replace(alias, CODECS[codec][1])
        # Manifest assertions are configuration-dependent, not arbitrary string replacements.
        if isinstance(node, ast.Compare) and len(node.comparators) == 1 and isinstance(node.comparators[0], ast.Constant):
            expression = ast.unparse(node.left)
            if ".manifest[" in expression and expression.endswith("['name']"):
                if backend and "['backend']" in expression:
                    replace(node.comparators[0], repr(BACKENDS[backend][1]))
                if codec and "['serializer']" in expression:
                    replace(node.comparators[0], repr(codec))
    if backend and not backend_hits:
        raise ValueError("Backend alternatives need an explicit factory/store constructor")
    if codec and not codec_hits:
        raise ValueError("Serializer alternatives need an explicit factory/codec constructor")
    for (start, end), value in sorted(edits.items(), reverse=True):
        code = code[:start] + value + code[end:]
    ast.parse(code)
    return code


def tab_source(spec: dict) -> str:
    rows = variants(spec)
    output = [spec["reason"], "", ".. tabs::", ""]
    for backend in spec["backends"] or [None]:
        label = BACKENDS[backend][0] if backend else "Serializer choices"
        output += [f"   .. tab:: {label}", "", "      .. tabs::", ""]
        for row in [row for row in rows if row["backend"] == backend]:
            label = CODECS[row["codec"]][0] if row["codec"] else "Configured serializer"
            output += [f"         .. tab:: {label}", "", "            .. code-block:: sh", "",
                       f'               python -m pip install "{row["requirement"]}"', "",
                       "            .. code-block:: python", ""]
            output += ["               " + line if line else "" for line in row["code"].splitlines()]
            output.append("")
    return "\n".join(output)


def setup(app):
    from docutils import nodes
    from docutils.parsers.rst import directives
    from docutils.statemachine import StringList
    from sphinx.errors import SphinxError
    from sphinx.util.docutils import SphinxDirective

    class DatabaseOptions(SphinxDirective):
        required_arguments = 1
        option_spec = {name: directives.unchanged_required for name in
                       ("backends", "codecs", "extras", "reason", "fragment")}

        def run(self):
            path = Path(self.env.doc2path(self.env.docname))
            specs = page_specifications(path.read_text(encoding="utf-8"), self.env.docname)
            spec = next(item for item in specs if item["id"] == self.arguments[0])
            container = nodes.container(classes=["database-example-tabs"])
            self.state.nested_parse(StringList(tab_source(spec).splitlines(), source=str(path)), self.content_offset, container)
            marker = nodes.comment("", "database-options canonical source follows")
            marker["canonical_database_example"] = spec["code"]
            return [container, marker]

    def add_declared_options(app, docname, source):
        for spec in reversed(page_specifications(source[0], docname)):
            if spec.get("render", True):
                position = spec["start"]
                source[0] = source[0][:position] + f".. database-options:: {spec['id']}\n\n" + source[0][position:]

    def remove_canonical_display(app, doctree):
        for marker in list(doctree.findall(nodes.comment)):
            if "canonical_database_example" not in marker:
                continue
            parent = marker.parent
            index = parent.index(marker)
            following = parent[index + 1] if index + 1 < len(parent) else None
            if not isinstance(following, nodes.literal_block) or following.astext().rstrip() != marker["canonical_database_example"]:
                raise SphinxError("database-options must precede its canonical Python block directly")
            parent.remove(following)
            parent.remove(marker)

    app.add_directive("database-options", DatabaseOptions)
    app.connect("source-read", add_declared_options)
    app.connect("doctree-read", remove_canonical_display)
    return {"parallel_read_safe": True, "parallel_write_safe": True}
