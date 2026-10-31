:orphan:

Explicit negatives and validation reports
============================================================

``gestaltdb.reasoning.validation`` checks a finite typed closure against an
explicit ``ValidationSuite`` and returns bounded ``Violation`` records with
exact supporting facts and constraint identities. Validation never infers,
never merges, and never explodes: the input closure stands unchanged, and a
report is evidence — not a classical inconsistency and not a shape-validation
certificate beyond the implemented checks.

.. code-block:: python

   from gestaltdb.reasoning.terms import Literal
   from gestaltdb.reasoning.typed import TypedFact
   from gestaltdb.reasoning.validation import Denial, ValidationSuite, validate

   facts = [TypedFact("type", ["mallory", "Cat"]), TypedFact("type", ["mallory", "Dog"])]
   suite = ValidationSuite("type", disjoint=[("Cat", "Dog")])
   report = validate(facts, suite)
   assert not report.clean
   assert report.violations[0].kind == "disjoint-members"
   assert report.violations[0].sources == ("disjoint:Cat:Dog",)

What is checked
----------------------------------------

- **Denied positives**: an explicit ``Denial(predicate, values)`` contradicting
  an identical ground fact. Absence of a fact is not a denial; only stated
  denials participate, so open-world silence never becomes a conflict.
- **Disjoint membership**: two membership facts sharing an entity under
  disjoint classes. Membership reads the suite's explicit membership predicate
  (callers validating ontology closures pass the same predicate the compiler
  emits); no vocabulary is assumed.
- **Reflexive loops**: ``P(x, x)`` facts on properties declared
  irreflexive.
- **Functional conflicts**: two facts sharing subject and predicate with
  different remaining values. The pair is reported with a note that equality
  is *not* inferred — equality-generating functionality belongs to RE-11.

``max_violations`` bounds the report; hitting it sets ``truncated`` instead of
failing, and deterministic canonical order makes repeat runs identical.
``cancel`` cooperates during indexed input processing and raises without a report;
input is fully buffered and sorted before those periodic checks. It does not
interrupt a blocking input iterator. ``max_violations`` limits report size, not
input memory. Violations
reference supporting ``TypedFact`` values and stable constraint identities
(``disjoint:A:B``, ``functional:age``), so every conflict traces to exact
logical input facts and a structural scope entry. These source strings are
generated constraint labels, not caller-supplied axiom IDs or exact historical
claim-version references.

What is *not* claimed
----------------------------------------

Domain and range axioms keep their entailment meaning in the compiler; they do
not validate required fields, and validation never emits facts. Unsatisfiable
classes (``find_unsatisfiable``) are a static schema check over subclass edges
and disjointness pairs — no instance data is read, and anonymous constructions
are out of scope. Full SHACL-style shapes, satisfiability beyond disjointness
closure, probabilistic combination and classical explosion are explicitly
deferred; the report vocabulary stays within the four implemented checks and
fails closed on anything else. Ground ``Denial`` values are validation inputs,
not polarity-aware rule premises: missing positive evidence cannot satisfy a
denied premise. Generic rules support positive atoms and explicitly scoped
absence, not signed-denial premises. Datatype shape/constraint checking is not
provided by this suite; strict ``Literal`` construction is a separate contract.

Measured cost
----------------------------------------

The original small-workload measurement was about 70ms warm for 20,000 typed
facts with 100 planted disjointness conflicts (single process). It is not a
general complexity or release-performance guarantee: validation buffers the
closure, sorts/deduplicates it, and some checks rescan it per constraint or
conflict. See #200 (D6) for the indexing/cancellation follow-up; callers must
bound their input independently.

Declarative structural validation (#394)
----------------------------------------

``gestaltdb.reasoning.structural`` covers shape constraints beyond conflict
detection: required properties, min/max cardinalities, literal-kind datatype
restrictions and mutually exclusive / exactly-one-of property groups. Each
constraint is scoped to one class, and checks run only for entities typed as
members of an explicitly declared ``closed_classes`` set — everything outside
that scope keeps open-world silence, so partial exports never flood the
report with absence assumptions.

.. code-block:: python

   from gestaltdb.reasoning.structural import (
       ExclusiveGroup, RequiredField, StructuralSuite, validate_structural,
   )
   from gestaltdb.reasoning.typed import TypedFact

   facts = [TypedFact("type", ["n", "Trade"])]
   suite = StructuralSuite("type", closed_classes=["Trade"],
                           required=[RequiredField("Trade", "tradeId")],
                           exclusive=[ExclusiveGroup("Trade", ["cash", "physical"])])
   report = validate_structural(facts, suite)
   assert [item.kind for item in report.violations] == ["exclusive-violation", "missing-required"]

Like the conflict suite, structural validation is read-only and bounded:
deterministic canonical order, ``max_violations`` report budget with
``truncated``, cooperative ``cancel``, and no inferred facts. The profile
matrix admits ``required-property``, ``cardinality-bounds``,
``datatype-check`` and ``exclusivity`` as ``validation-only`` rows via
``reasoning.structural``; the older deferred ``cardinality``,
``datatype-facets`` and ``shape-constraints`` rows keep their inference-level
meaning and are unchanged.

Constraint identities and evidence
~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~

Structural violation ``sources`` use versioned, canonical JSON components,
for example ``structural:v2:required:["Trade","tradeId"]``. Cardinality
identifiers include both bounds; exclusive-group identifiers include the
sorted predicates and mode. Names containing delimiters cannot alias other
constraints. These replace the older ambiguous colon-joined structural
labels; consumers should treat them as opaque identifiers, not split them on
colons. The suite digest and declarative constraint constructors are unchanged.

Every structural violation includes the class-membership fact establishing
its scope, plus the offending property facts when present. Missing-property
reports describe absence only under the declared closed-class assumption;
they do not invent a negative assertion or claim-version provenance.
Reports have a total canonical order, including constraint identity and
detail when kind and supporting facts tie.

Cardinality counts distinct logical facts, not duplicate input occurrences
or distinct object values in an n-ary fact. Exclusivity counts predicates
present, not their number of facts. Datatype checks require every object
argument to be a ``Literal`` of exactly the requested kind; entity strings
are references, not text literals. Constraints on undeclared classes remain
inactive. Full SHACL, datatype/value facets, cross-entity uniqueness and
automatic inference of completeness are unsupported.

Cancellation is checked periodically while consuming and indexing inputs,
and between entities and constraints. It raises without returning a partial
report. Input is still buffered, deduplicated and sorted in memory;
cancellation does not interrupt a blocking iterator or an individual sort.
The violation budget limits reports, not input memory, and reaching it marks
the report truncated even if it happens to be the final violation.
