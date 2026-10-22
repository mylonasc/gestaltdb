Gaps and pain points
======================

Status reviewed on 2026-10-22 against this checkout (base ``0dba88f``), remote
``main`` (``25daaf9``), remote ``feature/onto-dev-epic-2`` (``9400930``), and
open issues/PRs. No integration-branch merge was performed for this review.
Treat the following as a dated capability map, not a release promise.

* Profiles: disjunctive heads, existentials/EL (bounded prototype #185),
  full OWL RL/DL — construct matrix + approval gates in #184; no badge claims.
* Negation (#171, #125): scoped stratified evaluation exists in the generic
  engine. The positive binary maintenance API rejects negation/closed scopes,
  aggregates, changed programs, and equality splits; explicitly rematerialize
  instead. RE-16 is not a promise of incremental negation support.
* Equality (#172): snapshot-local canonical representatives; literals never
  merge; no unique-name assumption; temporal representatives rebuild per
  segment.
* Aggregates: nonrecursive/stratified only; recursive/lattice deferred.
* Extraction (#194): native decoded evidence and locator helpers are present;
  the remaining acceptance work is larger-scale measurement (100K+ rows and
  cache/value variation). Small fixtures do not establish a throughput win.
* Integration follow-ups: demand pushdown (#249), resumable commit-delta
  sessions (#251), assertion multiplicities (#250), generation identity (#248),
  inferred-generation consumption (#256), and executor pipeline glue (#255)
  remain active. Draft PRs are not available APIs in this checkout.
* Support-discovery head binding (#252) and immutable dependency lookup caches
  (#253) have merged implementations in this checkout (PRs #266 and #267),
  even though their issues remain open. Inspect code/tests and acceptance work
  rather than equating an open checkbox with an absent capability.
* Scale: profiling and worker/spill policy contracts exist; they are not a
  deployed out-of-core runtime or a bespoke native executor (RE-26, #187).
  Bounded one-step minimal/top-k witnesses and authenticated closure checkpoints
  also exist (RE-27, #188); globally minimal recursive proof trees and durable
  incremental-session resume are different, stronger requirements. See
  :doc:`../reasoning-proofs` and :doc:`../reasoning-scale`.
* Tracker: techdebt umbrella #200; roadmap phases RE-E1..E5 (#157–#161);
  neuro-symbolic (#128), weights (#124), explanations (#126) coordinate but
  stay separate.

Rule of thumb: check each API's declared fragment and supported combinations.
The construct matrix in ``reasoning/profiles.py`` is an ontology approval
inventory, not a universal execution-capability dispatcher. A construct's
presence there does not make it executable in every engine, adapter, or
temporal/maintenance mode. Never silently weaken a requested guarantee.
