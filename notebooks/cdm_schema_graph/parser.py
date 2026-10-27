"""Download and parse FINOS ISDA CDM 7.0 JSON schemas.

Example-use parser only (not core library code).  It retains type identity,
namespace hierarchy, property cardinality, ``$ref`` targets, and documentation
(``description`` fields at schema, property, and enum-value level).

Versioning: :data:`PARSER_VERSION` is stamped onto every parsed record and,
via :mod:`cdm_schema_graph.graph_builder`, onto every graph node/edge plus a
dedicated dataset node, so stored graphs always record which parser produced
them.
"""

from __future__ import annotations

import concurrent.futures
import json
import re
import urllib.request
from dataclasses import dataclass, field
from pathlib import Path

#: Parser version, also stamped onto parsed records and graph payloads.
#: Bump on any change to parsing/classification semantics.
#: 0.2.0 adds the pydantic model source (see cdm_schema_graph.pydantic_source)
#: alongside the original JSON-schema parser.
PARSER_VERSION = "0.2.0"

#: CDM release this parser targets.
CDM_VERSION = "7.0"

#: Default schema index.
BASE_URL = "https://cdm.finos.org/schemas/7.0/"

_HREF_RE = re.compile(r'href="([^"]*\.schema\.json)"')
_CHOICE_HINT_RE = re.compile(r"one and only one|choice data type", re.IGNORECASE)


@dataclass
class CdmProperty:
    """One object property: docs, reference target, and cardinality."""

    name: str
    description: str | None = None
    ref_file: str | None = None
    ref_target: str | None = None
    #: Exact key of the target record in ``CdmParseResult.records`` when known.
    #: Falls back to ``ref_target`` lookup when None.
    ref_key: str | None = None
    is_array: bool = False
    min_items: int | None = None
    max_items: int | None = None
    required: bool = False
    primitive_type: str | None = None


@dataclass
class CdmTypeRecord:
    """Parsed view of one ``*.schema.json`` file."""

    file: str
    type_name: str
    namespace_tokens: list[str] = field(default_factory=list)
    namespace_slug: str = ""
    namespace_anchor: str | None = None
    coarse_namespace: str = ""
    title: str | None = None
    description: str | None = None
    kind: str = "unknown"  # object | choice | enum | scalar | unknown
    required: list[str] = field(default_factory=list)
    properties: list[CdmProperty] = field(default_factory=list)
    #: Canonical names of direct CDM base types (pydantic source only).
    base_types: list[str] = field(default_factory=list)
    enum_values: list[str] = field(default_factory=list)
    enum_details: list[dict] = field(default_factory=list)
    parser_version: str = PARSER_VERSION


@dataclass
class CdmParseResult:
    """Outcome of parsing a set of schema files."""

    records: dict[str, CdmTypeRecord] = field(default_factory=dict)
    files_parsed: int = 0
    dangling_refs: list[str] = field(default_factory=list)
    collisions: list[str] = field(default_factory=list)
    parser_version: str = PARSER_VERSION
    cdm_version: str = CDM_VERSION
    #: Where the records came from: ``"json-schema"`` or ``"pydantic"``.
    source: str = "json-schema"


def record_key(namespace_slug: str, type_name: str, taken: set[str]) -> str:
    """Return the records-dict key, qualifying with namespace on collision."""
    if type_name not in taken:
        return type_name
    return f"{namespace_slug}:{type_name}"


def _loads(text: str, origin: str) -> dict:
    """Parse JSON leniently (CDM ships control characters); name the culprit."""
    try:
        return json.loads(text, strict=False)
    except json.JSONDecodeError as exc:
        raise ValueError(f"invalid JSON in {origin}: {exc}") from exc


def split_schema_filename(name: str) -> tuple[str, list[str], str]:
    """Split ``cdm-a-b-Type.schema.json`` into type, tokens, and slug."""
    stem = name.removesuffix(".schema.json")
    parts = stem.split("-")
    type_name = parts[-1]
    namespace_tokens = parts[:-1]
    return type_name, namespace_tokens, "-".join(namespace_tokens)


