"""Build CDM records from the ``finos-cdm`` pydantic distribution.

Primary source for the schema graph: ``pip install finos-cdm`` ships every CDM
type as a Pydantic v2 class (``finos.cdm.<namespace>.<Type>``), generated from
the authoritative Rune model.  This avoids network downloads and fragile HTML
scraping, and additionally exposes real Python inheritance hierarchies
(``Trade`` extends ``TradableProduct``) that the JSON schemas do not spell out.

Documentation is retained from three levels:

* class descriptions: bare-string statements in the generated class bodies
  (PEP 258 style; ``__doc__`` is empty for models, set for enums),
* field descriptions: ``Field(description=...)`` / ``model_fields[].description``,
* enum member descriptions: bare strings after each member assignment.

The output reuses :class:`CdmTypeRecord` / :class:`CdmParseResult`, so
:mod:`cdm_schema_graph.graph_builder` works unchanged.  Every record is
stamped with :data:`PARSER_VERSION` and ``source="pydantic"`.
"""

from __future__ import annotations

import ast
import enum
import importlib
import importlib.metadata
import inspect
import pkgutil
import re
import types
import typing
from dataclasses import dataclass

from .parser import (
    CDM_VERSION,
    PARSER_VERSION,
    CdmParseResult,
    CdmProperty,
    CdmTypeRecord,
    _CHOICE_HINT_RE,
    coarse_namespace_for,
    record_key,
)

#: Source label recorded on parse results and graph payloads.
CDM_SOURCE = "pydantic"

_ARRAY_ORIGINS = {list, tuple, set, frozenset}


def cdm_package_version() -> str:
    """Return the installed ``finos-cdm`` version, falling back to CDM_VERSION."""
    try:
        return importlib.metadata.version("finos-cdm")
    except importlib.metadata.PackageNotFoundError:
        return CDM_VERSION


def ensure_cdm_importable():
    """Import the lazy bundle first to avoid circular-import failures."""
    import finos._bundle  # initializes lazy re-exports
    import finos.cdm

    return finos.cdm


@dataclass
class _DiscoveredModel:
    canonical_name: str
    module_name: str
    cls: type


def discover_model_modules(limit: int | None = None) -> list[str]:
    """List candidate ``finos.cdm`` modules (capitalized leaf = type module)."""
    ensure_cdm_importable()
    import finos.cdm

    modules = [
        m.name
        for m in pkgutil.walk_packages(finos.cdm.__path__, "finos.cdm.")
        if m.name.rsplit(".", 1)[-1][:1].isupper()
    ]
    modules.sort()
    return modules[:limit] if limit is not None else modules


def load_models(
    module_names: list[str] | None = None,
    limit: int | None = None,
) -> tuple[dict[str, _DiscoveredModel], list[str]]:
    """Import model modules; return key -> model and skipped module names.

    Keys are canonical type names, qualified with the namespace slug on the
    rare collision (e.g. a type defined in two namespaces).
    """
    ensure_cdm_importable()
    from pydantic import BaseModel

    if module_names is None:
        module_names = discover_model_modules(limit=limit)
    elif limit is not None:
        module_names = module_names[:limit]

    models: dict[str, _DiscoveredModel] = {}
    taken: set[str] = set()
    skipped: list[str] = []
    for module_name in module_names:
        try:
            module = importlib.import_module(module_name)
        except Exception:
            skipped.append(module_name)
            continue
        leaf = module_name.rsplit(".", 1)[-1]
        obj = getattr(module, leaf, None)
        if not (
            isinstance(obj, type)
            and (issubclass(obj, BaseModel) or issubclass(obj, enum.Enum))
        ):
            skipped.append(module_name)
            continue
        namespace_slug = "cdm-" + "-".join(module_name.split(".")[2:-1])
        key = record_key(namespace_slug, leaf, taken)
        taken.add(key)
        models[key] = _DiscoveredModel(
            canonical_name=leaf, module_name=module_name, cls=obj
        )
    return models, skipped


# -- documentation extraction ---------------------------------------------

_module_doc_cache: dict[str, dict[str, dict]] = {}


