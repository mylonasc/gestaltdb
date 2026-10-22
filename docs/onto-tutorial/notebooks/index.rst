Tutorial notebooks
==================

Use the environment described in :doc:`../index`. Download or open each lab:

* :download:`nb01_subclass_closure.ipynb` — compile ``SubClassOf`` + ``Domain``,
  evaluate with ``DatalogEngine``, inspect ``result.explain``.
* :download:`nb02_property_chains.ipynb` — binary ``PropertyChain`` + transitivity,
  origin table back to axiom IDs.
* :download:`nb03_graph_projection.ipynb` — project a pinned LMDB property graph
  to facts, infer membership, and visualize a caller-owned logical graph without
  modifying stored labels.

Notebook outputs are intentionally uncommitted. Tests execute every code cell
in order with a fresh namespace and fail on exceptions or failed assertions;
they do not merely syntax-check the JSON. The HTML build offers downloads,
not a notebook execution service.
