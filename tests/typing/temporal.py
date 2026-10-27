"""Static temporal consumer contracts, including literal-kind return types."""

from collections.abc import Iterator

from typing_extensions import assert_type

from gestaltdb._graph.entities import Node
from gestaltdb._graph.temporal_reads import TemporalReader
from gestaltdb._graph.temporal_selection import entity_as_of
from gestaltdb.epistemic_engine import EpistemicEngine
from gestaltdb.rules import ClaimExplanation, RuleRunResult, RuleVersion, TruthMaintenanceResult
from gestaltdb.rules_engine import RulesEngine
from gestaltdb.temporal import TemporalInstant
from gestaltdb.temporal_engine import TemporalEngine
from gestaltdb.versioning import ClaimVersion, EdgeVersion, NodeVersion, NodeVersionWrite, TemporalCommit, TemporalVersion


def reader_contracts(reader: TemporalReader, node_version: NodeVersion) -> None:
    assert_type(reader.get_commit(1), TemporalCommit | None)
    assert_type(reader.get_commit_cached(1), TemporalCommit | None)
    assert_type(reader.get_version_at(b"locator"), TemporalVersion | None)
    assert_type(reader.get_node_version("version"), NodeVersion | None)
    assert_type(reader.get_edge_version("version"), EdgeVersion | None)
    assert_type(reader.get_claim_version("version"), ClaimVersion | None)
    assert_type(reader.iter_commits(), Iterator[TemporalCommit])
    assert_type(entity_as_of(reader, "node", "n", valid_time=0), NodeVersion | None)
    assert_type(entity_as_of(reader, "edge", "e", valid_time=0), EdgeVersion | None)
    assert_type(entity_as_of(reader, "claim", "c", valid_time=0), ClaimVersion | None)
    assert_type(reader.copy_payload(node_version), NodeVersion)
    reader.get_commit("1")  # type: ignore[arg-type]
    wrong: str = next(iter(reader.commit_cache))[1]  # type: ignore[assignment]


def engine_contracts(engine: TemporalEngine, node: Node) -> None:
    assert_type(engine.put_node_version(node, valid=(0, 100)), NodeVersion)
    assert_type(engine.commit_versions(
        NodeVersionWrite.assertion(node, (0, 100)) for _ in range(1)
    ), TemporalCommit)
    assert_type(engine.iter_nodes_as_of(valid_time=0), Iterator[NodeVersion])
    assert_type(engine.iter_edges_as_of(source="n", valid_time=0), Iterator[EdgeVersion])
    assert_type(engine.next_system_time(None), TemporalInstant)
    assert_type(engine.entity_as_of("node", "n", valid_time=0), NodeVersion | None)
    assert_type(engine.entity_as_of("edge", "e", valid_time=0), EdgeVersion | None)
    assert_type(engine.entity_as_of("claim", "c", valid_time=0), ClaimVersion | None)
    history = engine.history()
    assert_type(history.entity_as_of("node", "n", valid_time=0), NodeVersion | None)
    assert_type(history.entity_as_of("edge", "e", valid_time=0), EdgeVersion | None)
    assert_type(history.entity_as_of("claim", "c", valid_time=0), ClaimVersion | None)
    with engine.write_guard() as guard:
        assert_type(guard, None)
    engine.commit_versions([node])  # type: ignore[list-item]
    engine.entity_as_of("node", "n")  # type: ignore[call-overload]
    engine.history(hydrate=lambda locator: "not-a-version")  # type: ignore[arg-type, return-value]
    wrong: EdgeVersion | None = engine.entity_as_of("node", "n", valid_time=0)  # type: ignore[assignment]


def service_contracts(epistemic: EpistemicEngine, rules: RulesEngine) -> None:
    assert_type(epistemic.assert_claim(
        subject="a", predicate="p", object="b", agent="test", polarity="positive", valid=(0, 100),
    ), ClaimVersion)
    assert_type(epistemic.iter_claim_versions(), Iterator[ClaimVersion])
    assert_type(epistemic.iter_claims_as_of(valid_time=0), Iterator[ClaimVersion])
    assert_type(rules.get_rule("rule"), RuleVersion | None)
    assert_type(rules.run(as_of=0), RuleRunResult)
    assert_type(rules.maintain(as_of=0), TruthMaintenanceResult)
    assert_type(rules.explain("claim", valid_time=0), ClaimExplanation | None)
    assert_type(rules.last_maintenance_instant(), TemporalInstant | None)
    rules.run(as_of=0, max_iterations="100")  # type: ignore[arg-type]
