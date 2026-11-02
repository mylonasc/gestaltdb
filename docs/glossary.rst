Concepts and Terminology
========================

You do not need a background in logic programming to use GestaltDB. This glossary
defines terms as used by its guides. Start with :doc:`reasoning-introduction` for
a small example that connects facts, rules, conclusions and explanations; use
:doc:`property-graph` for the database model.

.. glossary::
   :sorted:

   Datalog
      A rule-based language for deriving facts from other facts. A program says
      what follows from the inputs, rather than listing every execution step.
      GestaltDB provides explicit Python rule objects and bounded evaluators,
      not an unrestricted textual Datalog parser. See
      `Datalog on Wikipedia <https://en.wikipedia.org/wiki/Datalog>`_.

   fact
      A concrete relation assertion such as ``binds(aspirin, ptgs1)``. A fact
      supplied to an evaluator is an input, not independent verification that
      the assertion is true in the real world.

   predicate
      The relation name, such as ``binds`` in ``binds(aspirin, ptgs1)``.
      It is not necessarily a Boolean-valued Python function.

   arity
      The number of arguments of a relation. ``binds(drug, protein)`` has arity
      two (binary); ``measurement(sample, value, unit)`` has arity three.

   atom
      A relation-shaped expression used in a rule, potentially containing
      variables: ``binds(x, y)``. A fact has concrete arguments; an atom can be a
      pattern to match. See :doc:`reasoning-introduction`.

   grounded
      Having concrete values in place of variables. Binding ``x`` to
      ``aspirin`` and ``y`` to ``ptgs1`` grounds ``binds(x, y)``.

   rule body
      The premises or conditions that must all hold for a rule application.
      Variables shared across conditions must receive compatible values.

   rule head
      The conclusion derived when a rule's body holds. In
      ``candidateFor(x, z) ← binds(x, y) ∧ inPathway(y, z)``, the left-hand
      expression is the head and the right-hand conditions form the body.

   safe rule
      A rule whose conclusion variables are bound by positive body relations;
      filters and negated variables have additional binding restrictions.
      This prevents deriving arbitrary new values from unbound variables.

   closure
      The input facts plus the consequences produced under the selected program.
      Full closure is different from answering only one selected goal. See
      :doc:`reasoning-demand` for selective evaluation.

   fixpoint
      A stage where another round of rule application adds no new facts. For
      finite positive Datalog, this is the stopping condition for full closure.
      It is not a claim that the input knowledge is complete or correct.
      See `fixed point <https://en.wikipedia.org/wiki/Fixed_point_(mathematics)>`_.

   recursion
      Rules depending directly or indirectly on their own conclusions. For
      example, reachability can extend a known path by another edge. Termination
      and work bounds still depend on the supported finite fragment.

   delta
      The facts newly added in a round or update. Semi-naive evaluation uses
      newly added facts to avoid repeating every old combination. This is a
      physical evaluation technique, not a different set of logical conclusions.

   witness
      One recorded derivation explaining how a conclusion follows: the rule and
      the concrete premises used, recursively linked to their own explanations.
      Here “witness” means an explanation path, not a person or all possible
      reasons. A bounded first-witness trace may omit other derivations. See
      :doc:`reasoning-introduction` and :doc:`reasoning-proofs`.

   support
      A recorded reason for retaining a conclusion, such as one grounded rule
      application. Multiple supports may independently justify the same fact.
      Deleting one support need not remove the conclusion. A witness display is
      not an all-support maintenance index (:doc:`reasoning-maintenance`).

   DAG
      A directed acyclic graph: arrows have direction and no directed cycle.
      An explanation DAG can share a premise between several derivations instead
      of repeating a whole tree. This does not mean the input graph or rule
      dependency graph must be acyclic. See
      `directed acyclic graph <https://en.wikipedia.org/wiki/Directed_acyclic_graph>`_.

   literal
      A typed data value such as integer ``18``, text ``"18"``, or an instant.
      Typed Datalog distinguishes these values and entity names; there is no
      implicit conversion between them (:doc:`reasoning-typed`).

   closed scope
      An explicit declaration that a selected predicate's absence may be checked
      against the supplied complete scope. It is not a claim that the whole
      world or every source is known (:doc:`reasoning-negation`).

   stratification
      Ordering evaluation into layers so that a negated relation has been
      computed before absence is checked. Negative dependency cycles are rejected
      rather than guessed. Nonrecursive aggregates have their own restrictions
      (:doc:`reasoning-typed`, :doc:`reasoning-negation`).

   ontology
      A named model of classes, relations and semantic axioms, such as “every
      Researcher is a Person”. GestaltDB compiles a selected fragment to rules;
      it does not claim full OWL support (:doc:`ontology`).

   axiom
      A statement in the semantic model, such as subclass, domain or inverse
      property declarations. Compiled rule origins link derivations back to
      these source statements; compilation does not verify their real-world truth.

   TBox
      Terminology/schema statements in an ontology, in contrast to facts about
      named individuals (the ABox). These are ontology terms, not two physical
      database tables. See
      `description logic <https://en.wikipedia.org/wiki/Description_logic>`_.

   ABox
      Assertions about named individuals, in contrast to the ontology's TBox.
      An RDF import profile may accept schema axioms while rejecting ABox data.

   subsumption
      An inclusion relationship, for example every Researcher being a Person.
      Inferring superclass membership is not a command to rewrite stored labels
      or merge entities (:doc:`ontology`).

   bitemporal
      Tracking two time dimensions: when an assertion applies (valid time) and
      when it was recorded (system time). This supports both “what applied then?”
      and “what had we recorded about it by then?” (:doc:`temporal-versioning`).

   world
      A named context containing claims, such as an actual-world account or an
      agent's belief context. A modal world is not a separate database backend.

   accessibility
      Explicit agent-specific links between worlds used by modal operators.
      A knowledge link and a belief link belong to separate frames; the library
      does not infer these links automatically (:doc:`modal-epistemic`).

   paraconsistent
      Allowing positive and negative evidence for a proposition without making
      every unrelated proposition follow from the contradiction. GestaltDB's
      four-valued claim status distinguishes supported, refuted, both and unknown.

   cohort
      A selected set of entities, such as proteins with score at least 0.8.
      Selecting seed entities does not restrict every later sampling hop
      (:doc:`property-filtering`).

   seed
      A starting entity or edge for a sampling operation, not necessarily a random
      number-generator seed. The latter controls reproducibility; name which
      meaning is intended in examples.

   fanout
      The requested number of neighbors drawn at a sampling step. Constraints,
      eligibility and replacement policy determine whether that many can be
      returned (:doc:`typed-sampling`).

   CSR
      Compressed sparse row: an array layout storing each source row's adjacency
      in a slice located by offsets. Snapshot samplers use compact integer IDs
      with these arrays, not Python graph objects per draw. See
      `sparse matrix storage <https://en.wikipedia.org/wiki/Sparse_matrix#Compressed_sparse_row_(CSR,_CRS_or_Yale_format)>`_.

   negative sample
      A candidate used as a negative training example under an explicit sampling
      policy. “Not known positive” is not proof of a false proposition, nor an
      epistemic negative claim (:doc:`typed-sampling`).

   provenance
      Information about where data or a result came from: for example, source
      version IDs, rule applications or snapshot identity. A provenance record
      has only the guarantees of the API that produced and verifies it.

   authentication
      In source/artifact APIs, checking expected database/source/state identity.
      This is not user login, a truth guarantee, or independent certification of
      every derivation (:doc:`reasoning-authenticated-sessions`).

   valid time
      When an assertion applies in the modeled world. Half-open interval
      ``[a, b)`` includes ``a`` but excludes ``b`` (:doc:`temporal-versioning`).

   system time
      When an update was recorded in database history. A recorded-history horizon
      limits which updates a reader may know about; it is distinct from valid time.

   temporal segment
      A time interval between change points where the selected active input set
      is constant. Temporal closure evaluates these intervals to determine when
      conclusions hold (:doc:`reasoning-temporal`).

   admission
      Explicit verification that a source/result/state matches the requested
      identities and supported execution contract. It is not automatic authority
      to publish results (:doc:`reasoning-generation-identity`).

   materialization
      Computing a result and retaining it for subsequent use, rather than deriving
      it again for each request. Publication of materialized conclusions is an
      explicit operation, not an automatic side effect of evaluation.

   stratum
      One evaluation layer in a stratified program. Dependencies must respect the
      layer order, especially when testing that another relation is absent.

   semi-naive evaluation
      Computing each rule round using at least one newly added input rather than
      repeatedly joining all old facts. It changes execution work, not the
      intended positive-program closure.

   SCC
      Strongly connected component: a group in a directed dependency graph where
      every member can reach every other. Prepared plans use these components to
      organize recursive positive rules (:doc:`reasoning-plans`).

   DRed
      Delete and rederive: a maintenance strategy that removes potentially affected
      conclusions after a deletion, then restores those still derivable from
      surviving support. It is not simply deleting every dependent fact forever.

   CAS
      Compare and swap: update only if the observed state still matches an expected
      state. Session/publication APIs qualify this check with exact identities and
      coordination; a checksum alone does not make an arbitrary file update atomic.

   MVCC
      Multiversion concurrency control: readers observe a selected consistent
      storage version while writes may create newer versions. GestaltDB's backend
      snapshot guarantees remain backend/runtime-specific (:doc:`storage-backends`).

   horizon
      The recorded-history boundary selected for a read or computation, such as
      the visible commit prefix. It limits which updates are visible, separately
      from the valid-time instant being queried.

   generation
      An identified version of prepared, evaluated or persisted reasoning state.
      Always specify the relevant API's source/program/state pins; the word does
      not imply every generation is independently source-authenticated.

   manifest
      Configuration metadata describing a database directory's backend, codec and
      format identity. It enables compatible reopen, not arbitrary format conversion.

   codec
      An encoder/decoder, called a serializer in these guides. A backend stores
      bytes; a codec determines how supported payloads become and recover from bytes.

   index freshness
      Whether derived index entries agree with the canonical records they describe.
      Deferred writes can mark index families stale; rebuilding restores the
      documented lookup contract (:doc:`ingestion`).

   native path
      A capability that uses the storage/runtime's compiled scan or writer APIs.
      It does not automatically provide stronger source authentication, lower
      total memory use, or a speedup for every workload.

   cardinality
      A count of distinct members or facts under the stated semantics. Validation
      cardinality constraints do not necessarily count physical duplicate records
      or edges (:doc:`reasoning-validation`).

   magic sets
      A demand-evaluation technique that specializes rule work around a requested
      goal and its bound arguments, rather than requesting every conclusion.
      Unsupported combinations require the documented complete strategy
      (:doc:`reasoning-demand`).

External links above are optional background reading, not specifications of
GestaltDB support. The local guides and supported-profile references define the
library's actual capabilities.
