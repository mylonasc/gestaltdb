Tutorial toolkit
================

``toolkit/`` contains the tutorial's illustration helpers. They use the standard
library and GestaltDB's packaged visualization API, with no Matplotlib, browser,
Graphviz, or embedded backend required for SVG generation.

* ``figures.py`` — a minimal vertical class-graph SVG helper and ``save_svg``.
* ``viz_helpers.py`` — thin wrappers over ``gestaltdb.viz.api`` with the
  tutorial's 2000/5000 caps and ``edge_labels="all"`` default.
* ``equations.md`` — MyST math conventions (``$$`` blocks).
* ``landscape.py`` — the ch01 concept graph as real ``Node``/``Edge`` objects
  in 3 progressive stages; ``uv run python docs/onto-tutorial/toolkit/landscape.py``
  regenerates ``figures/landscape-stage{1,2,3}.svg`` and the worked subsumption
  figure from GestaltDB SVG geometry. A presentation-only pass enlarges text
  and removes the white text stroke for converters without ``paint-order``
  support. The latter figure's inferred membership edges come from
  ``DatalogEngine`` rather than a hand-drawn assertion of what it should infer.

Run from the repository root::

   uv run python docs/onto-tutorial/toolkit/landscape.py

Static SVGs use supplied coordinates; they do not capture an interactive
notebook's browser state. The test suite regenerates them in a temporary
directory and compares them with the committed files.

.. toctree::
   :maxdepth: 1

   equations
