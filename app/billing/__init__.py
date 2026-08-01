"""AI-assisted billing domain.

The package deliberately separates:

* extraction (fake/Ollama now, Vertex later),
* deterministic calculations and validation,
* persistence/finalization side effects.

Every provider returns the same :class:`BillDraftData` contract.
"""

from app.billing.models import BillDraftData

__all__ = ["BillDraftData"]
