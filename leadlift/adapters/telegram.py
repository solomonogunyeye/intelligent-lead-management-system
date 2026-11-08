"""LEADLIFT — Telegram Bot inbound intake adapter.

Phase 3F: Telegram Bot API inbound-message intake.

Security considerations:
- Webhook payloads validated for structure before processing
- Secret-token validation via X-Telegram-Bot-Api-Secret-Token header
- Constant-time comparison
- Fail-closed on missing security configuration
- Malformed payload handling
- Replay/duplicate protection via update_id and message_id
- No outbound messaging; read-only webhook processing
- Sensitive data never logged
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime, timezone
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


class TelegramMessageStatus(str, Enum):
    """Status of Telegram message processing."""
    RECEIVED = "received"
    EXTRACTED = "extracted"
    NO_INQUIRY = "no_inquiry"
    INSUFFICIENT = "insufficient"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"
    DUPLICATE = "duplicate"
    EDITED = "edited"
    CHANNEL_POST = "channel_post"
    CALLBACK_QUERY = "callback_query"
    MEMBERSHIP = "membership"
    SERVICE = "service"


@dataclass
class TelegramMessage:
    """Normalized Telegram inbound message."""
    message_id: str
    update_id: str
    chat_id: str
    chat_type: str
    sender_id: Optional[str]
    sender_username: Optional[str]
    sender_first_name: Optional[str]
    sender_last_name: Optional[str]
    timestamp: Optional[str]
    text: Optional[str]
    caption: Optional[str]
    message_type: str
    reply_to_message_id: Optional[str] = None
    is_forwarded: bool = False
    raw_payload: Optional[Dict[str, Any]] = None


@dataclass
class TelegramIntakeResult:
    """Result of Telegram inbound message intake."""
    success: bool
    status: str = "received"
    message_id: Optional[str] = None
    update_id: Optional[str] = None
    chat_id: Optional[str] = None
    chat_type: Optional[str] = None
    sender_id: Optional[str] = None
    sender_username: Optional[str] = None
    sender_name: Optional[str] = None
    extracted_inquiry: Optional[Inquiry] = None
    errors: List[dict] = field(default_factory=list)
    warnings: List[dict] = field(default_factory=list)
    is_duplicate: bool = False
    is_unsupported: bool = False
    is_invalid: bool = False
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


class TelegramIntakeAdapter(BaseAdapter):
    """Telegram Bot intake adapter for inbound messages via webhook.

    Features:
    - Secret-token webhook validation (X-Telegram-Bot-Api-Secret-Token header)
    - Constant-time comparison
    - Message type detection (text, photo, document, video, audio, voice, location, contact)
    - Edited message, channel post, callback query, membership, service message handling
    - Duplicate detection (update_id and message_id)
    - Review workflow integration
    - No outbound messaging; read-only webhook processing only
    """

    # Telegram message types supported for text inquiry extraction
    VALID_MESSAGE_TYPES = {"text"}
    # Media types that are unsupported for inquiry extraction (but may have captions)
    MEDIA_TYPES = {"photo", "document", "video", "audio", "voice", "animation", "sticker"}
    # Service/unsupported update types
    UNSUPPORTED_UPDATE_TYPES = {
        "channel_post", "edited_channel_post",
        "callback_query", "inline_query", "chosen_inline_result",
        "shipping_query", "pre_checkout_query", "poll", "poll_answer",
        "my_chat_member", "chat_member", "chat_join_request",
    }

    def __init__(self, settings=None):
        super().__init__(settings)
        self.settings = settings or {}
        self._seen_update_ids: Set[str] = set()
        self._seen_message_ids: Set[str] = set()
        self._repository: Optional[InquiryRepository] = None

    @property
    def repository(self) -> InquiryRepository:
        if self._repository is None:
            self._repository = InquiryRepository()
        return self._repository

    # ------------------------------------------------------------------
    # Webhook verification - Telegram uses secret token header
    # ------------------------------------------------------------------

    def verify_webhook(self, mode: str, token: str, challenge: str, app_secret: Optional[str] = None) -> Optional[str]:
        """Telegram does not use GET verification handshake.

        Returns None always (Telegram uses POST with secret token).
        """
        return None

    def validate_secret_token(self, provided_token: Optional[str], configured_secret: Optional[str]) -> bool:
        """Validate Telegram webhook secret token.

        Telegram sends the secret in X-Telegram-Bot-Api-Secret-Token header.
        Uses constant-time comparison. Fail-closed when config missing.
        """
        if not configured_secret:
            return False
        if not provided_token:
            return False
        return hmac.compare_digest(provided_token, configured_secret)

    # Alias for consistency with other adapters
    def validate_signature(self, payload_body: bytes, signature_header: Optional[str], app_secret: Optional[str]) -> bool:
        """Alias for validate_secret_token for API consistency.

        Note: Telegram does not use HMAC. This validates the secret token.
        """
        return self.validate_secret_token(signature_header, app_secret)

    # ------------------------------------------------------------------
    # Webhook payload processing
    # ------------------------------------------------------------------

    def process_webhook_payload(self, payload: Dict[str, Any]) -> TelegramIntakeResult:
        """Process a Telegram Bot API webhook update.

        Supports:
        - text messages
        - photo/document/video/audio/voice with captions
        - location and contact messages
        - Unsupported updates classified (edited, channel posts, callback queries, etc.)

        Returns a TelegramIntakeResult with status and optional extracted inquiry.
        """
        # Validate top-level structure
        if not isinstance(payload, dict):
            return TelegramIntakeResult(
                success=False,
                status=TelegramMessageStatus.INVALID.value,
                is_invalid=True,
                errors=[{"field": "webhook_payload", "message": "Webhook payload must be a JSON object"}],
            )

        update_id = payload.get("update_id")
        if update_id is None:
            return TelegramIntakeResult(
                success=False,
                status=TelegramMessageStatus.INVALID.value,
                is_invalid=True,
                errors=[{"field": "update_id", "message": "Webhook payload missing 'update_id'"}],
            )

        update_id_str = str(update_id)

        # Check for replay: same update_id processed before
        if update_id_str in self._seen_update_ids:
            return TelegramIntakeResult(
                success=True,
                status=TelegramMessageStatus.DUPLICATE.value,
                update_id=update_id_str,
                is_duplicate=True,
                errors=[],
                warnings=[{
                    "field": "duplicate_detection",
                    "message": f"Duplicate Telegram update_id replay: {update_id_str}",
                    "severity": "warning",
                }],
            )

        # Track this update_id
        self._seen_update_ids.add(update_id_str)

        # Determine update type
        # Check for unsupported update types first
        for utype in self.UNSUPPORTED_UPDATE_TYPES:
            if utype in payload and payload.get(utype) is not None:
                return self._handle_unsupported_update(utype, payload, update_id_str)

        # Check for message (regular incoming message)
        message = payload.get("message")
        if isinstance(message, dict):
            return self._process_message(message, update_id_str, is_edited=False)

        # Check for edited_message
        edited_message = payload.get("edited_message")
        if isinstance(edited_message, dict):
            return self._handle_edited_message(edited_message, update_id_str)

        # Check for channel_post
        channel_post = payload.get("channel_post")
        if isinstance(channel_post, dict):
            return self._handle_channel_post(channel_post, update_id_str)

        # Check for edited_channel_post
        edited_channel_post = payload.get("edited_channel_post")
        if isinstance(edited_channel_post, dict):
            return self._handle_channel_post(edited_channel_post, update_id_str, edited=True)

        # No recognized message type
        return TelegramIntakeResult(
            success=True,
            status=TelegramMessageStatus.RECEIVED.value,
            update_id=update_id_str,
            errors=[],
            warnings=[{
                "field": "update",
                "message": "No recognized message in update",
                "severity": "info",
            }],
        )

    def _process_message(self, message: Dict[str, Any], update_id: str, is_edited: bool = False) -> TelegramIntakeResult:
        """Process a regular Telegram message."""
        message_id = message.get("message_id")
        mid_str = str(message_id) if message_id is not None else ""

        chat = message.get("chat") or {}
        chat_id = str(chat.get("id")) if chat.get("id") is not None else ""
        chat_type = chat.get("type", "unknown")

        from_user = message.get("from") or {}
        sender_id = str(from_user.get("id")) if from_user.get("id") is not None else None
        sender_username = from_user.get("username")
        sender_first_name = from_user.get("first_name")
        sender_last_name = from_user.get("last_name")

        timestamp = message.get("date")
        ts_str = str(timestamp) if timestamp is not None else None

        # Determine message type
        message_type = self._extract_message_type(message)

        # Handle service messages
        if self._is_service_message(message):
            return TelegramIntakeResult(
                success=True,
                status=TelegramMessageStatus.SERVICE.value,
                message_id=mid_str,
                update_id=update_id,
                chat_id=chat_id,
                chat_type=chat_type,
                sender_id=sender_id,
                sender_username=sender_username,
                sender_name=self._build_sender_name(sender_first_name, sender_last_name, sender_username),
                is_unsupported=True,
                unsupported_reason="Service message",
                event_type="service",
            )

        # Unsupported media types
        if message_type in self.MEDIA_TYPES:
            caption = message.get("caption")
            # Check if there's a caption that could be text
            text = message.get("text")
            if not text and not caption:
                return TelegramIntakeResult(
                    success=True,
                    status=TelegramMessageStatus.UNSUPPORTED.value,
                    message_id=mid_str,
                    update_id=update_id,
                    chat_id=chat_id,
                    chat_type=chat_type,
                    sender_id=sender_id,
                    sender_username=sender_username,
                    sender_name=self._build_sender_name(sender_first_name, sender_last_name, sender_username),
                    is_unsupported=True,
                    unsupported_reason=f"Unsupported message type: {message_type}",
                    event_type="message",
                )
            # If there's a caption, still mark as unsupported (not a text inquiry)
            if message_type in self.MEDIA_TYPES and text is None:
                return TelegramIntakeResult(
                    success=True,
                    status=TelegramMessageStatus.UNSUPPORTED.value,
                    message_id=mid_str,
                    update_id=update_id,
                    chat_id=chat_id,
                    chat_type=chat_type,
                    sender_id=sender_id,
                    sender_username=sender_username,
                    sender_name=self._build_sender_name(sender_first_name, sender_last_name, sender_username),
                    is_unsupported=True,
                    unsupported_reason=f"Unsupported message type: {message_type}",
                    event_type="message",
                )

        # Location messages
        if message_type == "location":
            return TelegramIntakeResult(
                success=True,
                status=TelegramMessageStatus.UNSUPPORTED.value,
                message_id=mid_str,
                update_id=update_id,
                chat_id=chat_id,
                chat_type=chat_type,
                sender_id=sender_id,
                sender_username=sender_username,
                sender_name=self._build_sender_name(sender_first_name, sender_last_name, sender_username),
                is_unsupported=True,
                unsupported_reason="Location message",
                event_type="message",
            )

        # Contact messages
        if message_type == "contact":
            return TelegramIntakeResult(
                success=True,
                status=TelegramMessageStatus.UNSUPPORTED.value,
                message_id=mid_str,
                update_id=update_id,
                chat_id=chat_id,
                chat_type=chat_type,
                sender_id=sender_id,
                sender_username=sender_username,
                sender_name=self._build_sender_name(sender_first_name, sender_last_name, sender_username),
                is_unsupported=True,
                unsupported_reason="Contact message",
                event_type="message",
            )

        # Extract text
        text = message.get("text")
        caption = message.get("caption")

        # Use caption as text if no text but caption exists (for media with captions, we still mark unsupported)
        # For text messages, use text
        if not text or not (isinstance(text, str) and text.strip()):
            # Check if it's a media message with caption - already handled above
            if message_type not in self.VALID_MESSAGE_TYPES:
                # Already returned unsupported above
                pass
            return TelegramIntakeResult(
                success=False,
                status=TelegramMessageStatus.INVALID.value,
                message_id=mid_str,
                update_id=update_id,
                chat_id=chat_id,
                chat_type=chat_type,
                sender_id=sender_id,
                sender_username=sender_username,
                sender_name=self._build_sender_name(sender_first_name, sender_last_name, sender_username),
                is_invalid=True,
                errors=[{"field": "text", "message": "Message contains no usable text content"}],
            )

        # Check for replay: same message_id in same chat
        replay_key = f"{chat_id}:{mid_str}"
        if replay_key in self._seen_message_ids:
            return TelegramIntakeResult(
                success=True,
                status=TelegramMessageStatus.DUPLICATE.value,
                message_id=mid_str,
                update_id=update_id,
                chat_id=chat_id,
                chat_type=chat_type,
                sender_id=sender_id,
                sender_username=sender_username,
                sender_name=self._build_sender_name(sender_first_name, sender_last_name, sender_username),
                is_duplicate=True,
                errors=[],
                warnings=[{
                    "field": "duplicate_detection",
                    "message": f"Duplicate Telegram message replay: {mid_str}",
                    "severity": "warning",
                }],
            )

        # Track this message
        if mid_str:
            self._seen_message_ids.add(replay_key)

        # Duplicate check via repository (database-level) using message_id
        if mid_str and self._is_duplicate_message(mid_str, chat_id):
            return TelegramIntakeResult(
                success=True,
                status=TelegramMessageStatus.DUPLICATE.value,
                message_id=mid_str,
                update_id=update_id,
                chat_id=chat_id,
                chat_type=chat_type,
                sender_id=sender_id,
                sender_username=sender_username,
                sender_name=self._build_sender_name(sender_first_name, sender_last_name, sender_username),
                is_duplicate=True,
                errors=[],
                warnings=[{
                    "field": "duplicate_detection",
                    "message": f"Duplicate Telegram message_id: {mid_str}",
                    "severity": "warning",
                }],
            )

        # Map to inquiry
        inquiry = self._map_to_inquiry(
            message_id=mid_str,
            update_id=update_id,
            chat_id=chat_id,
            chat_type=chat_type,
            sender_id=sender_id,
            sender_username=sender_username,
            sender_first_name=sender_first_name,
            sender_last_name=sender_last_name,
            timestamp=ts_str,
            text=text.strip(),
            reply_to_message_id=self._extract_reply_to_message_id(message),
            is_forwarded=self._is_forwarded(message),
        )

        return TelegramIntakeResult(
            success=True,
            status=TelegramMessageStatus.EXTRACTED.value,
            message_id=mid_str,
            update_id=update_id,
            chat_id=chat_id,
            chat_type=chat_type,
            sender_id=sender_id,
            sender_username=sender_username,
            sender_name=self._build_sender_name(sender_first_name, sender_last_name, sender_username),
            extracted_inquiry=inquiry,
            warnings=[],
            event_type="message",
        )

    def _handle_edited_message(self, message: Dict[str, Any], update_id: str) -> TelegramIntakeResult:
        """Handle edited message update."""
        message_id = message.get("message_id")
        mid_str = str(message_id) if message_id is not None else ""
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id")) if chat.get("id") is not None else ""

        return TelegramIntakeResult(
            success=True,
            status=TelegramMessageStatus.EDITED.value,
            message_id=mid_str,
            update_id=update_id,
            chat_id=chat_id,
            chat_type=chat.get("type", "unknown"),
            sender_id=str((message.get("from") or {}).get("id")) if (message.get("from") or {}).get("id") is not None else None,
            is_unsupported=True,
            unsupported_reason="Edited message",
            event_type="edited_message",
        )

    def _handle_channel_post(self, message: Dict[str, Any], update_id: str, edited: bool = False) -> TelegramIntakeResult:
        """Handle channel post update."""
        message_id = message.get("message_id")
        mid_str = str(message_id) if message_id is not None else ""
        chat = message.get("chat") or {}
        chat_id = str(chat.get("id")) if chat.get("id") is not None else ""

        return TelegramIntakeResult(
            success=True,
            status=TelegramMessageStatus.CHANNEL_POST.value,
            message_id=mid_str,
            update_id=update_id,
            chat_id=chat_id,
            chat_type=chat.get("type", "channel"),
            is_unsupported=True,
            unsupported_reason="Channel post" + (" (edited)" if edited else ""),
            event_type="channel_post" if not edited else "edited_channel_post",
        )

    def _handle_unsupported_update(self, utype: str, payload: Dict[str, Any], update_id: str) -> TelegramIntakeResult:
        """Handle unsupported update types."""
        status_map = {
            "callback_query": TelegramMessageStatus.CALLBACK_QUERY,
            "my_chat_member": TelegramMessageStatus.MEMBERSHIP,
            "chat_member": TelegramMessageStatus.MEMBERSHIP,
            "chat_join_request": TelegramMessageStatus.MEMBERSHIP,
        }
        status = status_map.get(utype, TelegramMessageStatus.UNSUPPORTED)

        # Extract chat_id if available
        chat_id = None
        for key in ("callback_query", "my_chat_member", "chat_member", "chat_join_request"):
            if key in payload and isinstance(payload.get(key), dict):
                obj = payload[key]
                chat = obj.get("chat") or obj.get("message", {}).get("chat") or {}
                if isinstance(chat, dict):
                    chat_id = str(chat.get("id")) if chat.get("id") is not None else None
                break

        return TelegramIntakeResult(
            success=True,
            status=status.value,
            update_id=update_id,
            chat_id=chat_id,
            is_unsupported=True,
            unsupported_reason=f"Unsupported update type: {utype}",
            event_type=utype,
        )

    # ------------------------------------------------------------------
    # Field extraction helpers
    # ------------------------------------------------------------------

    def _extract_message_type(self, message: Dict[str, Any]) -> str:
        """Determine Telegram message type from provider payload."""
        if "text" in message and isinstance(message.get("text"), str):
            return "text"
        if "photo" in message and isinstance(message.get("photo"), list):
            return "photo"
        if "document" in message and isinstance(message.get("document"), dict):
            return "document"
        if "video" in message and isinstance(message.get("video"), dict):
            return "video"
        if "audio" in message and isinstance(message.get("audio"), dict):
            return "audio"
        if "voice" in message and isinstance(message.get("voice"), dict):
            return "voice"
        if "animation" in message and isinstance(message.get("animation"), dict):
            return "animation"
        if "sticker" in message and isinstance(message.get("sticker"), dict):
            return "sticker"
        if "location" in message and isinstance(message.get("location"), dict):
            return "location"
        if "contact" in message and isinstance(message.get("contact"), dict):
            return "contact"
        if "caption" in message and isinstance(message.get("caption"), str):
            return "photo"  # caption implies media
        return "text"

    def _is_service_message(self, message: Dict[str, Any]) -> bool:
        """Check if message is a service message (new member, left member, etc.)."""
        service_keys = {
            "new_chat_members", "left_chat_member", "new_chat_title",
            "new_chat_photo", "delete_chat_photo", "group_chat_created",
            "supergroup_chat_created", "channel_chat_created",
            "migrate_to_chat_id", "migrate_from_chat_id", "pinned_message",
        }
        return bool(service_keys & set(message.keys()))

    def _build_sender_name(self, first_name: Optional[str], last_name: Optional[str], username: Optional[str]) -> Optional[str]:
        """Build sender display name from available info."""
        parts = []
        if first_name:
            parts.append(first_name)
        if last_name:
            parts.append(last_name)
        if parts:
            return " ".join(parts)
        if username:
            return username
        return None

    def _extract_reply_to_message_id(self, message: Dict[str, Any]) -> Optional[str]:
        """Extract reply-to message ID if present."""
        reply = message.get("reply_to_message")
        if isinstance(reply, dict):
            mid = reply.get("message_id")
            if mid is not None:
                return str(mid)
        return None

    def _is_forwarded(self, message: Dict[str, Any]) -> bool:
        """Check if message is forwarded."""
        return "forward_from" in message or "forward_from_chat" in message or "forward_date" in message

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    def _is_duplicate_message(self, message_id: str, chat_id: str) -> bool:
        """Check if this Telegram message was seen before (database-level)."""
        if not message_id:
            return False
        duplicates = self.repository.find_duplicates(
            customer_email=None,
            text_fingerprint=None,
            conversation_id=chat_id,
            message_id=message_id,
        )
        return len(duplicates) > 0

    # ------------------------------------------------------------------
    # Inquiry mapping
    # ------------------------------------------------------------------

    def _map_to_inquiry(
        self,
        message_id: str,
        update_id: str,
        chat_id: str,
        chat_type: str,
        sender_id: Optional[str],
        sender_username: Optional[str],
        sender_first_name: Optional[str],
        sender_last_name: Optional[str],
        timestamp: Optional[str],
        text: str,
        reply_to_message_id: Optional[str] = None,
        is_forwarded: bool = False,
    ) -> Inquiry:
        """Map Telegram message to an Inquiry object."""
        received_at = timestamp if timestamp else datetime.now(timezone.utc).isoformat()

        sender_name = self._build_sender_name(sender_first_name, sender_last_name, sender_username)

        return Inquiry(
            source=InquirySource.TELEGRAM,
            source_platform="telegram",
            source_account=sender_id or chat_id,
            source_conversation_id=chat_id,
            source_message_id=message_id,
            customer_name=sender_name,
            original_text=text,
            received_at=received_at,
            first_observed_at=received_at,
            recommended_next_action="Manual review required for Telegram inquiries",
        )

    # ------------------------------------------------------------------
    # Review workflow - save confirmed inquiry
    # ------------------------------------------------------------------

    def save_confirmed_inquiry(self, inquiry: Inquiry) -> Inquiry:
        """Persist a confirmed inquiry to the database."""
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

    def process(self, payload: Dict[str, Any]) -> TelegramIntakeResult:
        """Synchronous entry point for webhook processing."""
        return self.process_webhook_payload(payload)
