"""LEADLIFT — Inquiry status management."""

from __future__ import annotations

from typing import List, Optional

from leadlift.models import InquiryStatus
from leadlift.repository import InquiryRepository


class StatusManager:
    """Manages inquiry status transitions with validation."""

    def __init__(self, repository: Optional[InquiryRepository] = None):
        self.repository = repository or InquiryRepository()

    @staticmethod
    def valid_transitions(from_status: InquiryStatus) -> List[InquiryStatus]:
        """Return the list of statuses that can be transitioned to from a given status."""
        transitions = InquiryStatus.valid_transitions()
        return transitions.get(from_status, [])

    @classmethod
    def can_transition(cls, from_status: InquiryStatus, to_status: InquiryStatus) -> bool:
        """Check if a status transition is valid."""
        if from_status == to_status:
            return True
        valid_targets = cls.valid_transitions(from_status)
        return to_status in valid_targets

    @classmethod
    def validate_transition(cls, from_status: InquiryStatus, to_status: InquiryStatus) -> Optional[str]:
        """Validate a transition. Returns None if valid, error message if not."""
        if from_status == to_status:
            return None

        if to_status not in cls.valid_transitions(from_status):
            return (f"Invalid transition from '{from_status.value}' to '{to_status.value}'. "
                    f"Valid transitions from '{from_status.value}': "
                    f"{', '.join(s.value for s in cls.valid_transitions(from_status)) or '(none)'}")

        return None

    def apply(self, inquiry_id: str, new_status: InquiryStatus, reason: Optional[str] = None) -> None:
        """Apply a status transition and record it in history."""
        from leadlift.repository import get_repository

        repo = self.repository
        inquiry = repo.get(inquiry_id)
        if inquiry is None:
            raise ValueError(f"Inquiry {inquiry_id} not found")

        current_status = inquiry.status
        error = self.validate_transition(current_status, new_status)
        if error:
            raise ValueError(error)

        repo.update_status(inquiry_id, new_status, reason)


def parse_status(status_str: str) -> InquiryStatus:
    """Parse a status string, handling case-insensitive matching."""
    for status in InquiryStatus:
        if status.value.lower() == status_str.strip().lower():
            return status
    raise ValueError(f"Unknown status: {status_str}")


def get_all_statuses() -> list[tuple[str, str]]:
    """Return all statuses as (value, label) pairs for UI use."""
    return [(s.value, s.value) for s in InquiryStatus]