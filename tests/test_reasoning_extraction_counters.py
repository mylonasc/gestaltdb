"""Scan telemetry counts candidates rejected by Python selection as well."""

import pytest

from gestaltdb.graphdb import GraphDB
from gestaltdb.reasoning.adapters.batched import PinnedBatchedClaimSource
from gestaltdb.reasoning.adapters.claims import ClaimMapping, ClaimSelection
from gestaltdb.reasoning.extraction import ExtractionRequest


@pytest.mark.parametrize("agents,expected_rows", [(["chosen"], 1), ([], 0)])
def test_scanned_candidates_are_not_output_rows_and_reset_per_pass(tmp_path, agents, expected_rows):
    pytest.importorskip("lmdb")
    graph = GraphDB.create(tmp_path / "db", backend="lmdb", serializer="json")
    try:
        for index in range(10):
            graph.assert_claim(subject=str(index), predicate="p", object="b",
                               agent="chosen" if index == 0 else "other",
                               polarity="positive", valid=(0, 100))
        with graph.read_view(valid_time=50) as view:
            with PinnedBatchedClaimSource(
                view, request=ExtractionRequest(world="default", batch_rows=1),
                ontology_revision="rev", selection=ClaimSelection(agents=agents), mapping=ClaimMapping(),
            ) as source:
                for _ in range(2):
                    batches = list(source.iter_batches())
                    assert sum(len(batch.evidence) for batch in batches) == expected_rows
                    assert source.profile.rows == expected_rows
                    assert source.profile.scanned == 10
    finally:
        graph.close()
