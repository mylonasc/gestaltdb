"""Tests for the CDM schema-graph sidecar example."""

import sys
from pathlib import Path

NOTEB00KS = Path(__file__).resolve().parents[1] / "notebooks"
sys.path.insert(0, str(NOTEB00KS))

from cdm_schema_graph.graph_builder import build_graph_payload
from cdm_schema_graph.parser import (
    PARSER_VERSION,
    CdmSchemaParser,
    parse_schema_dict,
)


def _write(cache: Path, name: str, schema: dict) -> None:
    import json

    (cache / name).write_text(json.dumps(schema), encoding="utf-8")


def _fixture_cache(tmp_path: Path) -> Path:
    cache = tmp_path / "cdm"
    cache.mkdir()
    _write(cache, "cdm-event-common-Trade.schema.json", {
        "$anchor": "cdm.event.common",
        "type": "object",
        "title": "Trade",
        "description": "Output of a financial transaction.",
        "properties": {
            "counterparty": {
                "description": "The two counterparties.",
                "type": "array",
                "items": {"$ref": "cdm-base-staticdata-party-Counterparty.schema.json"},
                "minItems": 2,
                "maxItems": 2,
            },
            "tradeDate": {"description": "Agreed date.", "type": "string"},
        },
        "required": ["tradeDate"],
    })
    _write(cache, "cdm-base-staticdata-party-Counterparty.schema.json", {
        "$anchor": "cdm.base.staticdata.party",
        "type": "object",
        "title": "Counterparty",
        "description": "A counterparty.",
        "properties": {},
    })
    _write(cache, "cdm-base-staticdata-asset-common-Asset.schema.json", {
        "$anchor": "cdm.base.staticdata.asset.common",
        "type": "object",
        "title": "Asset",
        "description": "As a choice data type, one and only one attribute must be used.",
        "properties": {
            "Cash": {
                "description": "Monetary holding.",
                "$ref": "cdm-base-staticdata-asset-common-Cash.schema.json",
            }
        },
    })
    _write(cache, "cdm-base-staticdata-asset-common-Cash.schema.json", {
        "$anchor": "cdm.base.staticdata.asset.common",
        "type": "object",
        "title": "Cash",
        "description": "Cash asset.",
        "properties": {},
    })
    _write(cache, "cdm-legaldocumentation-common-LegalAgreement.schema.json", {
        "$anchor": "cdm.legaldocumentation.common",
        "type": "object",
        "title": "LegalAgreement",
        "description": "A legal agreement.",
        "properties": {
            "relatedAgreements": {
                "description": "Governing agreements.",
                "type": "array",
                "items": {"$ref": "cdm-legaldocumentation-common-LegalAgreement.schema.json"},
            }
        },
    })
    _write(cache, "cdm-base-datetime-BusinessDayConventionEnum.schema.json", {
        "$anchor": "cdm.base.datetime",
        "type": "string",
        "title": "BusinessDayConventionEnum",
        "description": "Date adjustment conventions.",
        "enum": ["FOLLOWING", "NONE"],
        "oneOf": [
            {"enum": ["FOLLOWING"], "title": "FOLLOWING", "description": "Next business day."},
            {"enum": ["NONE"], "title": "NONE", "description": "No adjustment."},
        ],
    })
    return cache


def test_parser_keeps_docs_cardinality_and_kinds(tmp_path):
    cache = _fixture_cache(tmp_path)
    parser = CdmSchemaParser(cache_dir=cache)
    result = parser.parse_all(directory=cache)

    assert result.files_parsed == 6
    assert result.parser_version == PARSER_VERSION
    trade = result.records["Trade"]
    assert trade.parser_version == PARSER_VERSION
    assert trade.kind == "object"
    counterparty = next(p for p in trade.properties if p.name == "counterparty")
    assert counterparty.description == "The two counterparties."
    assert counterparty.ref_target == "Counterparty"
    assert counterparty.is_array and counterparty.min_items == 2 and counterparty.max_items == 2
    assert result.records["Asset"].kind == "choice"
    enum = result.records["BusinessDayConventionEnum"]
    assert enum.kind == "enum" and enum.enum_values == ["FOLLOWING", "NONE"]
    assert enum.enum_details[0]["description"] == "Next business day."
    assert result.records["LegalAgreement"].properties[0].ref_target == "LegalAgreement"


def test_parse_schema_dict_pure_function():
    record = parse_schema_dict("cdm-event-common-Trade.schema.json", {
        "type": "object", "title": "Trade", "description": "t",
        "properties": {"party": {"type": "array",
                                 "items": {"$ref": "cdm-base-staticdata-party-Party.schema.json"}}},
    })
    assert record.type_name == "Trade"
    assert record.namespace_slug == "cdm-event-common"
    assert record.coarse_namespace == "cdm-event"
    assert record.properties[0].ref_target == "Party"


def test_builder_metadata_assets_and_parser_version(tmp_path):
    cache = _fixture_cache(tmp_path)
    result = CdmSchemaParser(cache_dir=cache).parse_all(directory=cache)
    payload = build_graph_payload(result, source_url="https://example.invalid/cdm/")

    node_ids = {n.get_id for n in payload.nodes}
    assert "ns:cdm-event" in node_ids
    assert "ns:cdm-legaldocumentation" in node_ids
    assert "ns:cdm" in node_ids
    assert "cdm:dataset:7.0" in node_ids
    assert all(n.properties.get("parser_version") == PARSER_VERSION for n in payload.nodes)
    assert all(e.properties.get("type") for e in payload.edges)
    assert all(e.properties.get("parser_version") == PARSER_VERSION for e in payload.edges)

    edge_types = {e.properties["type"] for e in payload.edges}
    assert {"HAS_PROPERTY", "IN_NAMESPACE", "CHILD_NAMESPACE"} <= edge_types
    asset_edges = [e for e in payload.edges
                   if e.source == payload.type_node_ids["Asset"]]
    assert any(e.properties.get("property_name") == "Cash" for e in asset_edges)
    dataset = next(n for n in payload.nodes if n.get_id == "cdm:dataset:7.0")
    assert dataset.properties["parser_version"] == PARSER_VERSION


