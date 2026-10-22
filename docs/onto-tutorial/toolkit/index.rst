Tutorial toolkit
================

``toolkit/`` contains the tutorial's illustration helpers. The landscape uses
GestaltDB graph objects plus the build-time Graphviz ``dot`` executable, not a
browser or an embedded database. Other helpers retain the packaged GestaltDB
visualization API. No production-library rendering behavior is changed.

* ``figures.py`` — a minimal vertical class-graph SVG helper and ``save_svg``.
* ``viz_helpers.py`` — thin wrappers over ``gestaltdb.viz.api`` with the
  tutorial's 2000/5000 caps and ``edge_labels="all"`` default.
* ``equations.md`` — MyST math conventions (``$$`` blocks).
* ``landscape.py`` — the ch01 concept graph as real ``Node``/``Edge`` objects
  in 3 progressive stages; ``uv run python docs/onto-tutorial/toolkit/landscape.py``
  regenerates ``figures/landscape-stage{1,2,3}.svg`` and the worked subsumption
  figure using Graphviz's hierarchical ranking, crossing reduction, cluster
  placement, and spline routing. Rounded filled nodes have large bold names,
  category badges, and padded interiors; relationship labels wrap independently.
  Dashed green edges mark computed memberships in the worked example.
  Those inferred membership edges come from
  ``DatalogEngine`` rather than a hand-drawn assertion of what it should infer.
  The same renderer generates ``figures/reasoning-cycle.svg`` as a conceptual
  explanation of the fixpoint, not as an execution trace of a particular plan.

Run from the repository root::

   # Install Graphviz first (e.g. apt install graphviz on Debian/Ubuntu).
   uv run python docs/onto-tutorial/toolkit/landscape.py

No node coordinates are hand-maintained. Committed SVGs keep HTML/PDF builds
independent of Graphviz; only regeneration needs ``dot`` and the DejaVu fonts.
Graphviz versions can change placement without changing the graph. Tests check
graph content, large text, spline paths, and repeatability on the installed
version rather than requiring one version's exact coordinates everywhere.
SVG text remains selectable. These static figures do not capture an interactive
notebook's browser state. See :doc:`../index` for ``build_docs.sh --pdf``.

.. toctree::
   :maxdepth: 1

   equations
