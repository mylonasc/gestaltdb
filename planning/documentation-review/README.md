# Documentation review working inventory

Parent: [#480](https://github.com/mylonasc/gestaltdb/issues/480).
Foundation: [DOC-01 #481](https://github.com/mylonasc/gestaltdb/issues/481).
Baseline: v0.17.0 (`8199b1d1025ae64967f8fa300f9542a324a15bd3`).

## Artifacts

- `capabilities.json`: source/test-backed capability-family matrix. Each row has
  an owner, status, existing documentation and a specific remediation gap.
- `documents.json`: exhaustive classification of current Sphinx pages, packaged
  agent Markdown resources and cross-surface entry points. Groups share an
  audience, role, disposition and rationale; they are not a proposed deletion list.
- `authoring-contract.md`: shared terminology, notation and example conventions.
- `graphics.md`: concrete visuals backlog with teaching goals and accessibility gates.
- `check_inventory.py`: stdlib-only validation and readable matrix output.

Run from any directory:

```sh
uv run python planning/documentation-review/check_inventory.py
uv run python planning/documentation-review/check_inventory.py --matrix
uv run pytest tests/test_documentation_inventory.py -q
```

The checker requires every existing `docs/*.rst` page and every packaged agent
Markdown resource to have exactly one classification. It compares packaged topic
resources with the literal `TOPICS` registry without importing the library and
checks that source, test, documentation and ownership references exist. New
pages/resources therefore require an inventory update instead of silently escaping
the review. No spreadsheet or optional backend is needed.

## Reading the matrix

`existing_docs` are **coverage pointers**, not certification that those pages are
complete tutorials or that their examples run. `gap` records the next required
change; `owner` is the slice accountable for resolving it. `status` distinguishes
supported families from opt-in experiments, compatibility shims and deferred
proposals. A family marked supported does not imply that every combination of its
features or every backend supports the same contract.

This is a semantic capability inventory, not a generated count of every method.
New public API families and subfeatures must be reconciled against it during code
review; file-existence checks cannot prove semantic completeness or correctness.
The initial source/test citations are representative regression anchors, not a
claim that all cited tests were rerun during the inventory pass.

## Delivery workflow

1. Select an owner slice and review its rows against actual source/tests.
2. Write the concise overview and task examples before expanding reference text.
3. Record example prerequisites and execution classification explicitly.
4. Update coverage pointers, page classifications and remediation gaps as pages
   move or split. Keep old external URLs working or provide migration links.
5. Run inventory, documentation/API, strict Sphinx and applicable example gates.
6. Record visuals as `VIS-*` items linked to the graphics issue; a prose guide
   does not have to wait for an optional animation to ship.

DOC-10 performs final audience-journey reconciliation; the existence checker is
not a substitute for that acceptance review.
