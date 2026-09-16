"""openCypher-oriented query support for GestaltDB.

The supported subset maps directly to existing typed adjacency and sampling APIs:

    MATCH (a {id: "node-id"})-[:TYPE1]->(b)<-[:TYPE2]-(c) RETURN a.name, b LIMIT 10
    CALL pg.sample_typed_paths(["node-id"], [{"edge_type": "TYPE", "sample_size": 2}]) YIELD path RETURN path
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from .cypher_ast import (
    CreateConstraint,
    DropConstraint,
    Query,
    SampleTypedPathsCall,
    ShowIndexes,
    ShowConstraints,
    UnionQuery,
)
from .cypher_parser import parse as _parse_query
from .cypher_parser import parse_ast as _parse_ast
from .cypher_parser import split_top_level_args as _split_top_level_args  # noqa: F401
from .cypher_plan import CreateStep, DeleteStep, ForeachStep, LogicalPlan, MergeStep, RemoveStep, SetStep, plan_query, plan_staged_query, plan_union_query
from .cypher_runtime import (
    QueryContext,
    execute_plan,
)

if TYPE_CHECKING:
    from .cypher_ast import (  # noqa: F401
        MatchQuery,
        MultiMatchQuery,
        NodeScanQuery,
        RelationshipScanQuery,
    )


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


def parse_ast(
    query: str,
) -> Query | SampleTypedPathsCall | UnionQuery | CreateConstraint | DropConstraint | ShowConstraints | ShowIndexes:
    """Parse the supported Cypher subset into its canonical clause AST."""
    return _parse_ast(query)


def plan(query: str) -> LogicalPlan:
    """Return the logical plan for a supported Cypher query.

    Clause queries plan to the staged operator pipeline, ``UNION`` queries
    plan each branch independently, and only the sampling procedure call
    keeps its dedicated source-backed plan. Constraint commands are executed
    directly and cannot be planned.
    """
    canonical = _parse_ast(query)
    if isinstance(canonical, SampleTypedPathsCall):
        return plan_query(canonical)
    if isinstance(canonical, UnionQuery):
        return plan_union_query(canonical)
    if isinstance(canonical, (CreateConstraint, DropConstraint, ShowConstraints, ShowIndexes)):
        raise TypeError(f"Cannot plan constraint command: {type(canonical).__name__}")
    return plan_staged_query(canonical)


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
    from .cypher_write import execute_ddl, transaction_supported

    canonical = _parse_ast(query)
    if isinstance(canonical, (CreateConstraint, DropConstraint, ShowConstraints, ShowIndexes)):
        columns, records = execute_ddl(graph, canonical)
        return QueryResult(columns=columns, records=records)
    if isinstance(canonical, SampleTypedPathsCall):
        logical_plan = plan_query(canonical)
    elif isinstance(canonical, UnionQuery):
        logical_plan = plan_union_query(canonical)
    else:
        logical_plan = plan_staged_query(canonical)
    if _plan_has_writes(logical_plan) and transaction_supported(graph):
        with graph.transaction() as tx_graph:
            records = execute_plan(
                logical_plan,
                QueryContext(graph=tx_graph, parameters=parameters or {}),
            )
    else:
        records = execute_plan(
            logical_plan,
            QueryContext(graph=graph, parameters=parameters or {}),
        )
    return QueryResult(columns=logical_plan.columns, records=records)


def _plan_has_writes(plan: LogicalPlan) -> bool:
    """Return whether a plan contains mutating operators."""
    return any(isinstance(operator, (CreateStep, MergeStep, ForeachStep, SetStep, RemoveStep, DeleteStep)) for operator in plan.operators)