def _module_class_docs(module_name: str) -> dict[str, dict]:
    """Map mangled class name -> {"doc": str|None, "members": {name: doc}}.

    Generated classes carry PEP 258 bare-string statements: the first one in a
    class body documents the class, and a string following a member assignment
    documents that member.  Parsed once per module via ``ast``.
    """
    if module_name in _module_doc_cache:
        return _module_doc_cache[module_name]
    docs: dict[str, dict] = {}
    try:
        source = inspect.getsource(importlib.import_module(module_name))
        tree = ast.parse(source)
    except Exception:
        _module_doc_cache[module_name] = docs
        return docs
    for node in tree.body:
        if not isinstance(node, ast.ClassDef):
            continue
        class_doc: str | None = None
        members: dict[str, str] = {}
        pending: str | None = None
        for stmt in node.body:
            if (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and isinstance(stmt.value.value, str)
            ):
                text = stmt.value.value.strip()
                if class_doc is None:
                    # Generator convention: the first bare string documents
                    # the class itself (before any member documentation).
                    class_doc = text
                elif pending is not None:
                    members[pending] = text
                pending = None
                continue
            pending = None
            if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                pending = stmt.target.id
            elif (
                isinstance(stmt, ast.Assign)
                and len(stmt.targets) == 1
                and isinstance(stmt.targets[0], ast.Name)
                and isinstance(stmt.value, ast.Constant)
            ):
                # Enum members are plain ``NAME = "VALUE"`` assignments.
                pending = stmt.targets[0].id
        docs[node.name] = {"doc": class_doc, "members": members}
    _module_doc_cache[module_name] = docs
    return docs


def class_description(model: _DiscoveredModel) -> str | None:
    """Class description: ``__doc__`` (enums) else AST bare-string (models).

    Some modules define their class locally (``class Asset``), others re-export
    the bundle class (``Trade = finos_cdm_event_common_Trade``); both the
    importing module and the defining module are searched.
    """
    doc = (model.cls.__doc__ or "").strip() or None
    if doc:
        return doc
    candidates = [
        (model.module_name, model.canonical_name),
        (model.module_name, model.cls.__name__),
        (getattr(model.cls, "__module__", "") or "", model.cls.__name__),
    ]
    seen: set[tuple[str, str]] = set()
    for module_name, class_name in candidates:
        if not module_name or (module_name, class_name) in seen:
            continue
        seen.add((module_name, class_name))
        entry = _module_class_docs(module_name).get(class_name)
        if entry and entry["doc"]:
            return entry["doc"]
    return None


def enum_member_docs(model: _DiscoveredModel) -> dict[str, str]:
    """Map enum member name -> bare-string description from class source."""
    candidates = [
        (model.module_name, model.canonical_name),
        (model.module_name, model.cls.__name__),
        (getattr(model.cls, "__module__", "") or "", model.cls.__name__),
    ]
    seen: set[tuple[str, str]] = set()
    for module_name, class_name in candidates:
        if not module_name or (module_name, class_name) in seen:
            continue
        seen.add((module_name, class_name))
        entry = _module_class_docs(module_name).get(class_name)
        if entry and entry["members"]:
            return dict(entry["members"])
    return {}


_CHOICE_CONDITION_RE = re.compile(r"condition_\d+_Choice")


def demangle(name: str) -> str:
    """Turn a bundle-mangled name into its likely canonical type name."""
    if name.startswith("finos_cdm_"):
        return name[len("finos_cdm_"):].rsplit("_", 1)[-1]
    return name


def is_choice_type(cls: type, description: str | None) -> bool:
    """Detect Rune choice types: description hint or generated Choice condition."""
    if description and _CHOICE_HINT_RE.search(description):
        return True
    try:
        namespace = vars(cls)
    except TypeError:
        return False
    return any(_CHOICE_CONDITION_RE.fullmatch(name) for name in namespace)


# -- annotation analysis ----------------------------------------------------


def _strip_annotated(tp):
    while typing.get_origin(tp) is typing.Annotated:
        tp = typing.get_args(tp)[0]
    return tp


def _split_union(tp) -> list:
    origin = typing.get_origin(tp)
    if origin is typing.Union or (types.UnionType is not None and origin is types.UnionType):
        flattened: list = []
        for arg in typing.get_args(tp):
            flattened.extend(_split_union(arg))
        return flattened
    return [tp]