def coarse_namespace_for(tokens: list[str]) -> str:
    """Map namespace tokens to a coarse metadata bucket.

    ``cdm-event-...`` -> ``cdm-event``, ``cdm-legaldocumentation-...`` ->
    ``cdm-legaldocumentation``.  ``cdm-base-X`` keeps two levels
    (``cdm-base-staticdata``, ...) so the ``cdm-base`` bucket does not swallow
    the asset hierarchy; other trees fall back to the first two tokens.
    """
    if not tokens:
        return "cdm"
    if tokens[0] != "cdm":
        return "-".join(tokens[:2]) if len(tokens) > 1 else tokens[0]
    if len(tokens) < 2:
        return "cdm"
    if tokens[1] == "base" and len(tokens) > 2:
        return f"cdm-base-{tokens[2]}"
    return f"cdm-{tokens[1]}"


def classify_kind(schema: dict) -> str:
    """Classify a schema dict as object / choice / enum / scalar / unknown."""
    if isinstance(schema.get("enum"), list):
        return "enum"
    schema_type = schema.get("type")
    if schema_type == "object":
        description = schema.get("description") or ""
        props = schema.get("properties") or {}
        if props and _CHOICE_HINT_RE.search(description):
            return "choice"
        return "object"
    if schema_type in ("string", "number", "integer", "boolean"):
        return "scalar"
    if schema_type is None and ("properties" in schema or "oneOf" in schema):
        return "object"
    return "unknown"


def _as_int(value) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def parse_property(name: str, spec: dict, required: set[str]) -> CdmProperty:
    """Parse one ``properties[name]`` entry, keeping docs and cardinality."""
    spec = spec if isinstance(spec, dict) else {}
    description = spec.get("description")
    is_array = spec.get("type") == "array"
    items = spec.get("items", {}) if is_array else spec
    if not isinstance(items, dict):
        items = {}
    ref_file = spec.get("$ref")
    if ref_file is None and is_array:
        ref_file = items.get("$ref")
    if ref_file is None:
        for key in ("oneOf", "anyOf"):
            branches = spec.get(key) or items.get(key) or []
            if isinstance(branches, list):
                for branch in branches:
                    if isinstance(branch, dict) and branch.get("$ref"):
                        ref_file = branch["$ref"]
                        break
            if ref_file is not None:
                break
    ref_target = None
    if isinstance(ref_file, str):
        ref_target, _, _ = split_schema_filename(ref_file.split("/")[-1])
    primitive = items.get("type") if is_array else spec.get("type")
    if primitive == "array":
        primitive = None
    return CdmProperty(
        name=name,
        description=description,
        ref_file=ref_file,
        ref_target=ref_target,
        is_array=is_array,
        min_items=_as_int(spec.get("minItems")) if is_array else None,
        max_items=_as_int(spec.get("maxItems")) if is_array else None,
        required=name in required,
        primitive_type=primitive,
    )


def parse_schema_dict(file: str, schema: dict) -> CdmTypeRecord:
    """Parse an already-loaded schema dict (pure function, no I/O)."""
    type_name, tokens, slug = split_schema_filename(file.split("/")[-1])
    required = schema.get("required") or []
    required_set = set(required) if isinstance(required, list) else set()
    props = schema.get("properties") or {}
    properties = [
        parse_property(name, spec, required_set)
        for name, spec in props.items()
        if isinstance(spec, dict)
    ]
    enum_values = schema.get("enum") if isinstance(schema.get("enum"), list) else []
    enum_details: list[dict] = []
    branches = schema.get("oneOf")
    if isinstance(branches, list):
        for branch in branches:
            if not isinstance(branch, dict):
                continue
            values = branch.get("enum") or []
            value = values[0] if len(values) == 1 else None
            if value is not None:
                enum_details.append(
                    {
                        "value": value,
                        "title": branch.get("title"),
                        "description": branch.get("description"),
                    }
                )
    return CdmTypeRecord(
        file=file.split("/")[-1],
        type_name=type_name,
        namespace_tokens=tokens,
        namespace_slug=slug,
        namespace_anchor=schema.get("$anchor"),
        coarse_namespace=coarse_namespace_for(tokens),
        title=schema.get("title"),
        description=schema.get("description"),
        kind=classify_kind(schema),
        required=[r for r in required if isinstance(r, str)],
        properties=properties,
        enum_values=[str(v) for v in enum_values],
        enum_details=enum_details,
        parser_version=PARSER_VERSION,
    )


