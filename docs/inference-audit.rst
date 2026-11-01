Unified inference audit
=======================

``gestaltdb.inference.explain_inference`` joins explicitly tagged model-output
claims with the existing historical logical explanation graph. This read-only
adapter returns a detached ``InferenceExplanation`` with deterministic
``to_json()``, copy-returning ``to_dict()`` and script-free ``as_html()``.
It does not run a model, request network resources, or change rule semantics.

Model outputs are assertions, not proofs
---------------------------------------------

Tag model outputs with ``provenance['model_output']`` containing explicit
``model_id`` and ``model_version`` strings. Optional training-data identifiers,
feature snapshot identifiers or calibration notes can be stored alongside
them. These fields are caller assertions, not authenticated model lineage.
No association is guessed from confidence, agent names, or predicates.

Confidence remains a score on an assertion. A deterministic rule consuming a
prediction proves only what follows *assuming that assertion*. It does not
prove the prediction correct, propagate probabilities, or explain the model's
internal decisions. This export is an audit record, not independent proof
verification or a regulatory-compliance certificate.

Runnable model-to-proof example
-------------------------------

This example requires the ``lmdb`` extra and closes the database explicitly.
The prediction could come from a GNN; the example deliberately uses a fixed
score rather than requiring a training framework.

.. code-block:: python

   from pathlib import Path
   from tempfile import TemporaryDirectory
   from gestaltdb.graphdb import GraphDB
   from gestaltdb.inference import explain_inference

   with TemporaryDirectory() as directory:
       graph = GraphDB.create(Path(directory) / "db", backend="lmdb", serializer="json")
       try:
           prediction = graph.epistemic.assert_claim(
               subject="supplier-1", predicate="PREDICTED_RISK", object="high",
               polarity="positive", agent="gnn-risk", confidence=0.8, valid=(0, 100),
               provenance={"model_output": {
                   "model_id": "supplier-risk-gnn", "model_version": "weights-7"}},
           )
           graph.rules.create_rule(
               "review-predictions", when=[("?s", "PREDICTED_RISK", "high")],
               then=("?s", "REQUIRES_REVIEW", "yes"),
           )
           graph.rules.run(as_of=10)
           conclusion = next(graph.epistemic.iter_claims_as_of(
               valid_time=10, subject="supplier-1", predicate="REQUIRES_REVIEW"))
           report = explain_inference(graph, conclusion.logical_id, valid_time=10)
           assert any(node["kind"] == "model_output" for node in report.to_dict()["nodes"])
           Path(directory, "audit.json").write_text(report.to_json(), encoding="utf-8")
           Path(directory, "audit.html").write_text(report.as_html(), encoding="utf-8")
       finally:
           graph.close()

Identity, alternatives and bounds
---------------------------------

Format-v1 exports carry the root logical claim/version IDs, requested valid
time, resolved system time, exact claim/rule versions, validity, commit ID and
ordinal, payload hashes, scores and provenance. Alternative justifications
retain their ordered premise-version groups per conclusion. They are not
collapsed through shared rule nodes; recursive evidence may be a finite graph,
not a tree. Rules using closed-world negation also export ``absence`` nodes and
per-support ``absences`` lists. These record absence checks at the evaluation
horizon; they are not claim versions, negative facts or independent proof that
the closed-world declaration was justified. Evidence is untrusted data and can contain sensitive metadata;
review it before sharing. JSON serialization does not authenticate the report.

``max_depth`` and ``max_nodes`` use the existing ``graph.rules.explain`` traversal
bounds. Missing/depth-limited evidence sets ``truncated=true``; incomplete
justifications are omitted, never presented as proofs. Additional ``max_edges``
and ``max_bytes`` bound the exported justification links and serialized JSON;
overflow raises ``RuleEvaluationLimitError`` without an artifact. These are
export bounds, not a guarantee of bounded backend reads or serializer memory.
HTML escaping prevents metadata from becoming executable markup.

Absent or retracted roots return ``None``. Pass ``system_time`` to inspect an
older visible root. Exact premise versions remain historical even if corrected
later. ``export_inference`` also accepts an existing ``ClaimExplanation``;
it does not authenticate manually constructed values.
