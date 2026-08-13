"""Scientific eval authoring built on top of an unmodified Dev Autopilot.

``dev-autopilot-evals`` is a companion distribution: it depends on
``dev-autopilot`` but never patches it. It prepares a workspace and project
charter, hands that charter to the public ``dev-autopilot project start``
command, and validates whatever eval pack the run produced.
"""

from __future__ import annotations

__version__ = "0.1.0"

__all__ = ["__version__"]
