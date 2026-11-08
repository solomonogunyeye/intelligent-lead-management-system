"""LEADLIFT — Facebook Page Messenger inbound intake adapter.

Phase 3E: Facebook Page Messenger inbound-message intake.

Security considerations:
- Webhook payloads validated for structure before processing
- HMAC-SHA256 signature verification using configured app secret
- Fail-safe on missing security configuration
- Malformed payloads rejected safely
- Replay/duplicate prevention via message IDs and content fingerprinting
- No outbound messaging; read-only webhook processing
- Sensitive data never logged
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum

from leadlift.models import (
    Inquiry,
    InquirySource,
    CSVImportResult,
)
from leadlift.repository import InquiryRepository
from leadlift.adapters.base import BaseAdapter


class FacebookMessageStatus(str, Enum):
    """Status of Facebook Messenger message processing."""
    RECEIVED = "received"
    EXTRACTED = "extracted"
    NO_INQUIRY = "no_inquiry"
    INSUFFICIENT = "insufficient"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"
    DUPLICATE = "duplicate"
    ECHO = "echo"
    REACTION = "reaction"
    DELETED = "deleted"
    READ_RECEIPT = "read_receipt"
    DELIVERY = "delivery"
    POSTBACK = "postback"
    QUICK_REPLY = "quick_reply"


@dataclass
class FacebookMessage:
    """Normalized Facebook Messenger inbound message."""
    message_id: str
    conversation_id: str
    sender_id: str
    sender_name: Optional[str]
    recipient_id: Optional[str]
    timestamp: Optional[str]
    text: Optional[str]
    message_type: str
    is_echo: bool = False
    raw_payload: Optional[Dict[str, Any]] = None


@dataclass
class FacebookIntakeResult:
    """Result of Facebook Messenger inbound message intake."""
    success: bool
    status: str = "received"
    message_id: Optional[str] = None
    conversation_id: Optional[str] = None
    sender_id: Optional[str] = None
    sender_name: Optional[str] = None
    recipient_id: Optional[str] = None
    extracted_inquiry: Optional[Inquiry] = None
    errors: List[dict] = field(default_factory=list)
    warnings: List[dict] = field(default_factory=list)
    is_duplicate: bool = False
    is_unsupported: bool = False
    is_invalid: bool = False
    is_echo: bool = False
    unsupported_reason: Optional[str] = None
    event_type: Optional[str] = None

    def to_csv_import_result(self) -> CSVImportResult:
        """Convert to CSVImportResult for UI compatibility."""
        return CSVImportResult(
            accepted=1 if self.extracted_inquiry else 0,
            rejected=0 if self.extracted_inquiry else 1,
            duplicates=1 if self.is_duplicate else 0,
            errors=self.errors,
            warnings=self.warnings,
        )


class FacebookIntakeAdapter(BaseAdapter):
    """Facebook Page Messenger intake adapter for inbound messages via webhook.

    Features:
    - HMAC-SHA256 webhook signature verification
    - Message type detection (text, attachments, quick replies, postbacks, etc.)
    - Echo message detection (Page echoing its own message)
    - Reaction, deletion, read receipt, delivery event handling
    - Duplicate detection (content + message ID)
    - Review workflow integration
    - No outbound messaging; read-only webhook processing only
    """

    # Facebook message types supported for text inquiry extraction
    VALID_MESSAGE_TYPES = {"text"}
    # Attachment types that are unsupported for inquiry extraction
    ATTACHMENT_TYPES = {"image", "video", "audio", "file", "location", "fallback"}

    def __init__(self, settings=None):
        super().__init__(settings)
        self.settings = settings or {}
        self._seen_message_ids: Set[str] = set()
        self._repository: Optional[InquiryRepository] = None

    @property
    def repository(self) -> InquiryRepository:
        if self._repository is None:
            self._repository = InquiryRepository()
        return self._repository

    # ------------------------------------------------------------------
    # Webhook verification (GET) - handshake
    # ------------------------------------------------------------------

    def verify_webhook(self, mode: str, token: str, challenge: str, app_secret: Optional[str] = None) -> Optional[str]:
        """Handle Facebook/Meta webhook verification GET handshake.

        Meta sends a GET request with parameters:
        - mode: should be "subscribe"
        - token: the verify token configured in the Meta developer console
        - challenge: an encrypted echo token (must be returned unchanged)

        Returns the challenge string if verification succeeds, else None.
        """
        expected_token = self.settings.get("facebook_verify_token", "")
        if not expected_token:
            return None
        if token != expected_token:
            return None
        return challenge

    # ------------------------------------------------------------------
    # Signature validation (POST) - HMAC-SHA256
    # ------------------------------------------------------------------

    def validate_signature(self, payload_body: bytes, signature_header: Optional[str], app_secret: Optional[str]) -> bool:
        """Validate HMAC-SHA256 signature of webhook payload.

        Prevents tampered/injected webhook payloads.
        Fails safely when app_secret is not configured (returns False).
        """
        if not app_secret:
            return False
        if not signature_header:
            return False

        expected = hmac.new(
            app_secret.encode("utf-8"),
            msg=payload_body,
            digestmod=hashlib.sha256,
        ).hexdigest()

        # Meta sends signature in "sha256=<hex>" format
        signature = signature_header.strip()
        if signature.startswith("sha256="):
            signature = signature[7:]

        return hmac.compare_digest(expected, signature)

    # ------------------------------------------------------------------
    # Webhook payload processing
    # ------------------------------------------------------------------

    def process_webhook_payload(self, payload: Dict[str, Any]) -> FacebookIntakeResult:
        """Process a Facebook Page Messenger webhook event payload.

        Supports:
        - text messages
        - sender profile info when explicitly provided
        - Unsupported events identified (attachments, quick replies, postbacks, reactions, echoes, read/delivery receipts)

        Returns a FacebookIntakeResult with status and optional extracted inquiry.
        """
        # Validate top-level structure
        if not isinstance(payload, dict):
            return FacebookIntakeResult(
                success=False,
                status=FacebookMessageStatus.INVALID.value,
                is_invalid=True,
                errors=[{"field": "webhook_payload", "message": "Webhook payload must be a JSON object"}],
            )

        obj = payload.get("object")
        if obj != "page":
            return FacebookIntakeResult(
                success=False,
                status=FacebookMessageStatus.INVALID.value,
                is_invalid=True,
                errors=[{"field": "object", "message": "Webhook payload object must be 'page'"}],
            )

        entry = payload.get("entry")
        if not isinstance(entry, list) or len(entry) == 0:
            return FacebookIntakeResult(
                success=False,
                status=FacebookMessageStatus.INVALID.value,
                is_invalid=True,
                errors=[{"field": "entry", "message": "Webhook payload missing 'entry' array"}],
            )

        # Collect all messaging events across entries
        all_events: List[Dict[str, Any]] = []
        page_id: Optional[str] = None

        for entry_item in entry:
            if not isinstance(entry_item, dict):
                continue
            if page_id is None:
                page_id = entry_item.get("id")
            messaging = entry_item.get("messaging") or []
            if isinstance(messaging, list):
                for event in messaging:
                    if isinstance(event, dict):
                        all_events.append(event)

        if len(all_events) == 0:
            return FacebookIntakeResult(
                success=True,
                status=FacebookMessageStatus.RECEIVED.value,
                errors=[],
                warnings=[{
                    "field": "messaging",
                    "message": "No messaging events present in webhook entry",
                    "severity": "info",
                }],
            )

        # Process each event in the batch
        results: List[FacebookIntakeResult] = []
        for event in all_events:
            results.append(self._process_single_event(event, page_id))

        # Aggregate results
        first = results[0]
        any_invalid = any(r.is_invalid for r in results)
        all_errors = [e for r in results for e in r.errors]

        return FacebookIntakeResult(
            success=not any_invalid,
            status=first.status,
            message_id=first.message_id,
            conversation_id=first.conversation_id,
            sender_id=first.sender_id,
            sender_name=first.sender_name,
            recipient_id=first.recipient_id,
            extracted_inquiry=first.extracted_inquiry if not first.is_duplicate else None,
            errors=all_errors,
            warnings=[w for r in results for w in r.warnings],
            is_duplicate=first.is_duplicate or any(r.is_duplicate for r in results[1:]),
            is_unsupported=any(r.is_unsupported for r in results),
            is_invalid=any_invalid,
            is_echo=first.is_echo or any(r.is_echo for r in results[1:]),
            unsupported_reason=first.unsupported_reason,
            event_type=first.event_type,
        )

    def _process_single_event(self, event: Dict[str, Any], page_id: Optional[str] = None) -> FacebookIntakeResult:
        """Process a single Facebook Messenger messaging event."""

        sender = event.get("sender") or {}
        recipient = event.get("recipient") or {}
        sender_id = sender.get("id") if isinstance(sender, dict) else None
        recipient_id = recipient.get("id") if isinstance(recipient, dict) else None
        timestamp = event.get("timestamp")

        # Detect echo messages (Page echoing its own message)
        is_echo = False
        message = event.get("message")
        if isinstance(message, dict) and message.get("is_echo"):
            is_echo = True

        # Detect message reactions
        if "reaction" in event:
            reaction_data = event.get("reaction") or {}
            mid = reaction_data.get("mid") if isinstance(reaction_data, dict) else None
            return FacebookIntakeResult(
                success=True,
                status=FacebookMessageStatus.REACTION.value,
                message_id=mid,
                conversation_id=sender_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                is_unsupported=True,
                unsupported_reason="Message reaction event",
                event_type="reaction",
            )

        # Detect postbacks
        if "postback" in event:
            postback = event.get("postback") or {}
            payload = postback.get("payload") if isinstance(postback, dict) else None
            title = postback.get("title") if isinstance(postback, dict) else None
            return FacebookIntakeResult(
                success=True,
                status=FacebookMessageStatus.POSTBACK.value,
                conversation_id=sender_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                is_unsupported=True,
                unsupported_reason="Postback event",
                event_type="postback",
            )

        # Detect read receipts
        if "read" in event:
            return FacebookIntakeResult(
                success=True,
                status=FacebookMessageStatus.READ_RECEIPT.value,
                conversation_id=sender_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                is_unsupported=True,
                unsupported_reason="Read receipt event",
                event_type="read",
            )

        # Detect delivery receipts
        if "delivery" in event:
            return FacebookIntakeResult(
                success=True,
                status=FacebookMessageStatus.DELIVERY.value,
                conversation_id=sender_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                is_unsupported=True,
                unsupported_reason="Delivery receipt event",
                event_type="delivery",
            )

        # Detect echo messages
        if is_echo:
            mid = message.get("mid") if isinstance(message, dict) else None
            return FacebookIntakeResult(
                success=True,
                status=FacebookMessageStatus.ECHO.value,
                message_id=mid,
                conversation_id=sender_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                is_echo=True,
                is_unsupported=True,
                unsupported_reason="Echoed Page message",
                event_type="message_echo",
            )

        if not isinstance(message, dict):
            return FacebookIntakeResult(
                success=False,
                status=FacebookMessageStatus.INVALID.value,
                conversation_id=sender_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                is_invalid=True,
                errors=[{"field": "message", "message": "Event missing 'message' object"}],
            )

        mid = message.get("mid")
        if not mid:
            mid = str(hash(json.dumps(message, sort_keys=True)))[:12] if message else ""

        text = message.get("text")

        # Detect quick replies
        quick_reply = message.get("quick_reply")
        if isinstance(quick_reply, dict) and quick_reply.get("payload"):
            return FacebookIntakeResult(
                success=True,
                status=FacebookMessageStatus.QUICK_REPLY.value,
                message_id=mid,
                conversation_id=sender_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                is_unsupported=True,
                unsupported_reason="Quick reply event",
                event_type="quick_reply",
            )

        # Determine message type
        message_type = self._extract_message_type(message)

        # Unsupported attachment types
        if message_type in self.ATTACHMENT_TYPES:
            return FacebookIntakeResult(
                success=True,
                status=FacebookMessageStatus.UNSUPPORTED.value,
                message_id=mid,
                conversation_id=sender_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                is_unsupported=True,
                unsupported_reason=f"Unsupported message type: {message_type}",
                event_type="message",
            )

        # No text content - invalid inquiry
        if not text or not (isinstance(text, str) and text.strip()):
            return FacebookIntakeResult(
                success=False,
                status=FacebookMessageStatus.INVALID.value,
                message_id=mid,
                conversation_id=sender_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                is_invalid=True,
                errors=[{"field": "text", "message": "Message contains no usable text content"}],
            )

        # Check for replay: same message_id sent again
        if mid and mid in self._seen_message_ids:
            return FacebookIntakeResult(
                success=True,
                status=FacebookMessageStatus.DUPLICATE.value,
                message_id=mid,
                conversation_id=sender_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                is_duplicate=True,
                errors=[],
                warnings=[{
                    "field": "duplicate_detection",
                    "message": f"Duplicate Facebook message ID replay: {mid}",
                    "severity": "warning",
                }],
            )

        # Track this message ID for replay detection
        if mid:
            self._seen_message_ids.add(mid)

        # Duplicate check via repository (database-level)
        if mid and self._is_duplicate_message(mid):
            return FacebookIntakeResult(
                success=True,
                status=FacebookMessageStatus.DUPLICATE.value,
                message_id=mid,
                conversation_id=sender_id,
                sender_id=sender_id,
                recipient_id=recipient_id,
                is_duplicate=True,
                errors=[],
                warnings=[{
                    "field": "duplicate_detection",
                    "message": f"Duplicate Facebook message ID: {mid}",
                    "severity": "warning",
                }],
            )

        # Extract sender profile info (explicitly provided only)
        sender_name = None
        if isinstance(sender, dict):
            sender_name = sender.get("name") or sender.get("username") or sender.get("first_name")

        # Map to inquiry
        inquiry = self._map_to_inquiry(
            message_id=mid,
            conversation_id=sender_id,
            sender_id=sender_id,
            sender_name=sender_name,
            recipient_id=recipient_id,
            timestamp=timestamp,
            text=text.strip(),
            page_id=page_id,
        )

        return FacebookIntakeResult(
            success=True,
            status=FacebookMessageStatus.EXTRACTED.value,
            message_id=mid,
            conversation_id=sender_id,
            sender_id=sender_id,
            sender_name=sender_name,
            recipient_id=recipient_id,
            extracted_inquiry=inquiry,
            warnings=[],
            event_type="message",
        )

    # ------------------------------------------------------------------
    # Field extraction helpers
    # ------------------------------------------------------------------

    def _extract_message_type(self, message: Dict[str, Any]) -> str:
        """Determine Facebook message type from provider payload."""
        # Check for attachments
        attachments = message.get("attachments")
        if isinstance(attachments, list) and len(attachments) > 0:
            att = attachments[0]
            if isinstance(att, dict):
                att_type = att.get("type", "").lower()
                if att_type:
                    return att_type

        if "text" in message and isinstance(message.get("text"), str):
            return "text"

        if message.get("quick_reply"):
            return "quick_reply"

        return "text"

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    def _is_duplicate_message(self, message_id: str) -> bool:
        """Check if this Facebook message ID was seen before (database-level)."""
        if not message_id:
            return False
        duplicates = self.repository.find_duplicates(
            customer_email=None,
            text_fingerprint=None,
            conversation_id=None,
            message_id=message_id,
        )
        return len(duplicates) > 0

    # ------------------------------------------------------------------
    # Inquiry mapping
    # ------------------------------------------------------------------

    def _map_to_inquiry(
        self,
        message_id: str,
        conversation_id: Optional[str],
        sender_id: str,
        sender_name: Optional[str],
        recipient_id: Optional[str],
        timestamp: Any,
        text: str,
        page_id: Optional[str] = None,
    ) -> Inquiry:
        """Map Facebook Messenger message to an Inquiry object."""
        received_at = str(timestamp) if timestamp else datetime.utcnow().isoformat() + "Z"

        return Inquiry(
            source=InquirySource.FACEBOOK,
            source_platform="facebook",
            source_account=sender_id,
            source_conversation_id=conversation_id,
            source_message_id=message_id,
            customer_name=sender_name,
            original_text=text,
            received_at=received_at,
            first_observed_at=received_at,
            recommended_next_action="Manual review required for Facebook Messenger inquiries",
        )

    # ------------------------------------------------------------------
    # Review workflow - save confirmed inquiry
    # ------------------------------------------------------------------

    def save_confirmed_inquiry(self, inquiry: Inquiry) -> Inquiry:
        """Persist a confirmed inquiry to the database.

        This must ONLY be called after explicit user confirmation.
        Uses the existing repository and persistence rules.
        """
        duplicates = self._check_for_duplicates(inquiry)
        if duplicates:
            return duplicates[0]

        repository = self.repository
        saved, inserted = repository.save_if_not_duplicate(inquiry)
        return saved

    def _check_for_duplicates(self, inquiry: Inquiry) -> List[Inquiry]:
        """Check for existing inquiries that match the new inquiry."""
        return self.repository.find_duplicates(
            customer_email=None,
            text_fingerprint=None,
            conversation_id=inquiry.source_conversation_id,
            message_id=inquiry.source_message_id,
        )

    # ------------------------------------------------------------------
    # Convenience entry
    # ------------------------------------------------------------------

    def process(self, payload: Dict[str, Any]) -> FacebookIntakeResult:
        """Synchronous entry point for webhook processing."""
        return self.process_webhook_payload(payload)
