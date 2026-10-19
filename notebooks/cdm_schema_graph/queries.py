"""Canned Cypher queries for exploring the CDM schema graph."""

TRADE_NEIGHBOURHOOD = """\
MATCH (t:CdmType {name: "Trade"})-[r:HAS_PROPERTY]->(dep)
RETURN t.name AS source, r.property_name AS property, dep.name AS target
ORDER BY property
"""

ASSET_HIERARCHY = """\
MATCH (a:CdmType {name: "Asset"})-[r:HAS_PROPERTY]->(child)
OPTIONAL MATCH (child)-[r2:HAS_PROPERTY]->(grandchild)
RETURN a.name AS asset, child.name AS child, grandchild.name AS grandchild
ORDER BY child, grandchild
"""

LEGAL_AGREEMENT = """\
MATCH (l:CdmType {name: "LegalAgreement"})-[r:HAS_PROPERTY]->(dep)
RETURN l.name AS source, r.property_name AS property, dep.name AS target
ORDER BY property
"""

METADATA_BACKBONE = """\
MATCH (n:CdmNamespace)
OPTIONAL MATCH (n)-[:CHILD_NAMESPACE]->(parent)
RETURN n.name AS namespace, parent.name AS parent
ORDER BY namespace
"""

TYPE_HUBS = """\
MATCH (t:CdmType)-[r:HAS_PROPERTY]->(dep)
RETURN dep.name AS type, count(*) AS in_degree
ORDER BY in_degree DESC
LIMIT 20
"""

INHERITANCE = """\
MATCH (child:CdmType)-[:EXTENDS]->(base)
RETURN child.name AS child, base.name AS base
ORDER BY base, child
LIMIT 50
"""

TRADE_INHERITANCE_CHAIN = """\
MATCH (t:CdmType {name: "Trade"})-[:EXTENDS*1..5]->(ancestor)
RETURN t.name AS type, ancestor.name AS ancestor
ORDER BY ancestor
"""

NAMESPACE_SIZES = """\
MATCH (t:CdmType)
RETURN t.coarse_namespace AS namespace, count(*) AS types
ORDER BY types DESC
"""
