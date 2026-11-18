"""Prerequisite selection for authenticated native assignments.

``catalog`` validates and freezes operator declarations. ``workflow`` binds a
selection to authenticated Gateway delivery and the native child lifecycle.
"""

from . import catalog, workflow

__all__ = ["catalog", "workflow"]
