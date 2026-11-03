Generation-bound executor admission
===================================

``gestaltdb.reasoning.generation.admit`` checks one explicit
``AdmissionRequest`` (mode, executor, dictionary) against a pinned
``GenerationIdentity``. Dictionary identity must match exactly; witness and
stateful modes (``proofs``, ``maintenance-supports``, ``temporal-closure``)
admit indexed engines only, and the Polars prototype stays closure-only.
Anything else fails closed instead of silently weakening the request. The
admitted triple records the generation digest it was checked against, so a
changed program, source horizon or dictionary means a new admission.

This module does not select executors — ``select_executor`` in
``reasoning/executors.py`` still makes the physical choice — and it does not
interpret arbitrary semantic profiles; legacy binary rows keep profile metadata
opaque. The explicit ``typed-indexed-python-v1`` row additionally requires
``positive-typed-point-v1`` / ``typed-values-v1`` and closure mode. See
:doc:`reasoning-typed-execution` for real typed dispatch and authenticated session
input. Admission checks supported compatibility; it neither runs an executor
nor establishes that a different physical path would be faster.