def test_builder_payload_loads_into_graph(tmp_path):
    pytest = __import__("pytest")
    pytest.importorskip("plyvel")
    from gestaltdb.graphdb import GraphDB
    from gestaltdb.kvstores import LevelDBStore
    from gestaltdb.serializers import JSONSerializer

    cache = _fixture_cache(tmp_path)
    result = CdmSchemaParser(cache_dir=cache).parse_all(directory=cache)
    payload = build_graph_payload(result)
    graph = GraphDB(LevelDBStore(path=str(tmp_path / "graph")), JSONSerializer())
    try:
        from cdm_schema_graph.graph_builder import load_into_graph

        stats = load_into_graph(graph, payload)
        assert stats["parser_version"] == PARSER_VERSION
        rows = graph.query(
            'MATCH (t:CdmType {name: "Trade"})-[r:HAS_PROPERTY]->(d) '
            "RETURN d.name AS dep"
        ).records
        assert rows == [{"dep": "Counterparty"}]
    finally:
        graph.close()


PYDANTIC_MODULES = [
    "finos.cdm.event.common.Trade",
    "finos.cdm.product.template.TradableProduct",
    "finos.cdm.base.staticdata.asset.common.Asset",
    "finos.cdm.base.staticdata.asset.common.Cash",
    "finos.cdm.base.staticdata.asset.common.Instrument",
    "finos.cdm.base.staticdata.party.Counterparty",
    "finos.cdm.legaldocumentation.common.LegalAgreement",
    "finos.cdm.base.datetime.BusinessDayConventionEnum",
]


def test_pydantic_subset_docs_kinds_and_refs():
    pytest = __import__("pytest")
    pytest.importorskip("finos.cdm")

    from cdm_schema_graph.pydantic_source import load_cdm_result

    result, skipped = load_cdm_result(module_names=PYDANTIC_MODULES)
    assert skipped == []
    assert result.source == "pydantic"
    assert result.parser_version == PARSER_VERSION

    trade = result.records["Trade"]
    assert trade.kind == "object"
    assert trade.description.startswith("Defines the output of a financial transaction")
    assert trade.base_types == ["TradableProduct"]
    counterparty = next(p for p in trade.properties if p.name == "counterparty")
    assert counterparty.ref_target == "Counterparty"
    assert counterparty.ref_key == "Counterparty"
    assert counterparty.is_array and counterparty.required
    assert "tradeDate" in trade.required

    asset = result.records["Asset"]
    assert asset.kind == "choice"
    assert "choice data type" in (asset.description or "").lower()
    cash = next(p for p in asset.properties if p.name == "Cash")
    assert cash.ref_target == "Cash" and cash.description.startswith("An Asset")

    agreement = result.records["LegalAgreement"]
    assert agreement.description.startswith("The specification of a legal agreement")
    assert "LegalAgreementBase" in agreement.base_types

    enum = result.records["BusinessDayConventionEnum"]
    assert enum.kind == "enum"
    assert "FOLLOWING" in enum.enum_values
    following = next(d for d in enum.enum_details if d["value"] == "FOLLOWING")
    assert following["description"].startswith("The non-business date")


def test_pydantic_subset_builder_extends_and_provenance():
    pytest = __import__("pytest")
    pytest.importorskip("finos.cdm")

    from cdm_schema_graph.graph_builder import EXTENDS, build_graph_payload
    from cdm_schema_graph.pydantic_source import cdm_package_version, load_cdm_result

    result, _ = load_cdm_result(module_names=PYDANTIC_MODULES)
    payload = build_graph_payload(result, package_version=cdm_package_version())

    node_ids = {n.get_id for n in payload.nodes}
    assert "ns:cdm-event" in node_ids
    assert "ns:cdm-legaldocumentation" in node_ids
    dataset = next(n for n in payload.nodes if "dataset" in n.get_id)
    assert dataset.properties["source"] == "pydantic"
    assert dataset.properties["parser_version"] == PARSER_VERSION

    extends = [e for e in payload.edges if e.properties.get("type") == EXTENDS]
    assert {(e.source, e.target) for e in extends} >= {
        (payload.type_node_ids["Trade"], payload.type_node_ids["TradableProduct"])
    }


def test_pydantic_full_walk_no_dangling_refs():
    pytest = __import__("pytest")
    pytest.importorskip("finos.cdm")

    from cdm_schema_graph.graph_builder import build_graph_payload
    from cdm_schema_graph.pydantic_source import load_cdm_result, summary

    result, _ = load_cdm_result()
    assert result.files_parsed > 1000
    assert result.dangling_refs == []
    stats = summary(result)
    assert stats["by_kind"]["choice"] >= 10
    assert stats["by_kind"]["enum"] > 200

    payload = build_graph_payload(result, package_version=result.cdm_version)
    assert payload.stats["extends_edges"] > 100
    assert payload.stats["has_property_edges"] > 1000
    node_ids = {n.get_id for n in payload.nodes}
    assert "ns:cdm-event" in node_ids and "ns:cdm-legaldocumentation" in node_ids
    assert all(n.properties.get("parser_version") == PARSER_VERSION for n in payload.nodes)
    assert all(e.properties.get("parser_version") == PARSER_VERSION for e in payload.edges)