def _unwrap_annotation(tp) -> tuple[bool, list]:
    """Return (is_array, candidate member types) for a field annotation."""
    tp = _strip_annotated(tp)
    is_array = False
    members = _split_union(tp)
    if len(members) == 1:
        member = _strip_annotated(members[0])
        if typing.get_origin(member) in _ARRAY_ORIGINS:
            is_array = True
            args = [_strip_annotated(a) for a in typing.get_args(member)]
            members = [a for inner in args for a in _split_union(inner)]
    return is_array, [m for m in members if m is not type(None)]


def _min_max_lengths(field_info) -> tuple[int | None, int | None]:
    """Extract min/max collection length from Annotated metadata."""
    min_items: int | None = None
    max_items: int | None = None
    for meta in getattr(field_info, "metadata", []) or []:
        for attr in ("min_length", "min_items"):
            value = getattr(meta, attr, None)
            if isinstance(value, int) and not isinstance(value, bool):
                min_items = value if min_items is None else min(min_items, value)
        for attr in ("max_length", "max_items"):
            value = getattr(meta, attr, None)
            if isinstance(value, int) and not isinstance(value, bool):
                max_items = value if max_items is None else max(max_items, value)
    return min_items, max_items


def _primitive_name(tp) -> str:
    if tp is None:
        return "unknown"
    name = getattr(tp, "__name__", None)
    return name if isinstance(name, str) else str(tp)


@dataclass
class _ModelIndex:
    by_key: dict[str, _DiscoveredModel]
    by_id: dict[int, str]
    by_name: dict[str, list[str]]

    @classmethod
    def build(cls, models: dict[str, _DiscoveredModel]) -> _ModelIndex:
        by_id: dict[int, str] = {}
        by_name: dict[str, list[str]] = {}
        for key, model in models.items():
            by_id[id(model.cls)] = key
            by_name.setdefault(model.canonical_name, []).append(key)
        return cls(by_key=models, by_id=by_id, by_name=by_name)

    def key_for_class(self, cls: type) -> str | None:
        """Exact record key for an annotation class, via identity then name."""
        key = self.by_id.get(id(cls))
        if key is not None:
            return key
        name = getattr(cls, "__name__", None)
        mangled = name or ""
        if mangled.startswith("finos_cdm_"):
            tail = mangled[len("finos_cdm_"):]
            guess = tail.rsplit("_", 1)[-1]
            candidates = self.by_name.get(guess, [])
            if len(candidates) == 1:
                return candidates[0]
        candidates = self.by_name.get(name, [])
        return candidates[0] if len(candidates) == 1 else None


def _is_cdm_class(tp) -> bool:
    module = getattr(tp, "__module__", "") or ""
    return module.startswith("finos.cdm") or module == "finos._bundle"


