"""Scope binding for transaction and snapshot graphs.

``GraphDB.transaction()`` and ``GraphDB.current_read_view()`` both derive a
sibling graph bound to a different store. The rules for which state is shared
versus store-bound live here, in one place, so service extractions reuse the
same constructors instead of copying fields inline.

Shared with the parent (deliberate):

- database identity, manifest, store path, backend/serializer names
- temporal writer coordination (one database-wide writer guard via the engine)
- serializer instance and index *configuration* (copied as new sets)

Store-bound (never inherited from the live graph):

- the backend store / snapshot handle itself
- decoded-record and adjacency-count caches (fresh, never the live dicts)
- index metadata reloaded from the snapshot store, not the parent
- temporal/epistemic/rules engines rebound to the child's store

Callers own handle lifetimes (commit/rollback, snapshot context). These
helpers perform no I/O beyond reloading index metadata from the child's own
store, exactly as the previous inline code did.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ..graphdb import GraphDB


def configure_transaction_child(parent: GraphDB, child: GraphDB) -> None:
    """Apply the parent's sharable config to a fresh transaction graph.

    The caller creates ``child`` on the transaction store and owns
    commit/rollback. Engines are rebound to the transaction store with a
    fresh commit cache and the transaction-bound marker so nested implicit
    transactions stay disabled.
    """
    child.indexed_node_properties = set(parent.indexed_node_properties)
    child.indexed_edge_properties = set(parent.indexed_edge_properties)
    child._store_path = parent._store_path
    child._backend_name = parent._backend_name
    child._serializer_name = parent._serializer_name
    child._manifest = parent._manifest
    child._database_id = parent._database_id
    child._lock_manager = parent._lock_manager
    child._node_repository = None
    child.temporal = parent.temporal.spawn_transaction_scope(child.store)
    child.epistemic = parent.epistemic.spawn_transaction_scope(child.temporal)
    child.rules = parent.rules.spawn_transaction_scope(child.temporal)


def configure_snapshot_child(parent: GraphDB, child: GraphDB) -> None:
    """Rebind a shallow-copied graph to its snapshot store.

    The caller performs ``copy.copy(parent)`` and substitutes the snapshot
    store first. Index sets and constraints are reloaded from the snapshot
    store (never inherited from the live parent), and store-bound caches and
    engines are replaced with snapshot-bound ones.
    """
    from ..graphdb import (
        _EDGE_PROPERTY_INDEXES_METADATA_KEY,
        _NODE_PROPERTY_INDEXES_METADATA_KEY,
    )

    del parent  # ownership is explicit: only the snapshot-bound child is read.
    child.indexed_node_properties = set(
        child._load_property_index_metadata(_NODE_PROPERTY_INDEXES_METADATA_KEY)
    )
    child.indexed_edge_properties = set(
        child._load_property_index_metadata(_EDGE_PROPERTY_INDEXES_METADATA_KEY)
    )
    child.node_constraints = child._load_node_constraint_metadata()
    child._typed_adjacency_count_cache = {}
    child._node_repository = None
    child.temporal = child.temporal.rebound(
        child.store, store_path=child._store_path)
    child.epistemic = child.epistemic.rebound(child.temporal)
    child.rules = child.rules.rebound(child.temporal)
