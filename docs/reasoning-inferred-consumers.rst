:orphan:

Inferred query-to-managed-world scenario
========================================

``tests/test_reasoning_inferred_consumers.py`` wires a typed session to a
separate managed world with explicit caller-driven steps — no automatic
Cypher rewrite, no hidden publication. The session answers an inferred goal;
the caller reconciles the managed world to those answers; corrections and
retractions flow through session advance and re-publication. Every transition
compares the session, the managed world and fresh recomputation, and readers
pinned at older commit horizons observe the old managed state unchanged.

Retractions in the source retire the whole logical claim even when they name
a superseded version, and versions committed by the managed-world writes
themselves are skipped as foreign scope rather than ingested.
``gestaltdb.reasoning.adapters.inferred_sampler.build_inferred_snapshot``
builds the sampler consumer: one immutable snapshot per pinned session with
``reasoning_generation`` provenance (database, horizon, program/catalog
digests, scope, explicit selection). Selection is explicit — inferred nodes
under one predicate plus session edges from those nodes — with one edge per
fact, weight ``1.0`` and no hidden rewrite. Generation mismatches and
non-empty output paths fail closed. Neither probabilistic reasoning nor
coordination with #127/#128 beyond leaving those interfaces untouched is
implemented here.
