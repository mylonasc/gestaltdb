# Ontology extensions — consolidated into the reasoning roadmap

**Status authority:** [EPIC_REASONING_ENGINE.md](EPIC_REASONING_ENGINE.md),
reviewed 2026-11-01T11:27:42Z against main `3403530`.

Parent #391 and children #392–#396 are closed. Their delivered scopes,
limitations, merge dates/commits and release boundaries are maintained in
sections 2–4 of that document, not in a second proposal/status ledger here:

- #392: bounded typed first-witness explanations.
- #393: selected temporal stratified negation and nonrecursive aggregates.
- #394: declarative closed-class structural validation.
- #395: explicit generation-pinned read-only inferred Cypher.
- #396: typed graph-to-fact mappings with explicit identity/collision policy.

Initial implementations are in v0.16.0. Acceptance hardening #448/#449 was
merged **after** that release tag and is present on reviewed main, not in
the published v0.16.0 wheel. Full OWL/SHACL, automatic CDM/Rune translation,
unrestricted inferred queries and recursive aggregates remain non-goals.
Tutorial #273 and CDM #409 are separate open documentation/application tracks.

This compatibility pointer preserves existing links. The original 2026-10-26
proposal analysis and its then-missing capabilities remain in git history;
they are no longer a current backlog or implementation order.
