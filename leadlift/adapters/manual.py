"""LEADLIFT — Manual entry adapter for inquiries."""

from __future__ import annotations

from datetime import datetime
from typing import Optional

from leadlift.models import Inquiry, InquirySource


class ManualEntryAdapter:
    """Adapter for creating inquiries manually through the UI."""

    @staticmethod
    def create(
        original_text: str,
        customer_name: Optional[str] = None,
        customer_email: Optional[str] = None,
        customer_phone: Optional[str] = None,
        organization: Optional[str] = None,
        requested_service: Optional[str] = None,
        budget_stated: Optional[str] = None,
        timeline_stated: Optional[str] = None,
        source_platform: Optional[str] = None,
        source_account: Optional[str] = None,
        source_conversation_id: Optional[str] = None,
        source_message_id: Optional[str] = None,
    ) -> Inquiry:
        """Create an inquiry from manual entry fields."""
        if not original_text or not original_text.strip():
            raise ValueError("original_text is required for manual entry")

        now = datetime.utcnow().isoformat() + "Z"

        return Inquiry(
            source=InquirySource.MANUAL,
            source_platform=source_platform,
            source_account=source_account,
            source_conversation_id=source_conversation_id,
            source_message_id=source_message_id,
            customer_name=customer_name.strip() if customer_name else None,
            customer_email=customer_email.strip() if customer_email else None,
            customer_phone=customer_phone.strip() if customer_phone else None,
            organization=organization.strip() if organization else None,
            requested_service=requested_service.strip() if requested_service else None,
            budget_stated=budget_stated.strip() if budget_stated else None,
            timeline_stated=timeline_stated.strip() if timeline_stated else None,
            original_text=original_text.strip(),
            received_at=now,
            first_observed_at=now,
        )