:orphan:

Generation-bound executor admission
===================================

``gestaltdb.reasoning.admission.admit`` checks one explicit
``AdmissionRequest`` (mode, executor, dictionary) against a pinned
``GenerationIdentity``. Dictionary identity must match exactly; witness and
stateful modes (``proofs``, ``maintenance-supports``, ``temporal-closure``)
admit indexed engines only, and the Polars prototype stays closure-only.
Anything else fails closed instead of silently weakening the request. The
admitted triple records the generation digest it was checked against, so a
changed program, source horizon or dictionary means a new admission.

This module does not select executors — ``select_executor`` in
``reasoning/executors.py`` still makes the physical choice — and it does not
interpret semantic profiles; the profile travels as opaque generation
metadata. Enabling a new physical path additionally requires a measured
end-to-end crossover from the benchmark work, not just admission.
