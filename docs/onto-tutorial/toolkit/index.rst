Tutorial toolkit
================

``toolkit/`` holds the figure/equation/snippet/viz helpers used by every
chapter and notebook. Stdlib-first; ``matplotlib`` only for static figures.

* ``figures.py`` — deterministic SVG emitters (subsumption lattices, TPM
  diagrams, SCC plans) + ``save_svg``.
* ``viz_helpers.py`` — thin wrappers over ``gestaltdb.viz.api`` with the
  tutorial's 2000/5000 caps and ``edge_labels="all"`` default.
* ``snippets.py`` — copy-paste cells with ``graph.close()`` discipline.
* ``equations.md`` — MyST math conventions (``$$`` blocks, operator macros).

.. toctree::
   :maxdepth: 1

   equations

.. code-block:: python

   from docs.onto_tutorial.toolkit import figures  # noqa
