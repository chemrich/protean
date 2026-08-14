"""Backends: the only code here that knows both a ``Scene`` and a viewer.

``wiggles_em`` computes cryo-EM views and returns a
:class:`~wiggles_em.scene.Scene` — an ordered list of ops that names no viewer.
A backend lowers one onto a specific viewer. PyMOL's lives upstream in
``wiggles_em.backends.pymol``; Mol\\*'s lives here, because it is written
against protean's bridge vocabulary and nothing upstream should know that
vocabulary exists.

The two rules a backend follows are upstream's, and both are load-bearing:

**Refuse rather than approximate.** An op this viewer cannot honour raises
:class:`~wiggles_em.scene.Refused`. It is never skipped and never substituted.
A dropped op leaves a picture that looks fine and means something other than
what was asked for.

**Normalisation is the backend's.** Levels and domains arrive in the units the
data is in. Mol\\*'s ``uncertainty`` theme ramps over a fixed ``[0, 100]``, so
:class:`~protean_mcp.backends.molstar.MolstarBackend` maps a scene's explicit
domain onto that. No view converts anything.
"""

from .molstar import MolstarBackend, atoms_for

__all__ = ["MolstarBackend", "atoms_for"]
