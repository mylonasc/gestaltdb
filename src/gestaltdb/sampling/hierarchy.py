"""Compatibility import for the shared hierarchy module.

``RelationHierarchy`` now lives in :mod:`gestaltdb.hierarchy` alongside
``LabelHierarchy``. This module keeps the historical
``gestaltdb.sampling.RelationHierarchy`` import path working.
"""

from ..hierarchy import RelationHierarchy

__all__ = ["RelationHierarchy"]
