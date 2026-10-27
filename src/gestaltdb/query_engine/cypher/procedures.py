"""Allowlisted procedure-call row seeding for Cypher execution.

``procedure_rows`` seeds binding rows for ``pg.sample_typed_paths`` and
``kg.entails`` calls. It lives apart from the streaming operators so the
runtime stays within its module-size budget; it consumes the query context
without importing the runtime module.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, cast

from .ast import EntailsCall, Expression
from .expr import evaluate_expression

if TYPE_CHECKING:
    from .plan import ProcedureSource
    from .runtime import BindingRow, QueryContext


def procedure_rows(source: ProcedureSource, context: QueryContext) -> Iterator[BindingRow]:
    """Seed binding rows from an allowlisted procedure call."""
    from .runtime import BindingRow

    if isinstance(source.query, EntailsCall):
        agent = evaluate_expression(cast(Expression, source.query.agent), {}, context)
        proposition = evaluate_expression(cast(Expression, source.query.proposition), {}, context)
        mode = evaluate_expression(cast(Expression, source.query.mode), {}, context)
        options = evaluate_expression(cast(Expression, source.query.options), {}, context)
        if not isinstance(options, dict):
            raise ValueError("kg.entails options must be a map")
        allowed_options = {"world", "validTime", "systemTime", "throughCommit", "maxDepth", "maxStates"}
        unknown = sorted(set(options) - allowed_options)
        if unknown:
            raise ValueError(f"kg.entails unsupported option: {unknown[0]}")
        if "world" not in options or "validTime" not in options:
            raise ValueError("kg.entails options require world and validTime")
        graph = context.graph
        with graph.temporal.open_read_view(
            graph,
            manifest=graph.manifest,
            database_id=graph.database_id,
            valid_time=options["validTime"],
            system_time=options.get("systemTime"),
            through_commit=options.get("throughCommit"),
        ) as entails_view:
            result = graph.epistemic.entails(
                entails_view,
                cast(str, agent),
                proposition,
                cast(str, mode),
                world=options["world"],
                max_depth=options.get("maxDepth", 4),
                max_states=options.get("maxStates", 1_000),
            )
        values: dict[str, object] = {
            "status": result.status.value,
            "confidence": result.confidence,
            "explanation": dict(result.explanation),
        }
        bindings = {alias: values[field] for field, alias in source.query.yields}
        return iter((BindingRow(bindings=bindings),))
    seed_ids = context.resolve(source.query.seed_ids)
    pattern = context.resolve(source.query.pattern)
    if not isinstance(seed_ids, list) or not all(isinstance(seed_id, str) for seed_id in seed_ids):
        raise ValueError("pg.sample_typed_paths seed IDs must be a list of strings")
    if not isinstance(pattern, list) or not all(isinstance(hop, dict) for hop in pattern):
        raise ValueError("pg.sample_typed_paths pattern must be a list of dictionaries")
    paths = context.graph.sample_typed_paths(seed_ids, pattern)
    return (
        BindingRow(bindings={source.query.output_name: path})
        for path in paths
    )