class CdmSchemaParser:
    """Download CDM schemas and parse them into :class:`CdmTypeRecord`."""

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        base_url: str = BASE_URL,
        timeout: int = 30,
    ) -> None:
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.timeout = timeout
        self.cache_dir = Path(cache_dir) if cache_dir is not None else None
        if self.cache_dir is not None:
            self.cache_dir.mkdir(parents=True, exist_ok=True)

    # -- discovery / download -------------------------------------------
    def _http_get_text(self, url: str) -> str:
        request = urllib.request.Request(url, headers={"User-Agent": "gestaltdb-cdm-example/0.1"})
        with urllib.request.urlopen(request, timeout=self.timeout) as response:
            return response.read().decode("utf-8")

    def list_remote_schema_files(self) -> list[str]:
        """Fetch the index page and return sorted ``*.schema.json`` names."""
        html = self._http_get_text(self.base_url)
        names = sorted(set(_HREF_RE.findall(html)))
        return [name.split("/")[-1] for name in names]

    def cached_path(self, name: str) -> Path | None:
        if self.cache_dir is None:
            return None
        return self.cache_dir / name.split("/")[-1]

    def download_one(self, name: str, force: bool = False) -> dict:
        """Download one schema (via cache when configured); return its dict."""
        short = name.split("/")[-1]
        cached = self.cached_path(short)
        if cached is not None and cached.is_file() and not force:
            return _loads(cached.read_text(encoding="utf-8"), str(cached))
        text = self._http_get_text(self.base_url + short)
        data = _loads(text, self.base_url + short)
        if cached is not None:
            cached.write_text(text, encoding="utf-8")
        return data

    def download_all(
        self,
        names: list[str] | None = None,
        limit: int | None = None,
        workers: int = 8,
        force: bool = False,
    ) -> dict[str, dict]:
        """Download many schemas concurrently; returns name -> schema dict."""
        if names is None:
            names = self.list_remote_schema_files()
        if limit is not None:
            names = names[:limit]
        schemas: dict[str, dict] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, workers)) as pool:
            future_to_name = {
                pool.submit(self.download_one, name, force): name for name in names
            }
            for future in concurrent.futures.as_completed(future_to_name):
                name = future_to_name[future]
                schemas[name.split("/")[-1]] = future.result()
        return schemas

    # -- parsing ----------------------------------------------------------
    def parse_file(self, path: str | Path) -> CdmTypeRecord:
        """Parse one cached ``*.schema.json`` file."""
        file_path = Path(path)
        schema = _loads(file_path.read_text(encoding="utf-8"), str(file_path))
        return parse_schema_dict(file_path.name, schema)

    def parse_all(
        self,
        schemas: dict[str, dict] | None = None,
        directory: str | Path | None = None,
        limit: int | None = None,
    ) -> CdmParseResult:
        """Parse schemas from memory or from a cache directory."""
        if schemas is None:
            source = Path(directory) if directory is not None else self.cache_dir
            if source is None or not source.is_dir():
                raise ValueError("no schemas given and no cache directory available")
            files = sorted(source.glob("*.schema.json"))
            if limit is not None:
                files = files[:limit]
            schemas = {}
            for file_path in files:
                schemas[file_path.name] = _loads(
                    file_path.read_text(encoding="utf-8"), str(file_path)
                )
        result = CdmParseResult(source="json-schema")
        known_targets = {split_schema_filename(name)[0] for name in schemas}
        taken: set[str] = set()
        for name in sorted(schemas):
            record = parse_schema_dict(name, schemas[name])
            key = record_key(record.namespace_slug, record.type_name, taken)
            if key != record.type_name:
                result.collisions.append(record.type_name)
            taken.add(key)
            result.records[key] = record
        dangling: set[str] = set()
        for record in result.records.values():
            for prop in record.properties:
                if prop.ref_target and prop.ref_target not in known_targets:
                    dangling.add(prop.ref_file or prop.ref_target)
        result.files_parsed = len(result.records)
        result.dangling_refs = sorted(dangling)
        return result

    def summary(self, result: CdmParseResult) -> dict:
        """Count records by kind and coarse namespace."""
        by_kind: dict[str, int] = {}
        by_namespace: dict[str, int] = {}
        for record in result.records.values():
            by_kind[record.kind] = by_kind.get(record.kind, 0) + 1
            key = record.coarse_namespace
            by_namespace[key] = by_namespace.get(key, 0) + 1
        return {
            "parser_version": result.parser_version,
            "cdm_version": result.cdm_version,
            "source": result.source,
            "files_parsed": result.files_parsed,
            "by_kind": by_kind,
            "by_coarse_namespace": by_namespace,
            "dangling_refs": len(result.dangling_refs),
            "collisions": result.collisions,
        }
