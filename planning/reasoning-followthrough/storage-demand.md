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

Update (PR #330, merged): multi-predicate plans now run one indexed
per-predicate pass under the same pinned view with exact version-ID dedup and
recorded ``predicates``/``predicates+subject`` pushdown modes. Demand-closure
parity is tested against full extraction, including recursive frontiers and
asserted headed facts, with scanned-row accounting.

Update (alias inversion, in progress): pushing a logical demand plan directly
matches nothing when aliases exist, because request predicates are
storage-level. ``ClaimMapping.storage_predicates`` inverts logical names to
the exact storage set (aliased sources plus unmapped passthrough; aliased-away
names excluded), with parity tested through aliased extraction and closure.
Binding/frontier I/O and native acceleration measurements remain open; #194's
native decoder comparison is untouched.
