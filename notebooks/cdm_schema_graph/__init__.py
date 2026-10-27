"""ISDA CDM 7.0 schema-graph example (sidecar, not core).

Companion library for ``notebooks/11_cdm_schema_graph.ipynb``.  It downloads
the FINOS CDM JSON schemas, parses type/property/documentation structure, and
maps the result into :class:`gestaltdb.graphdb.Node` / ``Edge`` collections.

The notebook puts this directory's parent (``notebooks/``) on ``sys.path``
and imports ``cdm_schema_graph`` directly, so this package uses relative
imports internally.
"""

from .graph_builder import (
    CHILD_NAMESPACE,
    EXTENDS,
    HAS_PROPERTY,
    IN_NAMESPACE,
    build_graph_payload,
    load_into_graph,
)
from .parser import (
    PARSER_VERSION,
    CdmParseResult,
    CdmProperty,
    CdmSchemaParser,
    CdmTypeRecord,
)
from .pydantic_source import (
    CDM_SOURCE,
    cdm_package_version,
    load_cdm_result,
    load_models,
    models_to_parse_result,
)

__all__ = [
    "CDM_SOURCE",
    "CHILD_NAMESPACE",
    "EXTENDS",
    "HAS_PROPERTY",
    "IN_NAMESPACE",
    "PARSER_VERSION",
    "CdmParseResult",
    "CdmProperty",
    "CdmSchemaParser",
    "CdmTypeRecord",
    "build_graph_payload",
    "cdm_package_version",
    "load_cdm_result",
    "load_into_graph",
    "load_models",
    "models_to_parse_result",
]
