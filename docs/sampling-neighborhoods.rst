Neighbors and message-passing layers
=====================================

Use a neighbor draw to inspect a local relationship set. Use layers when each hop
needs its own :term:`fanout`, direction or relation policy. Begin with
:doc:`sampling-introduction` if snapshots or compact IDs are unfamiliar.

A :term:`message-passing block` is a pair of input/neighbor node frames plus
edge positions for a model layer. It is not automatically a PyG or DGL object.
Sampling follows the requested graph direction; exported blocks send messages
**from sampled neighbors to layer inputs**, which can reverse stored edge direction.

Draw two layers and inspect their frames
-----------------------------------------

This standalone array example needs no database backend or serializer. The graph
is ``a -R-> b,c`` and ``b,c -S-> d``. Both first-hop edges are eligible, so fanout
two returns both. The second layer deduplicates the next frontier, not the edge
occurrences that delivered messages.

.. code-block:: python

   from tempfile import TemporaryDirectory
   from gestaltdb.sampling import (
       ExternalNeighborSamplingSpec, SamplerEngine, SamplerSnapshot,
   )

   with TemporaryDirectory() as root:
       snapshot = SamplerSnapshot.from_edge_arrays(
           f"{root}/snapshot", external_node_ids=["a", "b", "c", "d"],
           external_edge_ids=["ab", "ac", "bd", "cd"],
           external_relation_ids=["R", "S"],
           src_int=[0, 0, 1, 2], dst_int=[1, 2, 3, 3], rel_int=[0, 0, 1, 1],
           edge_weights=[1.0, 3.0, 1.0, 1.0],
       )
       engine = SamplerEngine(snapshot, seed=7)
       neighbors = engine.sample_neighbors_external(
           ["a"], fanout=2, relations=["R"], strategy="weighted",
       )
       assert set(neighbors.to_external(snapshot)["neighbor_nodes"]) == {"b", "c"}
       layers = engine.sample_layers_external(["a"], [
           ExternalNeighborSamplingSpec(2, direction="out", relations=["R"]),
           ExternalNeighborSamplingSpec(2, direction="out", relations=["S"]),
       ])
       blocks = layers.to_blocks_external(snapshot)
       first = blocks[0]
       message_pairs = {
           (first["src_nodes"][source], first["dst_nodes"][target])
           for source, target in first["edge_index"].T
       }
       assert message_pairs == {("b", "a"), ("c", "a")}
       assert set(blocks[1]["src_nodes"]) == {"d"}
       assert blocks[1]["edge_index"].shape == (2, 2)

Weights affect selection, not eligibility alone. ``strategy="weighted"`` must be
explicit; persisted weights do not silently change uniform draws. Weights must
be finite and non-negative. Zero-weight candidates are excluded and a nonempty
all-zero candidate set fails. With replacement, repeated edge occurrences are
preserved in the exported ``edge_index``.

Policies and framework boundaries
----------------------------------

- Use ``direction="in"`` for incoming relationships, ``"out"`` for outgoing, or
  ``"any"`` when both are intended. Relation filters
  apply before the draw, not after it.
- ``balanced=True`` assigns deterministic equal quotas across relations (or
  node types for node seeds). A small group returns fewer rows without
  replacement; unused quota is not borrowed from another group.
- ``to_blocks(snapshot)`` keeps compact IDs; ``to_blocks_external(snapshot)``
  maps node/edge/relation IDs without changing local ``edge_index`` positions.
  Each block has its own node frames, not a single global matrix index.
- ``to_pyg_blocks(snapshot)`` requires Torch and returns tensor dictionaries.
  Training, feature lookup and framework block construction remain external.

For live typed paths, see :ref:`sampling-live-traversal`. For relation/node-type
hierarchy expansion, see :ref:`sampling-relation-hierarchies` and
:ref:`sampling-node-hierarchies`. Exact policies, balanced quotas and export
metadata are documented in :doc:`typed-sampling`; RNG behavior is in
:doc:`sampling-consistency`.
