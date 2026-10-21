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
``cancel`` cooperates between chunks and raises without a report. Violations
reference supporting ``TypedFact`` values and stable constraint identities
(``disjoint:A:B``, ``functional:age``), so every conflict traces to exact
inputs and an exact scope entry.

What is *not* claimed
----------------------------------------

Domain and range axioms keep their entailment meaning in the compiler; they do
not validate required fields, and validation never emits facts. Unsatisfiable
classes (``find_unsatisfiable``) are a static schema check over subclass edges
and disjointness pairs — no instance data is read, and anonymous constructions
are out of scope. Full SHACL-style shapes, satisfiability beyond disjointness
closure, probabilistic combination and classical explosion are explicitly
deferred; the report vocabulary stays within the four implemented checks and
fails closed on anything else.

Measured cost
----------------------------------------

Validation scans the closure once with small per-fact indexes: 20,000 typed
facts with 100 planted disjointness conflicts validate in about 70ms warm
(single process), linear in closure size with bounded memory. See the roadmap
register for the frozen budget reference.
