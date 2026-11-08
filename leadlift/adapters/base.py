"""LEADLIFT — Adapter base class for all inquiry intake adapters."""

from __future__ import annotations

from typing import Any

from leadlift.models import (
    CSVImportResult,
    Inquiry,
    CSVImportRowError,
)


class BaseAdapter:
    """Base class for all intake adapters.

    Every adapter should produce a consistent internal Inquiry representation
    and return structured results for the caller to decide what to do with.
    """

    def __init__(self, settings=None):
        self.settings = settings or {}

    async def process(self, *args, **kwargs) -> CSVImportResult:
        """Process a single intake and return a structured result."""
        raise NotImplementedError

    def _normalize_text(self, text: Any) -> str:
        """Normalize incoming text: strip, default to empty."""
        if text is None:
            return ""
        return text.strip()

    def _validate_inquiry(self, inquiry: Inquiry) -> List[dict]:
        """Validate an inquiry and return a list of error dicts."""
        errors: List[dict] = []

        # Original text must not be empty
        if not inquiry.original_text or not inquiry.original_text.strip():
            errors.append({
                "field": "original_text",
                "message": "Original inquiry text must not be empty",
                "severity": "error",
            })

        # At least some meaningful information must be present
        has_info = any([
            inquiry.customer_name,
            inquiry.organization,
            inquiry.requested_service,
            inquiry.original_text.strip(),
        ])
        if not has_info:
            errors.append({
                "field": "minimal_info",
                "message": "Inquiry must contain at least a customer name, organization, "
                           "or requested service description",
                "severity": "warning",
            })

        return errors