Pinned rule catalog and generation binding
============================================================

``gestaltdb.reasoning.catalog`` pins an exact set of rule versions: each
``CatalogEntry`` binds a rule key to its content digest and ordinal, and the
``RuleCatalog`` digest binds the whole set with an indexed per-key lookup.
``pin_generation`` binds that catalog digest together with a program digest
and a fact horizon into one ``PinnedGeneration`` identity; ``verify`` fails
closed on any catalog drift or horizon movement instead of silently mixing
generations.

**Reference fragment:** ``digest``, ``program`` and ``horizon`` must come from
the caller's selected rule content and fact scope. These values do not
authenticate database input; see :doc:`reasoning-generation-identity` for the
source-specific bridges.

.. code-block:: python

   from gestaltdb.reasoning import RuleCatalog, pin_generation

   catalog = RuleCatalog.from_dict({"entries": [{"key": "reach", "digest": digest}]})
   pinned = pin_generation("gen-42", catalog, program.digest, horizon)
   pinned.verify(catalog, horizon)  # raises on drift

Catalogs are versioned: v1 ``key``/``digest`` dictionaries migrate to v2
(``migrate_dict``) with historical version IDs and digests passing through
byte-identical; unknown versions fail closed. ``rebind`` adopts an
equivalent catalog explicitly after metadata-only migration; content
changes fail and demand rematerialization. Historical pinned generations
stay readable across catalog versions.

Legacy compatibility
----------------------------------------

The stored-rule service (:doc:`stored-rules`) is not implemented through this
catalog API. ``catalog_legacy_parity_gates`` describes interval selection,
all-support justifications, limits, identity/provenance and publication as
distinct compatibility requirements. Constructing a catalog does not replace
the stored service, migrate its state or substitute bounded witnesses for its
maintenance supports. The catalog remains explicit binding metadata.
