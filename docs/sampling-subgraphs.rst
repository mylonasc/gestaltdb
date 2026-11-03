Sample subgraphs for training
==============================

Choose a topology sampling method by its selection rule, not just its output
shape. An :term:`induced subgraph` contains every eligible edge between selected
nodes. A neighbor expansion instead follows chosen edges and need not contain
every edge between the reached nodes. See :doc:`sampling-introduction` for ID
domains and the distinction between seed filters and eligible-graph filters.

.. list-table:: Subgraph selection rules
   :header-rows: 1

   * - Method
     - Selection
   * - ``sample_subgraph``
     - Seed edges, sampled neighborhoods, optional training negatives
   * - ``sample_packed_subgraphs``
     - One independent edge-seeded expansion per group, with component offsets
   * - ``sample_induced_subgraph``
     - Uniform node draw and every eligible induced edge under a required cap
   * - ``sample_partition_block``
     - One uniform random balanced partition block, not a cut-optimized partition

Recover graph edges from local rows
------------------------------------

This standalone example needs no backend. It draws three of four nodes, retains
all eligible induced edges, then maps their local endpoints back to application
IDs. It also packs two seed-edge groups without merging their node frames.

.. code-block:: python

   from tempfile import TemporaryDirectory
   import numpy as np
   from gestaltdb.sampling import SamplerEngine, SamplerSnapshot

   with TemporaryDirectory() as root:
       snapshot = SamplerSnapshot.from_edge_arrays(
           f"{root}/snapshot", external_node_ids=["a", "b", "c", "d"],
           external_edge_ids=["ab", "ac", "bd", "cd"],
           external_relation_ids=["R"],
           src_int=[0, 0, 1, 2], dst_int=[1, 2, 3, 3], rel_int=[0, 0, 0, 0],
       )
       engine = SamplerEngine(snapshot, seed=7)
       batch = engine.sample_induced_subgraph(3, max_edges=4, normalize=True)
       names = [snapshot.external_node_id(node) for node in batch.node_ids_global]
       pairs = {(names[source], names[target])
                for source, target in zip(batch.senders, batch.receivers)}
       expected = {(a, b) for a, b in [("a", "b"), ("a", "c"), ("b", "d"), ("c", "d")]
                   if a in names and b in names}
       assert pairs == expected
       assert len(names) == 3
       assert np.allclose(batch.node_weights, 4 / 3)
       assert np.allclose(batch.edge_weights, 2.0)
       packed = engine.sample_packed_subgraphs(
           [[snapshot.edge_int("ab")], [snapshot.edge_int("cd")]], fanouts=[1],
       )
       assert len(packed.graph_node_offsets) == len(packed.graph_edge_offsets) == 3
       assert packed.positives.shape == (2, 3)

``senders`` and ``receivers`` are local rows, not snapshot IDs. Packed results
use concatenated local frames delimited by ``graph_node_offsets`` and
``graph_edge_offsets``; the same global node can appear in several components.
Positive rows follow seed-group order. See :doc:`sampling-negatives` before
attaching training negatives.

Caps and normalization
------------------------

``max_edges`` raises if the induced edge set exceeds the cap. The method does
not truncate the edge set or silently redraw nodes. Relation/time filters restrict
eligible edges before this check; they do not change uniform node selection.

``normalize=True`` attaches reciprocal inclusion-probability weights, not model
confidence or logical probabilities. For an induced draw of ``k`` out of ``N``
nodes, node weights are ``N/k`` and non-loop edge weights are
``N*(N-1)/(k*(k-1))``; self-loops carry the node weight. Training must explicitly
use these weights. They are absent by default. Partition-block normalization uses
its own selection probability; see :ref:`sampling-induced-reference` for exact
contracts and :doc:`performance` for memory qualifications.
