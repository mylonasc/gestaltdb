# Storage demand milestone — #249

The first executable milestone is `reasoning.demand.required_predicates`:
a conservative predicate-dependency plan including asserted facts whose
predicates also occur in rule heads. Tests retain recursive intermediate
subjects and compare specialized answers before/after predicate pruning.

This does not yet reduce backend reads: passing a predicate set to an adapter
that scans then filters is not pushdown. Integration remains blocked by #248
and #194. Next steps are predicate-alias inversion, indexed multi-predicate
claims extraction under one view, and a frontier strategy that never confines
all recursive reads to the initial bound subject. Track scanned/decoded rows,
not merely the number of final answers.
