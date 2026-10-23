:orphan:

Resumable incremental reasoning sessions
========================================

``gestaltdb.reasoning.adapters.sessions`` connects committed source-version
deltas to the exact-version evidence ledger and maintenance support indexes.
``start_session`` pins a database, program, rule catalog, world/selection
mapping scope and source commit horizon, then materializes the closure from
one full scan. ``advance_session`` consumes the exact versions committed
since the pinned horizon: assertions become typed facts through an explicit
claim mapping, corrections retire their superseded version atomically, and
retractions retire the named versions. Logical insertions propagate through
existing supports; logical retractions use DRed against the pinned dependency
index. Every advance is verified against the pinned database identity, and a
backwards-moving horizon fails closed.

Unknown history never guesses: a delta referencing versions the session never
observed, an invisible commit, or an over-wide gap triggers an explicit full
rebuild instead of incremental repair. Alternative sourced assertions for one
fact survive single retractions; the fact disappears only with its final
active version.

This milestone advances sessions in memory; the returned session value is the
resumable state. Durable cross-process persistence of that value is not
implemented. Sessions do not publish; publication stays with the managed
generation APIs. Identity strings and ledger records alone do not authenticate
data — the session verifies its database on every advance.
