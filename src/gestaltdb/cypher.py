"""Minimal read-only Cypher support for GestaltDB.

The supported subset maps directly to existing typed adjacency and sampling APIs:

    MATCH (a {id: "node-id"})-[:TYPE1]->(b)<-[:TYPE2]-(c) RETURN a.name, b LIMIT 10
    CALL pg.sample_typed_paths(["node-id"], [{"edge_type": "TYPE", "sample_size": 2}]) YIELD path RETURN path
"""

from __future__ import annotations

from dataclasses import dataclass

from .cypher_ast import MatchQuery, MultiMatchQuery, NodeScanQuery, RelationshipScanQuery, SampleTypedPathsCall
from .cypher_plan import LogicalPlan, plan_query
from .cypher_parser import parse as _parse_query, split_top_level_args as _split_top_level_args
from .cypher_runtime import QueryContext, execute_match, execute_multi_match, execute_node_scan, execute_relationship_scan


@dataclass(frozen=True)
class QueryResult:
    """Tabular query result returned by ``GraphDB.query``.

    ``columns`` contains projected column names in return order. ``records`` is
    a list of dictionaries keyed by column name.

    Examples:
        >>> result = QueryResult(columns=("n",), records=[{"n": "node"}])
        >>> len(result)
        1
        >>> list(result)[0]["n"]
        'node'
    """

    columns: tuple[str, ...]
    records: list[dict[str, object]]

    def __iter__(self):
        """Iterate over result records."""
        return iter(self.records)

    def __len__(self):
        """Return the number of result records."""
        return len(self.records)


def parse(query: str) -> MatchQuery | SampleTypedPathsCall | NodeScanQuery | RelationshipScanQuery | MultiMatchQuery:
    """Parse the supported Cypher subset.

    Args:
        query: Cypher query text.

    Returns:
        Parsed query object.

    Raises:
        ValueError: If the query is outside the supported subset.

    Examples:
        >>> parse('MATCH (n:Drug) RETURN n').label
        'Drug'
    """
    return _parse_query(query)


def plan(query: str) -> LogicalPlan:
    """Return the logical plan for a supported Cypher query."""
    return plan_query(parse(query))


def execute(graph, query: str, parameters: dict[str, object] | None = None) -> QueryResult:
    """Execute a supported Cypher query against a ``GraphDB`` instance.

    Args:
        graph: ``GraphDB`` instance used for indexed lookups and traversal.
        query: Cypher query text.

    Returns:
        ``QueryResult`` with projected records.

    Examples:
        >>> execute(graph_db, 'MATCH (n:Drug) RETURN n')  # doctest: +SKIP
    """
    parsed = parse(query)
    plan_query(parsed)
    parameters = parameters or {}
    if isinstance(parsed, SampleTypedPathsCall):
        paths = graph.sample_typed_paths(parsed.seed_ids, parsed.pattern)
        if parsed.limit is not None:
            paths = paths[:parsed.limit]
        return QueryResult(
            columns=parsed.returns,
            records=[{"path": path} for path in paths],
        )
    if isinstance(parsed, NodeScanQuery):
        records = execute_node_scan(parsed, QueryContext(graph=graph, parameters=parameters))
        return QueryResult(columns=parsed.returns, records=records)
    if isinstance(parsed, RelationshipScanQuery):
        records = execute_relationship_scan(parsed, QueryContext(graph=graph, parameters=parameters))
        return QueryResult(columns=parsed.returns, records=records)
    if isinstance(parsed, MultiMatchQuery):
        records = execute_multi_match(parsed, QueryContext(graph=graph, parameters=parameters))
        return QueryResult(columns=parsed.returns, records=records)
    records = execute_match(parsed, QueryContext(graph=graph, parameters=parameters))
    return QueryResult(columns=parsed.returns, records=records)
