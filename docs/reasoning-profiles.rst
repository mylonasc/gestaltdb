:orphan:

Gated profile expansion and conformance matrix
============================================================

``gestaltdb.reasoning.profiles`` is the construct-by-construct conformance
matrix: every known construct has exactly one row naming its role
(entailment, validation, equality, deferred), its standing (supported,
validation-only, deferred), its implementing mechanism and its boundary
note. Directional Horn rewrites, open-world validation and class-conflict
detection are separate rows — never one blurred claim.

.. code-block:: python

   from gestaltdb.reasoning import check_construct, subset_constructs

   assert check_construct("subproperty").mechanism == "ontology.compiler"
   assert "subclass" in subset_constructs("gestaltdb-rl-core-v1")
   check_construct("disjunctive-head")  # raises: non-Horn, no case splitting

Named subsets
----------------------------------------

``gestaltdb-rl-core-v1`` (entailment), ``gestaltdb-validation-v1`` and
``gestaltdb-equality-v1`` are the whole published claim: admission goes
only through the named subset, and anything without a ``supported`` row —
unknown names, deferred rows, subset mismatches — fails closed with the
documented mechanism or reason. Complete OWL RL becomes a target only
when every normative applicable rule is independently covered; until
then no full-RL claim is made.

Approval per extension
----------------------------------------

Extending a subset is an approval, not a flag: specify the semantics and
termination contract, implement with exact origins, add oracle/parity and
failure tests, measure cost, then change the matrix, the subsets and the
pinned ``matrix_digest`` together. The digest test makes silent widening
impossible — every new row arrives with its evidence.
