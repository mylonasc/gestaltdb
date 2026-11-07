Inferred query-to-managed-world scenario
========================================

A typed session and a managed target world have separate lifecycles. A session
can answer an inferred goal; an application must separately invoke a supported
publisher to reconcile the managed world. Advancing the session after a source
correction or retraction does not automatically update published claims. Compare
session answers, managed outputs and fresh recomputation when validating an
application. Readers pinned at older commit horizons retain their old managed
state (:doc:`ontology-publication`, :doc:`reasoning-sessions`).

Retractions in the source retire the whole logical claim even when they name
a superseded version, and versions committed by the managed-world writes
themselves are skipped as foreign scope rather than ingested.
``gestaltdb.reasoning.adapters.inferred_sampler.build_inferred_snapshot``
builds the sampler consumer: one immutable snapshot per pinned session with
``reasoning_generation`` provenance (database, horizon, program/catalog
digests, scope, explicit selection). Selection is explicit — inferred nodes
under one predicate plus session edges from those nodes — with one edge per
fact, weight ``1.0`` and no hidden rewrite. Generation mismatches and
non-empty output paths fail closed. This builder does not implement probabilistic
reasoning or automated model-training loops.

This legacy builder does not independently authenticate its supplied session.
For exact shared generation/state admission, complete versioned metadata and
independent artifact hydration, use :doc:`reasoning-authenticated-inferred`.
