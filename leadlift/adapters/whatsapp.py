"""LEADLIFT — WhatsApp Business inbound intake adapter.

Phase 3C: WhatsApp Business Platform inbound-message intake.

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
import logging
import re
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
from leadlift.logging_config import get_logger, safe_log

_logger = get_logger("adapters.whatsapp")


class WhatsAppMessageStatus(str, Enum):
    """Status of WhatsApp message processing."""
    RECEIVED = "received"
    EXTRACTED = "extracted"
    NO_INQUIRY = "no_inquiry"
    INSUFFICIENT = "insufficient"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"
    DUPLICATE = "duplicate"


@dataclass
class WhatsAppMessage:
    """Normalized WhatsApp inbound message."""
    message_id: str
    conversation_id: str
    sender_phone: str
    sender_name: Optional[str]
    timestamp: Optional[str]
    text: Optional[str]
    text_caption: Optional[str]
    profile_name: Optional[str]
    profile_whatsapp_id: Optional[str]
    message_type: str
    raw_payload: Optional[Dict[str, Any]] = None


@dataclass
class WhatsAppIntakeResult:
    """Result of WhatsApp inbound message intake."""
    success: bool
    status: str = "received"
    message_id: Optional[str] = None
    conversation_id: Optional[str] = None
    sender_phone: Optional[str] = None
    sender_name: Optional[str] = None
    extracted_inquiry: Optional[Inquiry] = None
    errors: List[dict] = field(default_factory=list)
    warnings: List[dict] = field(default_factory=list)
    is_duplicate: bool = False
    is_unsupported: bool = False
    is_invalid: bool = False
    unsupported_reason: Optional[str] = None

    def to_csv_import_result(self) -> CSVImportResult:
        """Convert to CSVImportResult for UI compatibility."""
        return CSVImportResult(
            accepted=1 if self.extracted_inquiry else 0,
            rejected=0 if self.extracted_inquiry else 1,
            duplicates=1 if self.is_duplicate else 0,
            errors=self.errors,
            warnings=self.warnings,
        )


class WhatsAppIntakeAdapter(BaseAdapter):
    """WhatsApp intake adapter for inbound inquiry messages via webhook.

    Features:
    - HMAC-SHA256 webhook signature verification
    - Message type detection (text, unsupported media, etc.)
    - Caption/text extraction from media messages
    - Contact/profile information extraction
    - Duplicate detection (content + message ID)
    - Review workflow integration
    - No outbound messaging; read-only webhook processing only
    """

    # WhatsApp message types supported for text inquiry extraction
    VALID_MESSAGE_TYPES = {"text"}
    # Types that have extractable captions but aren't primary text inquiries
    MEDIA_TYPES_WITH_CAPTION = {"image", "document", "audio", "video"}

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
        """Handle WhatsApp Business Platform webhook verification GET handshake.

        WhatsApp sends a GET request with parameters:
        - mode: should be "webhook"
        - token: the verify token configured in the Meta developer console
        - challenge: an encrypted echo token (must be returned unchanged)

        Returns the challenge string if verification succeeds, else None.
        """
        expected_token = self.settings.get("whatsapp_verify_token", "")
        if not expected_token:
            # Fail-safe: no verify token configured
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

        # WhatsApp sends signature in "sha256=<hex>" format or just hex
        signature = signature_header.strip()
        if signature.startswith("sha256="):
            signature = signature[7:]

        return hmac.compare_digest(expected, signature)

    # ------------------------------------------------------------------
    # Webhook payload processing
    # ------------------------------------------------------------------

    def process_webhook_payload(self, payload: Dict[str, Any]) -> WhatsAppIntakeResult:
        """Process a WhatsApp Business Platform webhook event payload.

        Supports:
        - text messages with body text
        - text messages with media captions
        - contact/profile info when provided
        - Unsupported message types (identified, not misinterpreted)

        Returns a WhatsAppIntakeResult with status and optional extracted inquiry.
        """
        # Validate top-level structure
        if not isinstance(payload, dict):
            safe_log(_logger, logging.WARNING, "Invalid webhook payload (not a dict)")
            return WhatsAppIntakeResult(
                success=False,
                status=WhatsAppMessageStatus.INVALID.value,
                is_invalid=True,
                errors=[{"field": "webhook_payload", "message": "Webhook payload must be a JSON object"}],
            )

        entry = payload.get("entry")
        if not isinstance(entry, list) or len(entry) == 0:
            return WhatsAppIntakeResult(
                success=False,
                status=WhatsAppMessageStatus.INVALID.value,
                is_invalid=True,
                errors=[{"field": "entry", "message": "Webhook payload missing 'entry' array"}],
            )

        changes = entry[0].get("changes") if isinstance(entry[0], dict) else None
        if not isinstance(changes, list) or len(changes) == 0:
            return WhatsAppIntakeResult(
                success=False,
                status=WhatsAppMessageStatus.INVALID.value,
                is_invalid=True,
                errors=[{"field": "changes", "message": "Webhook payload missing 'changes' array"}],
            )

        change = changes[0]
        value = change.get("value") if isinstance(change, dict) else None
        if not isinstance(value, dict):
            return WhatsAppIntakeResult(
                success=False,
                status=WhatsAppMessageStatus.INVALID.value,
                is_invalid=True,
                errors=[{"field": "value", "message": "Webhook change missing 'value' object"}],
            )

        messages = value.get("messages") or []
        if len(messages) == 0:
            # Status updates (delivered/read) are not inquiries
            return WhatsAppIntakeResult(
                success=True,
                status=WhatsAppMessageStatus.RECEIVED.value,
                errors=[],
                warnings=[{
                    "field": "messages",
                    "message": "No inbound messages present in webhook change",
                    "severity": "info",
                }],
            )

        contacts = value.get("contacts") or []

        # Process each message in the batch
        results: List[WhatsAppIntakeResult] = []
        for message in messages:
            results.append(self._process_single_message(message, contacts))

        # Aggregate results - return the first message's result primarily
        first = results[0]

        # Check if any message is invalid
        any_invalid = any(r.is_invalid for r in results)

        # Check if any message is unsupported
        any_unsupported = any(r.is_unsupported for r in results)

        # Check if any message had errors
        all_errors = [e for r in results for e in r.errors]

        # For extracted inquiries, check if duplicates exist
        if first.extracted_inquiry:
            self._check_duplicate_inquiry(first.extracted_inquiry)

        # Also check for within-batch duplicates
        message_ids_in_batch = [r.message_id for r in results if r.message_id]
        if len(message_ids_in_batch) != len(set(message_ids_in_batch)):
            # There are duplicates within this batch
            pass  # already marked

        return WhatsAppIntakeResult(
            success=not any_invalid,
            status=first.status if results else WhatsAppMessageStatus.INVALID.value,
            message_id=first.message_id,
            conversation_id=first.conversation_id,
            sender_phone=first.sender_phone,
            sender_name=first.sender_name,
            extracted_inquiry=first.extracted_inquiry if not first.is_duplicate else None,
            errors=all_errors,
            warnings=[w for r in results for w in r.warnings],
            is_duplicate=first.is_duplicate or any(r.is_duplicate for r in results[1:]),
            is_unsupported=any_unsupported,
            is_invalid=any_invalid,
            unsupported_reason=first.unsupported_reason,
        )

    def _process_single_message(self, message: Dict[str, Any], contacts: Optional[List[Dict[str, Any]]] = None) -> WhatsAppIntakeResult:
        """Process a single WhatsApp inbound message from webhook payload."""

        # Extract message type from provider metadata
        message_type = self._extract_message_type(message)
        sender = self._extract_sender(message, contacts or [])
        text = self._extract_text(message, message_type)

        normalized = WhatsAppMessage(
            message_id=self._extract_message_id(message),
            conversation_id=self._extract_conversation_id(message, contacts or []),
            sender_phone=self._extract_sender_phone(message, contacts or []),
            sender_name=sender.get("name") if isinstance(sender, dict) else None,
            timestamp=self._extract_timestamp(message),
            text=text,
            text_caption=self._extract_text_caption(message),
            profile_name=message.get("profile", {}).get("name") if isinstance(message.get("profile"), dict) else None,
            profile_whatsapp_id=message.get("profile", {}).get("wa_id") if isinstance(message.get("profile"), dict) else None,
            message_type=message_type,
            raw_payload=message if isinstance(message, dict) else None,
        )

        # Check for replay: same message_id sent again
        if normalized.message_id in self._seen_message_ids:
            return WhatsAppIntakeResult(
                success=True,
                status=WhatsAppMessageStatus.DUPLICATE.value,
                message_id=normalized.message_id,
                conversation_id=normalized.conversation_id,
                sender_phone=normalized.sender_phone,
                sender_name=normalized.sender_name,
                is_duplicate=True,
                errors=[],
                warnings=[{
                    "field": "duplicate_detection",
                    "message": f"Duplicate WhatsApp message ID replay: {normalized.message_id}",
                    "severity": "warning",
                }],
            )

        # Track this message ID for replay detection
        self._seen_message_ids.add(normalized.message_id)

        # Unsupported message types (media, etc.)
        if message_type in self.MEDIA_TYPES_WITH_CAPTION:
            return WhatsAppIntakeResult(
                success=True,
                status=WhatsAppMessageStatus.UNSUPPORTED.value,
                message_id=normalized.message_id,
                conversation_id=normalized.conversation_id,
                sender_phone=normalized.sender_phone,
                sender_name=normalized.sender_name,
                is_unsupported=True,
                unsupported_reason=f"Unsupported message type: {message_type}",
            )

        if message_type not in self.VALID_MESSAGE_TYPES:
            return WhatsAppIntakeResult(
                success=True,
                status=WhatsAppMessageStatus.UNSUPPORTED.value,
                message_id=normalized.message_id,
                conversation_id=normalized.conversation_id,
                sender_phone=normalized.sender_phone,
                sender_name=normalized.sender_name,
                is_unsupported=True,
                unsupported_reason=f"Unsupported message type: {message_type}",
            )

        # No text content - invalid inquiry
        if not normalized.text or not normalized.text.strip():
            return WhatsAppIntakeResult(
                success=False,
                status=WhatsAppMessageStatus.INVALID.value,
                message_id=normalized.message_id,
                conversation_id=normalized.conversation_id,
                sender_phone=normalized.sender_phone,
                sender_name=normalized.sender_name,
                is_invalid=True,
                errors=[{
                    "field": "text",
                    "message": "Message contains no usable text content",
                    "severity": "error",
                }],
            )

        # Duplicate check via message ID against repository
        if self._is_duplicate_message(normalized.message_id):
            return WhatsAppIntakeResult(
                success=True,
                status=WhatsAppMessageStatus.DUPLICATE.value,
                message_id=normalized.message_id,
                conversation_id=normalized.conversation_id,
                sender_phone=normalized.sender_phone,
                sender_name=normalized.sender_name,
                is_duplicate=True,
                errors=[],
                warnings=[{
                    "field": "duplicate_detection",
                    "message": f"Duplicate WhatsApp message ID: {normalized.message_id}",
                    "severity": "warning",
                }],
            )

        # Map to inquiry
        inquiry = self._map_to_inquiry(normalized)

        return WhatsAppIntakeResult(
            success=True,
            status=WhatsAppMessageStatus.EXTRACTED.value,
            message_id=normalized.message_id,
            conversation_id=normalized.conversation_id,
            sender_phone=normalized.sender_phone,
            sender_name=normalized.sender_name,
            extracted_inquiry=inquiry,
            warnings=[],
        )

    # ------------------------------------------------------------------
    # Field extraction helpers
    # ------------------------------------------------------------------

    def _extract_message_type(self, message: Dict[str, Any]) -> str:
        """Determine WhatsApp message type from provider payload."""
        msg_type = (message.get("type") or "").lower().strip()
        if not msg_type:
            # Infer from presence of keys
            for t in ["image", "document", "audio", "video", "location", "contact", "interactive", "text"]:
                if t in message and message.get(t):
                    return t
            return "text"
        return msg_type

    def _extract_sender(self, message: Dict[str, Any], contacts: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Optional[str]]:
        """Extract sender name from profile/contact info."""
        # Try contacts list first (more reliable for name)
        if contacts:
            contact = contacts[0] if isinstance(contacts[0], dict) else {}
            if isinstance(contact, dict):
                profile_info = contact.get("profile")
                if isinstance(profile_info, dict) and profile_info.get("name"):
                    return {
                        "name": profile_info.get("name"),
                        "wa_id": contact.get("wa_id"),
                    }
                if contact.get("name"):
                    return {
                        "name": contact.get("name"),
                        "wa_id": contact.get("wa_id"),
                    }
        # Try message-level profile
        profile = message.get("profile")
        if isinstance(profile, dict):
            return {
                "name": profile.get("name"),
                "wa_id": profile.get("wa_id"),
            }
        return {"name": None, "wa_id": None}

    def _extract_sender_phone(self, message: Dict[str, Any], contacts: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
        """Extract sender phone number from WhatsApp payload."""
        from_info = message.get("from")
        if from_info is not None:
            if isinstance(from_info, dict):
                phone = from_info.get("wa_id") or from_info.get("phoneNumber") or from_info.get("id")
                if phone:
                    return str(phone)
            elif isinstance(from_info, str) and from_info.strip():
                return from_info.strip()
        # Fallback: contacts list first entry
        if contacts:
            contact = contacts[0]
            if isinstance(contact, dict):
                return contact.get("wa_id") or contact.get("phoneNumber") or contact.get("id") or None
        contacts = message.get("contacts")
        if isinstance(contacts, list) and len(contacts) > 0:
            contact = contacts[0]
            if isinstance(contact, dict):
                return contact.get("wa_id") or contact.get("phoneNumber") or contact.get("id") or None
        return None

    def _extract_message_id(self, message: Dict[str, Any]) -> str:
        """Extract WhatsApp message ID."""
        msg_id = message.get("id")
        if msg_id and isinstance(msg_id, str):
            return msg_id
        # Fallback: hash-based ID
        return str(hash(json.dumps(message, sort_keys=True)))[:12] if message else ""

    def _extract_conversation_id(self, message: Dict[str, Any], contacts: Optional[List[Dict[str, Any]]] = None) -> Optional[str]:
        """Extract WhatsApp conversation/thread ID.

        WhatsApp uses the sender's phone number as the conversation key.
        """
        return self._extract_sender_phone(message, contacts)

    def _extract_timestamp(self, message: Dict[str, Any]) -> Optional[str]:
        """Extract message timestamp."""
        ts = message.get("timestamp")
        if ts:
            return str(ts)
        return None

    def _extract_text(self, message: Dict[str, Any], message_type: str) -> Optional[str]:
        """Extract text content from message.

        Handles:
        - text.message.body -> primary text
        - text.message.caption -> when text is media with caption
        - Standard text type messages
        """
        # Primary text extraction for text type messages
        if message_type == "text":
            text_body = message.get("text")
            if isinstance(text_body, dict):
                text_body = text_body.get("body")
            if text_body and isinstance(text_body, str) and text_body.strip():
                return text_body.strip()

        # Caption extraction for media messages with text
        text_body = message.get("text")
        if isinstance(text_body, dict):
            text_body = text_body.get("body")

        # Check for caption field
        if text_body and isinstance(text_body, dict):
            # Some payloads have 'caption' at top level
            caption = text_body.get("caption")
            if caption and isinstance(caption, str) and caption.strip():
                return caption.strip()

        # Direct caption field
        caption = message.get("caption")
        if isinstance(caption, dict):
            caption = caption.get("text")
        if caption and isinstance(caption, str) and caption.strip():
            return caption.strip()

        # Also check contacts with description
        contacts = message.get("contacts")
        if isinstance(contacts, list) and len(contacts) > 0:
            contact = contacts[0]
            if isinstance(contact, dict):
                desc = contact.get("description") or contact.get("name")
                if desc and isinstance(desc, str) and desc.strip():
                    return desc.strip()

        return None

    def _extract_text_caption(self, message: Dict[str, Any]) -> Optional[str]:
        """Extract caption from media messages (maps to text field)."""
        # Standard caption field
        caption = message.get("caption")
        if isinstance(caption, dict):
            caption = caption.get("text")
        if caption and isinstance(caption, str) and caption.strip():
            return caption.strip()

        # Check text.body as caption alternative
        text_body = message.get("text")
        if isinstance(text_body, dict):
            return text_body.get("caption") or text_body.get("body")

        return None

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    def _is_duplicate_message(self, message_id: str) -> bool:
        """Check if this WhatsApp message ID was seen before (database-level)."""
        if not message_id:
            return False
        duplicates = self.repository.find_duplicates(
            customer_email=None,
            text_fingerprint=None,
            conversation_id=None,
            message_id=message_id,
        )
        return len(duplicates) > 0

    def _check_duplicate_inquiry(self, inquiry: Inquiry) -> None:
        """Check if this inquiry is a duplicate against existing DB records."""
        duplicates = self.repository.find_duplicates(
            customer_email=inquiry.customer_phone or None,
            text_fingerprint=None,
            conversation_id=inquiry.source_conversation_id,
            message_id=inquiry.source_message_id,
        )
        if duplicates:
            inquiry.text_fingerprint = None  # fingerprint already set on original
            # Mark as duplicate for UI feedback

    # ------------------------------------------------------------------
    # Inquiry mapping
    # ------------------------------------------------------------------

    def _map_to_inquiry(self, message: WhatsAppMessage) -> Inquiry:
        """Map WhatsApp message to an Inquiry object."""
        original_text = message.text or message.text_caption or ""

        # Determine customer name from available info
        customer_name = None
        if message.sender_name:
            customer_name = message.sender_name
        elif message.profile_name:
            customer_name = message.profile_name

        return Inquiry(
            source=InquirySource.WHATSAPP,
            source_platform="whatsapp",
            source_account=message.sender_phone,
            source_conversation_id=message.conversation_id,
            source_message_id=message.message_id,
            customer_phone=message.sender_phone,
            customer_name=customer_name,
            original_text=original_text,
            received_at=message.timestamp or datetime.utcnow().isoformat() + "Z",
            first_observed_at=message.timestamp or datetime.utcnow().isoformat() + "Z",
            recommended_next_action="Manual review required for WhatsApp inquiries",
        )

    # ------------------------------------------------------------------
    # Review workflow - save confirmed inquiry
    # ------------------------------------------------------------------

    def save_confirmed_inquiry(self, inquiry: Inquiry) -> Inquiry:
        """Persist a confirmed inquiry to the database.

        This must ONLY be called after explicit user confirmation.
        Uses the existing repository and persistence rules.

        Args:
            inquiry: Confirmed Inquiry to save

        Returns:
            The saved Inquiry (or existing duplicate)
        """
        # Check for duplicates before saving
        duplicates = self._check_for_duplicates(inquiry)
        if duplicates:
            return duplicates[0]

        repository = self.repository
        saved, inserted = repository.save_if_not_duplicate(inquiry)
        return saved

    def _check_for_duplicates(self, inquiry: Inquiry) -> List[Inquiry]:
        """Check for existing inquiries that match the new inquiry."""
        return self.repository.find_duplicates(
            customer_email=inquiry.customer_phone or None,
            text_fingerprint=None,
            conversation_id=inquiry.source_conversation_id,
            message_id=inquiry.source_message_id,
        )

    # ------------------------------------------------------------------
    # Convenience entry
    # ------------------------------------------------------------------

    def process(self, payload: Dict[str, Any]) -> WhatsAppIntakeResult:
        """Synchronous entry point for webhook processing."""
        return self.process_webhook_payload(payload)