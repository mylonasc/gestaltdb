# Historical temporal-introduction observations

The introductory range guide formerly included these workload observations
before its example. They are retained here rather than mixed into the new
learning path or presented as current-wheel measurements:

- A fragmented fixture (400 inputs, 199 segments) reported 800 events and
  1,957 scheduled memberships instead of 79,600 complete input-scan checks.
  These are operation counts, not a wall-time speedup.
- A 20-edge chain with sliding windows reported 29 segments, 935 derivations
  with complete supports and approximately 150 ms single-threaded.
  The original introductory prose did not bind that timing to a source revision
  or complete host configuration; it is not a current-release latency claim.

Current contracts are in `docs/reasoning-temporal.rst` and
`docs/reasoning-temporal-sweep.rst`. Reproducible reference measurements and
limitations are documented in `docs/reasoning-conformance.rst` and its reports.
