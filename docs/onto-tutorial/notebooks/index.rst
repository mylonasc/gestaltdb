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

Work through the labs in order if you want hands-on reinforcement. The first
isolates the meaning of two axioms; the second shows why combining rules is
more interesting than applying each once; the third connects the same semantics
to stored data and verifies that inference has not silently changed its labels.

Each lab includes assertions for the expected answers. Read the accompanying
explanation before executing the cell, predict the result, then vary a premise
or axiom. If an answer changes, identify the rule body that stopped matching.
That habit is more useful than rerunning a notebook until it reports success.

Outputs are omitted so that you can produce them in your own environment.
The HTML documentation offers downloads; the PDF describes the labs but does
not execute or embed a live notebook session.