def model_to_record(
    key: str,
    model: _DiscoveredModel,
    index: _ModelIndex,
    cdm_version: str,
) -> CdmTypeRecord:
    """Convert one discovered model class into a CdmTypeRecord."""
    from pydantic import BaseModel

    tokens = ["cdm", *model.module_name.split(".")[2:-1]]
    namespace_slug = "-".join(tokens)
    is_enum = isinstance(model.cls, enum.EnumMeta)
    if not is_enum:
        try:
            model.cls.model_rebuild(force=True)
        except Exception:
            pass

    if is_enum:
        members = enum_member_docs(model)
        values = [m.name for m in model.cls]
        return CdmTypeRecord(
            file=model.module_name + ".py",
            type_name=model.canonical_name,
            namespace_tokens=tokens,
            namespace_slug=namespace_slug,
            namespace_anchor=".".join(tokens),
            coarse_namespace=coarse_namespace_for(tokens),
            title=model.canonical_name,
            description=class_description(model),
            kind="enum",
            properties=[],
            enum_values=values,
            enum_details=[
                {"value": m.name, "title": m.name, "description": members.get(m.name)}
                for m in model.cls
            ],
            parser_version=PARSER_VERSION,
        )

    description = class_description(model)
    kind = "choice" if is_choice_type(model.cls, description) else "object"
    resolved_bases: list[str] = []
    for base in model.cls.__bases__:
        if not (isinstance(base, type) and issubclass(base, BaseModel)):
            continue
        if not _is_cdm_class(base):
            continue
        if base.__name__.startswith("finos_cdm_"):
            target_key = index.key_for_class(base)
            if target_key is not None:
                resolved_bases.append(index.by_key[target_key].canonical_name)
            else:
                resolved_bases.append(demangle(base.__name__))
        else:
            resolved_bases.append(base.__name__)
    # Drop a self-reference if it ever appears as a direct parent.
    resolved_bases = [b for b in resolved_bases if b != model.canonical_name]

    properties: list[CdmProperty] = []
    required: list[str] = []
    for name, info in model.cls.model_fields.items():
        is_array, members = _unwrap_annotation(info.annotation)
        ref_key: str | None = None
        ref_target: str | None = None
        primitive_type: str | None = None
        cdm_members = [m for m in members if isinstance(m, type) and _is_cdm_class(m)]
        if cdm_members:
            target_key = index.key_for_class(cdm_members[0])
            if target_key is not None:
                ref_key = target_key
                ref_target = index.by_key[target_key].canonical_name
            else:
                # Target not in this load (subset or external): keep a readable
                # display name so dangling refs stay debuggable.
                ref_target = demangle(_primitive_name(cdm_members[0]))
        elif members:
            member = members[0]
            primitive_type = _primitive_name(member) if isinstance(member, type) else str(member)
        else:
            primitive_type = "unknown"
        min_items, max_items = _min_max_lengths(info)
        is_required = bool(info.is_required())
        if is_required:
            required.append(name)
        properties.append(
            CdmProperty(
                name=name,
                description=info.description,
                ref_file=None,
                ref_target=ref_target,
                ref_key=ref_key,
                is_array=is_array,
                min_items=min_items,
                max_items=max_items,
                required=is_required,
                primitive_type=primitive_type,
            )
        )
    _ = key
    return CdmTypeRecord(
        file=model.module_name + ".py",
        type_name=model.canonical_name,
        namespace_tokens=tokens,
        namespace_slug=namespace_slug,
        namespace_anchor=".".join(tokens),
        coarse_namespace=coarse_namespace_for(tokens),
        title=model.canonical_name,
        description=description,
        kind=kind,
        required=required,
        properties=properties,
        base_types=resolved_bases,
        parser_version=PARSER_VERSION,
    )


def models_to_parse_result(
    models: dict[str, _DiscoveredModel],
    skipped: list[str] | None = None,
) -> CdmParseResult:
    """Convert discovered models into a CdmParseResult (source="pydantic")."""
    version = cdm_package_version()
    index = _ModelIndex.build(models)
    result = CdmParseResult(source=CDM_SOURCE, cdm_version=version)
    dangling: set[str] = set()
    for key in sorted(models):
        record = model_to_record(key, models[key], index, version)
        result.records[key] = record
        for prop in record.properties:
            if prop.ref_target and prop.ref_key is None:
                dangling.add(prop.ref_target)
    result.files_parsed = len(result.records)
    result.dangling_refs = sorted(dangling)
    result.collisions = sorted(
        name for name, keys in index.by_name.items() if len(keys) > 1
    )
    _ = skipped
    return result


def load_cdm_result(
    module_names: list[str] | None = None,
    limit: int | None = None,
) -> tuple[CdmParseResult, list[str]]:
    """Discover, import, and convert CDM models; also return skipped modules."""
    models, skipped = load_models(module_names=module_names, limit=limit)
    return models_to_parse_result(models, skipped), skipped


def summary(result: CdmParseResult) -> dict:
    """Count records by kind and coarse namespace (mirrors parser.summary)."""
    by_kind: dict[str, int] = {}
    by_namespace: dict[str, int] = {}
    extends = 0
    for record in result.records.values():
        by_kind[record.kind] = by_kind.get(record.kind, 0) + 1
        by_namespace[record.coarse_namespace] = by_namespace.get(
            record.coarse_namespace, 0
        ) + 1
        extends += len(record.base_types)
    return {
        "parser_version": result.parser_version,
        "cdm_version": result.cdm_version,
        "source": result.source,
        "files_parsed": result.files_parsed,
        "by_kind": by_kind,
        "by_coarse_namespace": by_namespace,
        "extends_links": extends,
        "dangling_refs": len(result.dangling_refs),
        "collisions": result.collisions,
    }
