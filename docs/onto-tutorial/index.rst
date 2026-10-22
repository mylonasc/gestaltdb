Gestalt Graph Onto-Tutorial
===========================

An ontology-reasoning tutorial for advanced coders, grounded in the
``gestalt-core-v1`` fragment on ``feature/onto-dev-epic-2``.

This tutorial is (1) a basic ontology-reasoning walkthrough with gestaltdb,
(2) an introduction to the intermediate representations and foundational
capabilities underneath — and how gestaltdb supports them efficiently — and
(3) an explicit map of gaps and pain points, linked to open issues.

Conventions: every chapter states its fragment, shows the math, runs code via
the :doc:`toolkit <toolkit/index>`, and links back to ``src/gestaltdb`` and the
epic issues (#156–#161, #248–#256, #194, #200, PropEnh #124–#128).

.. toctree::
   :maxdepth: 2
   :caption: Tutorial

   ch01-property-graph-vs-semantic
   ch02-ontology-fragment
   ch03-datalog-irs
   ch04-storage-grounding
   ch05-claims-temporal
   ch06-query-sampling
   ch07-performance
   ch08-gaps
   toolkit/index
   notebooks/index
   OUTLINE_COVERAGE
