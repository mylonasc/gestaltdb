:orphan:

Resumable incremental reasoning sessions
========================================

``gestaltdb.reasoning.adapters.sessions`` connects committed source-version
deltas to the exact-version evidence ledger and maintenance support indexes.
``start_session`` pins a database, program, rule catalog, world/selection
mapping scope and source commit horizon, then materializes the closure from
one full scan. ``advance_session`` consumes the exact versions committed
since the pinned horizon: assertions become typed facts through an explicit
claim mapping, corrections retire active versions of their logical claim
atomically, and retractions retire that logical claim's observed versions.
Only changes whose half-open valid interval contains the session's pinned
valid-time point affect its active evidence. For a point at ``50``, retractions
over ``[0,50)`` or ``[51,100)`` leave evidence intact; ``[50,51)`` retracts it.
The same rule applies when resuming a persisted session.
Logical insertions propagate through
existing supports; logical retractions use DRed against the pinned dependency
index. Every advance is verified against the pinned database identity, and a
backwards-moving horizon fails closed.

Unknown history never guesses: a delta referencing versions the session never
observed, an invisible commit, or an over-wide gap triggers an explicit full
rebuild instead of incremental repair. Alternative sourced assertions for one
fact survive single retractions; the fact disappears only with its final
active version.

This milestone advances sessions in memory; the returned session value is the
resumable state. ``gestaltdb.reasoning.adapters.session_store`` adds durable
cross-process persistence for that value: ``save_session`` writes one versioned
JSON document atomically (temporary file plus rename, so readers observe the
old or the new session, never a partial write), ``load_session`` verifies the
embedded digest, program/catalog digests, index binding and ledger consistency,
and ``advance_persisted_session`` resumes a file in a new process, advances
through live commit deltas (or explicit rebuild) and re-persists with a
horizon compare. A concurrent writer changing the file between load and
persist fails closed; callers reload and retry. Sessions do not publish;
publication stays with the managed generation APIs. Identity strings and
ledger records alone do not authenticate data — every resume re-verifies the
live database identity and horizon.
