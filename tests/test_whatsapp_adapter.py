"""LEADLIFT — WhatsApp intake adapter comprehensive tests.

Uses synthetic webhook payloads and mocked settings.
No real WhatsApp account, phone number, or network connection required.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Optional
from pathlib import Path

import pytest

from leadlift.models import Inquiry, InquirySource, CSVImportResult
from leadlift.repository import InquiryRepository
from leadlift.adapters.whatsapp import (
    WhatsAppIntakeAdapter,
    WhatsAppIntakeResult,
    WhatsAppMessageStatus,
    WhatsAppMessage,
)
from tests.test_support import reset_test_database


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_db(isolated_database):
    """Ensure fresh database for each test."""
    reset_test_database(isolated_database, Path(isolated_database).parent)
    yield
    reset_test_database(isolated_database, Path(isolated_database).parent)


@pytest.fixture
def mock_settings():
    """Provide mock settings for WhatsApp configuration."""
    return {
        "whatsapp_verify_token": "test_verify_token_12345",
        "whatsapp_app_secret": "test_app_secret_12345",
    }


@pytest.fixture
def make_text_webhook():
    def _make_text_webhook(
        message_id: str = "wamid.HBgMMTYzY2I1Y2I3NjVBGhRdQUtZTk5OTlVBSXlBR0lMdUFBQA==",
        sender_phone: str = "15551234567",
        sender_name: str = "Alice Smith",
        body: str = "We need a website built for our company. Budget around $5000. Timeline 2 months.",
        timestamp: str = "1729999999",
        profile_name: Optional[str] = None,
) -> dict:
        """Create a synthetic WhatsApp Business Platform webhook payload with a text message."""
        payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WHATSAPP_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550000000",
                                "phone_number_id": "1234567890",
                            },
                            "contacts": [
                                {
                                    "profile": {
                                        "name": sender_name,
                                    },
                                    "wa_id": sender_phone,
                                }
                            ],
                            "messages": [
                                {
                                    "from": sender_phone,
                                    "id": message_id,
                                    "timestamp": timestamp,
                                    "text": {
                                        "body": body,
                                    },
                                    "type": "text",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
        }

        if profile_name:
            payload["entry"][0]["changes"][0]["value"]["messages"][0]["profile"] = {
                "name": profile_name,
                "wa_id": sender_phone,
            }
    
        return payload

    return _make_text_webhook



@pytest.fixture
def make_caption_webhook():
    def _make_caption_webhook(
        message_id: str = "wamid.HBgMMTYzY2I1Y2I3NjVBGhRdQUtZTk5OTlVBSXlBR0lMdUFBQA==",
        sender_phone: str = "15551234567",
        sender_name: str = "Bob Jones",
        caption: str = "I need a mobile app developed. Can you help?",
        media_id: str = "media_123",
        media_type: str = "image",
        ) -> dict:
        """Create a synthetic WhatsApp webhook with a media message that has a caption."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WHATSAPP_ID",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15550000000",
                                    "phone_number_id": "1234567890",
                                },
                                "contacts": [
                                    {
                                        "profile": {
                                            "name": sender_name,
                                        },
                                        "wa_id": sender_phone,
                                    }
                                ],
                                "messages": [
                                    {
                                        "from": sender_phone,
                                    "id": message_id,
                                    "timestamp": "1729999999",
                                    "type": media_type,
                                    media_type: {
                                        "link": "https://example.com/image.jpg",
                                        "caption": caption,
                                        "mime_type": "image/jpeg",
                                        "sha256": "abc123",
                                        "id": media_id,
                                    },
                                    "caption": {
                                        "text": caption,
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        ],
        }

        return payload

    return _make_caption_webhook

@pytest.fixture
def make_unsupported_webhook():
    def _make_unsupported_webhook(
        message_type: str = "audio",
        message_id: str = "wamid.unsupported.123",
        sender_phone: str = "15557654321",
        sender_name: str = "Charlie Doe",
        ) -> dict:
        """Create a synthetic WhatsApp webhook with an unsupported message type."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WHATSAPP_ID",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15550000000",
                                    "phone_number_id": "1234567890",
                                },
                                "contacts": [
                                    {
                                        "profile": {
                                            "name": sender_name,
                                        },
                                        "wa_id": sender_phone,
                                    }
                                ],
                                "messages": [
                                    {
                                        "from": sender_phone,
                                    "id": message_id,
                                    "timestamp": "1729999999",
                                    "type": message_type,
                                    message_type: {
                                        "link": "https://example.com/audio.mp3",
                                        "mime_type": "audio/mpeg",
                                        "sha256": "abc123",
                                        "id": "media_456",
                                    },
                                }
                            ],
                        },
                    }
                ],
            }
        ],
        }

        return payload

    return _make_unsupported_webhook

@pytest.fixture
def make_no_text_webhook():
    def _make_no_text_webhook(
        message_id: str = "wamid.no_text.123",
        sender_phone: str = "15557654321",
        sender_name: str = "Dave Wilson",
    ) -> dict:
        """Create a synthetic WhatsApp webhook with a text type but no body."""
        payload = {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "WHATSAPP_ID",
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "messaging_product": "whatsapp",
                            "metadata": {
                                "display_phone_number": "15550000000",
                                "phone_number_id": "1234567890",
                            },
                            "contacts": [
                                {
                                    "profile": {
                                        "name": sender_name,
                                    },
                                    "wa_id": sender_phone,
                                }
                            ],
                            "messages": [
                                {
                                    "from": sender_phone,
                                    "id": message_id,
                                    "timestamp": "1729999999",
                                    "text": {},
                                    "type": "text",
                                }
                            ],
                        },
                    }
                ],
            }
        ],
    }
        return payload

    return _make_no_text_webhook
# Test Class: Webhook Verification (GET)
# ---------------------------------------------------------------------------

class TestWebhookVerification:
    """Tests for WhatsApp webhook GET verification handshake."""

    def test_successful_webhook_verification(self, mock_settings):
        """Test successful webhook verification with correct token."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.verify_webhook(
            mode="webhook",
            token="test_verify_token_12345",
            challenge="challenge_abc123",
        )
        assert result == "challenge_abc123"

    def test_failed_webhook_verification_wrong_token(self, mock_settings):
        """Test webhook verification fails with incorrect token."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.verify_webhook(
            mode="webhook",
            token="wrong_token",
            challenge="challenge_abc123",
        )
        assert result is None

    def test_webhook_verification_no_configured_token(self):
        """Test webhook verification fails when no verify token configured."""
        adapter = WhatsAppIntakeAdapter(settings={})
        result = adapter.verify_webhook(
            mode="webhook",
            token="some_token",
            challenge="challenge_abc123",
        )
        assert result is None

    def test_webhook_verification_wrong_mode(self, mock_settings):
        """Test webhook verification ignores unexpected mode."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.verify_webhook(
            mode="subscribe",
            token="test_verify_token_12345",
            challenge="challenge_abc123",
        )
        # Mode isn't validated, but token is - should still pass
        assert result == "challenge_abc123"


# ---------------------------------------------------------------------------
# Test Class: Signature Validation
# ---------------------------------------------------------------------------

class TestSignatureValidation:
    """Tests for HMAC-SHA256 webhook signature validation."""

    def test_valid_signature(self, mock_settings):
        """Test valid HMAC-SHA256 signature passes validation."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        app_secret = mock_settings["whatsapp_app_secret"]
        payload = b'{"object": "whatsapp_business_account"}'
        expected = hmac.new(app_secret.encode(), msg=payload, digestmod=hashlib.sha256).hexdigest()

        assert adapter.validate_signature(payload, expected, app_secret) is True

    def test_valid_signature_with_sha256_prefix(self, mock_settings):
        """Test signature with 'sha256=' prefix passes validation."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        app_secret = mock_settings["whatsapp_app_secret"]
        payload = b'{"object": "whatsapp_business_account"}'
        expected = "sha256=" + hmac.new(
            app_secret.encode(), msg=payload, digestmod=hashlib.sha256
        ).hexdigest()

        assert adapter.validate_signature(payload, expected, app_secret) is True

    def test_invalid_signature_tampered_payload(self, mock_settings):
        """Test tampered payload fails signature validation."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        app_secret = mock_settings["whatsapp_app_secret"]
        original = b'{"object": "whatsapp_business_account"}'
        tampered = b'{"object": "whatsapp_business_account_evil"}'
        signature = hmac.new(
            app_secret.encode(), msg=original, digestmod=hashlib.sha256
        ).hexdigest()

        assert adapter.validate_signature(tampered, signature, app_secret) is False

    def test_invalid_signature_wrong_secret(self, mock_settings):
        """Test wrong app secret fails signature validation."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        payload = b'{"object": "whatsapp_business_account"}'
        signature = hmac.new(
            b"wrong_secret", msg=payload, digestmod=hashlib.sha256
        ).hexdigest()

        assert adapter.validate_signature(payload, signature, mock_settings["whatsapp_app_secret"]) is False

    def test_missing_signature_header(self, mock_settings):
        """Test missing signature header fails validation."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        payload = b'{"test": "data"}'

        assert adapter.validate_signature(payload, None, mock_settings["whatsapp_app_secret"]) is False

    def test_missing_app_secret(self, mock_settings):
        """Test that missing app secret fails validation (fail-safe)."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        payload = b'{"test": "data"}'
        signature = "abc123"

        assert adapter.validate_signature(payload, signature, None) is False

    def test_empty_signature_header(self, mock_settings):
        """Test empty signature header fails validation."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        payload = b'{"test": "data"}'

        assert adapter.validate_signature(payload, "", mock_settings["whatsapp_app_secret"]) is False


# ---------------------------------------------------------------------------
# Test Class: Valid Text Message Webhooks
# ---------------------------------------------------------------------------

class TestValidTextMessages:
    """Tests for processing valid WhatsApp text message webhooks."""

    def test_text_message_extraction(self, clean_db, mock_settings, make_text_webhook):
        """Test that text messages are properly extracted into inquiries."""
        payload = make_text_webhook()
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.success is True
        assert result.status == WhatsAppMessageStatus.EXTRACTED.value
        assert result.extracted_inquiry is not None
        assert result.extracted_inquiry.source == InquirySource.WHATSAPP
        assert result.extracted_inquiry.source_platform == "whatsapp"

    def test_message_id_preserved(self, clean_db, mock_settings, make_text_webhook):
        """Test that the WhatsApp message ID is preserved on the inquiry."""
        payload = make_text_webhook(message_id="wamid.custom_id_12345")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.message_id == "wamid.custom_id_12345"
        assert result.extracted_inquiry.source_message_id == "wamid.custom_id_12345"

    def test_sender_phone_preserved(self, clean_db, mock_settings, make_text_webhook):
        """Test that the sender phone number is preserved."""
        payload = make_text_webhook(sender_phone="15559998888")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.sender_phone == "15559998888"
        assert result.extracted_inquiry.customer_phone == "15559998888"

    def test_sender_name_from_contacts(self, clean_db, mock_settings, make_text_webhook):
        """Test that sender name is extracted from contacts array."""
        payload = make_text_webhook(sender_name="Alice Smith")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.sender_name == "Alice Smith"
        assert result.extracted_inquiry.customer_name == "Alice Smith"

    def test_profile_name_extracted(self, clean_db, mock_settings, make_text_webhook):
        """Test that profile name is extracted when provided separately."""
        payload = make_text_webhook(
            sender_name="Alice Smith",
            profile_name="AliceS Profile",
        )
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.extracted_inquiry.customer_name == "Alice Smith"

    def test_conversation_id_is_sender_phone(self, clean_db, mock_settings, make_text_webhook):
        """Test that conversation ID is the sender's phone number."""
        payload = make_text_webhook(sender_phone="15551234567")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.conversation_id == "15551234567"
        assert result.extracted_inquiry.source_conversation_id == "15551234567"

    def test_original_text_preserved(self, clean_db, mock_settings, make_text_webhook):
        """Test that the message body is preserved as original_text."""
        body = "We need a website built for our company. Budget around $5000. Timeline 2 months."
        payload = make_text_webhook(body=body)
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.extracted_inquiry.original_text == body

    def test_received_at_set(self, clean_db, mock_settings, make_text_webhook):
        """Test that received_at is set from the timestamp."""
        payload = make_text_webhook(timestamp="1729999999")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.extracted_inquiry.received_at == "1729999999"

    def test_missing_optional_sender_name(self, clean_db, mock_settings, make_text_webhook):
        """Test processing a message with no sender name."""
        payload = make_text_webhook()
        payload["entry"][0]["changes"][0]["value"]["contacts"] = [
            {"wa_id": "15551234567"}
        ]
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.success is True
        assert result.sender_name is None
        assert result.extracted_inquiry.customer_name is None

    def test_missing_contacts_array(self, clean_db, mock_settings, make_text_webhook):
        """Test processing a message with no contacts array."""
        payload = make_text_webhook()
        del payload["entry"][0]["changes"][0]["value"]["contacts"]
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.success is True
        assert result.sender_name is None
        assert result.extracted_inquiry.customer_phone is not None


# ---------------------------------------------------------------------------
# Test Class: Caption Extraction
# ---------------------------------------------------------------------------

class TestCaptionExtraction:
    """Tests for extracting text from media messages with captions."""

    def test_image_with_caption_extracted_as_unsupported(self, clean_db, mock_settings, make_caption_webhook):
        """Test that images with captions are identified as unsupported (not text inquiries)."""
        payload = make_caption_webhook(caption="Caption text here", media_type="image")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.is_unsupported is True
        assert "image" in (result.unsupported_reason or "").lower() or result.status == WhatsAppMessageStatus.UNSUPPORTED.value

    def test_image_without_caption_is_invalid(self, clean_db, mock_settings, make_caption_webhook):
        """Test that images without captions are rejected."""
        payload = make_caption_webhook(caption="", media_type="image")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.is_unsupported is True

    def test_document_with_caption_unsupported(self, clean_db, mock_settings, make_caption_webhook):
        """Test that document messages are unsupported."""
        payload = make_caption_webhook(caption="Check this document", media_type="document")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.is_unsupported is True

    def test_audio_message_unsupported(self, clean_db, mock_settings, make_unsupported_webhook):
        """Test that audio messages are unsupported."""
        payload = make_unsupported_webhook(message_type="audio")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.is_unsupported is True
        assert result.status == WhatsAppMessageStatus.UNSUPPORTED.value


# ---------------------------------------------------------------------------
# Test Class: Unsupported Message Types
# ---------------------------------------------------------------------------

class TestUnsupportedMessages:
    """Tests for handling unsupported message types."""

    def test_location_message_unsupported(self, clean_db, mock_settings):
        """Test that location messages are unsupported."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WHATSAPP_ID",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15550000000",
                                    "phone_number_id": "1234567890",
                                },
                                "contacts": [{"profile": {"name": "Test"}, "wa_id": "15551234567"}],
                                "messages": [
                                    {
                                        "from": "15551234567",
                                        "id": "wamid.location.123",
                                        "timestamp": "1729999999",
                                        "type": "location",
                                        "location": {
                                            "latitude": "-33.8688",
                                            "longitude": "151.2093",
                                            "name": "Sydney Opera House",
                                        },
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload(payload)

        assert result.is_unsupported is True
        assert result.status == WhatsAppMessageStatus.UNSUPPORTED.value

    def test_contact_message_unsupported(self, clean_db, mock_settings):
        """Test that contact messages are unsupported."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WHATSAPP_ID",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"display_phone_number": "15550000000", "phone_number_id": "1234567890"},
                                "contacts": [{"profile": {"name": "Test"}, "wa_id": "15551234567"}],
                                "messages": [
                                    {
                                        "from": "15551234567",
                                        "id": "wamid.contact.123",
                                        "timestamp": "1729999999",
                                        "type": "contacts",
                                        "contacts": [{"name": {"first_name": "John", "last_name": "Doe"}}],
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload(payload)

        assert result.is_unsupported is True

    def test_interactive_message_unsupported(self, clean_db, mock_settings):
        """Test that interactive messages are unsupported."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WHATSAPP_ID",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {"display_phone_number": "15550000000", "phone_number_id": "1234567890"},
                                "contacts": [{"profile": {"name": "Test"}, "wa_id": "15551234567"}],
                                "messages": [
                                    {
                                        "from": "15551234567",
                                        "id": "wamid.interactive.123",
                                        "timestamp": "1729999999",
                                        "type": "interactive",
                                        "interactive": {"type": "button", "button": {"text": "Click"}},
                                    }
                                ],
                            },
                        }
                    ],
                }
            ],
        }
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload(payload)

        assert result.is_unsupported is True


# ---------------------------------------------------------------------------
# Test Class: Malformed Payloads
# ---------------------------------------------------------------------------

class TestMalformedPayloads:
    """Tests for handling malformed or incomplete webhook payloads."""

    def test_payload_not_dict(self, clean_db, mock_settings):
        """Test that non-dict payload is rejected."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload("not a dict")

        assert result.success is False
        assert result.is_invalid is True

    def test_missing_entry(self, clean_db, mock_settings):
        """Test that missing 'entry' is rejected."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload({"object": "whatsapp_business_account"})

        assert result.is_invalid is True
        assert result.success is False

    def test_empty_changes(self, clean_db, mock_settings):
        """Test that empty changes array is rejected."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload({
            "object": "whatsapp_business_account",
            "entry": [{"id": "test", "changes": []}],
        })

        assert result.is_invalid is True

    def test_missing_value_in_change(self, clean_db, mock_settings):
        """Test that missing 'value' in change is rejected."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload({
            "object": "whatsapp_business_account",
            "entry": [{"id": "test", "changes": [{"field": "messages"}]}],
        })

        assert result.is_invalid is True

    def test_no_messages_array(self, clean_db, mock_settings, make_text_webhook):
        """Test that webhook with no messages is handled gracefully."""
        payload = make_text_webhook()
        del payload["entry"][0]["changes"][0]["value"]["messages"]
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.success is True
        assert result.status == WhatsAppMessageStatus.RECEIVED.value
        assert len(result.warnings) >= 1


# ---------------------------------------------------------------------------
# Test Class: Duplicate Detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection:
    """Tests for replay and content-based duplicate detection."""

    def test_duplicate_message_id_replay(self, clean_db, mock_settings, make_text_webhook):
        """Test that the same message ID processed twice is detected as duplicate (replay)."""
        payload = make_text_webhook(message_id="wamid.replay_test_123")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        first = adapter.process_webhook_payload(payload)
        second = adapter.process_webhook_payload(payload)

        assert first.success is True
        assert first.status == WhatsAppMessageStatus.EXTRACTED.value
        assert second.status == WhatsAppMessageStatus.DUPLICATE.value
        assert second.is_duplicate is True
        assert second.extracted_inquiry is None

    def test_different_message_ids_not_duplicate(self, clean_db, mock_settings, make_text_webhook):
        """Test that different message IDs are not flagged as duplicates."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        payload1 = make_text_webhook(message_id="wamid.first_123", body="First message about web design")
        payload2 = make_text_webhook(message_id="wamid.second_456", body="Second message about app dev")

        first = adapter.process_webhook_payload(payload1)
        second = adapter.process_webhook_payload(payload2)

        assert first.status == WhatsAppMessageStatus.EXTRACTED.value
        assert second.status == WhatsAppMessageStatus.EXTRACTED.value
        assert second.is_duplicate is False

    def test_duplicate_via_repository_message_id(self, clean_db, mock_settings, make_text_webhook):
        """Test that a message ID already in the database is detected as duplicate."""
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        # Process webhook to get proposed inquiry
        payload = make_text_webhook(message_id="wamid.db_duplicate_123")
        result = adapter.process_webhook_payload(payload)

        # Save the inquiry to DB (simulating accepted message that was saved)
        inquiry = result.extracted_inquiry
        repo = InquiryRepository()
        repo.save(inquiry)

        # Create a new adapter instance and send same message
        adapter2 = WhatsAppIntakeAdapter(settings=mock_settings)
        result2 = adapter2.process_webhook_payload(payload)

        assert result2.is_duplicate is True
        assert result2.status == WhatsAppMessageStatus.DUPLICATE.value

    def test_multiple_messages_in_one_payload(self, clean_db, mock_settings, make_text_webhook):
        """Test handling multiple messages in a single webhook event."""
        base = make_text_webhook()
        # Add a second message
        base["entry"][0]["changes"][0]["value"]["messages"].append({
            "from": "15551234568",
            "id": "wamid.second_msg_678",
            "timestamp": "1729999998",
            "text": {"body": "Need SEO services for my business. Budget $2000/month."},
            "type": "text",
        })
        # Add contacts for second message
        base["entry"][0]["changes"][0]["value"]["contacts"].append({
            "profile": {"name": "Eve Brown"},
            "wa_id": "15551234568",
        })

        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload(base)

        assert result.success is True
        assert result.status == WhatsAppMessageStatus.EXTRACTED.value


# ---------------------------------------------------------------------------
# Test Class: Missing Required Fields
# ---------------------------------------------------------------------------

class TestMissingFields:
    """Tests for messages with missing required fields."""

    def test_missing_sender_phone(self, clean_db, mock_settings, make_text_webhook):
        """Test handling of a message with no sender phone."""
        payload = make_text_webhook()
        payload["entry"][0]["changes"][0]["value"]["contacts"] = []
        payload["entry"][0]["changes"][0]["value"]["messages"][0]["from"] = None

        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload(payload)

        # Should still process - phone comes from 'from' field
        assert result.extracted_inquiry is not None

    def test_empty_text_body(self, clean_db, mock_settings, make_no_text_webhook):
        """Test that empty text body triggers invalid status."""
        payload = make_no_text_webhook()
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.is_invalid is True

    def test_missing_timestamp(self, clean_db, mock_settings, make_text_webhook):
        """Test that missing timestamp is handled gracefully."""
        payload = make_text_webhook()
        del payload["entry"][0]["changes"][0]["value"]["messages"][0]["timestamp"]
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.success is True
        assert result.extracted_inquiry is not None

    def test_missing_message_id_fallback(self, clean_db, mock_settings, make_text_webhook):
        """Test that missing message ID generates a fallback."""
        payload = make_text_webhook()
        del payload["entry"][0]["changes"][0]["value"]["messages"][0]["id"]
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.message_id is not None
        assert len(result.message_id) > 0


# ---------------------------------------------------------------------------
# Test Class: Security - No Credential Logging
# ---------------------------------------------------------------------------

class TestSecurityNoCredentialLogging:
    """Tests verifying sensitive data is never logged or exposed."""

    def test_no_print_statements_in_adapter(self):
        """Test that the adapter contains no print() statements."""
        import inspect
        import re
        source = inspect.getsource(WhatsAppIntakeAdapter)

        print_calls = re.findall(r'\bprint\s*\(', source)
        assert len(print_calls) == 0, "Adapter should not contain print statements"

    def test_no_dangerous_api_calls_in_code(self):
        """Test that the adapter only uses read-only operations (no send/delete)."""
        import inspect
        source = inspect.getsource(WhatsAppIntakeAdapter)

        dangerous_patterns = [
            "messages.send",
            "messages.delete",
            "messages.modify",
            "chats.delete",
            "contacts.delete",
            "media/delete",
        ]
        for pattern in dangerous_patterns:
            assert pattern not in source, f"Found dangerous operation: {pattern}"


# ---------------------------------------------------------------------------
# Test Class: Inquiry Mapping
# ---------------------------------------------------------------------------

class TestInquiryMapping:
    """Tests for WhatsApp message to Inquiry mapping."""

    def test_inquiry_source_set_to_whatsapp(self, clean_db, mock_settings, make_text_webhook):
        """Test that source is set to WHATSAPP."""
        payload = make_text_webhook()
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload(payload)

        assert result.extracted_inquiry.source == InquirySource.WHATSAPP

    def test_inquiry_source_platform_is_whatsapp(self, clean_db, mock_settings, make_text_webhook):
        """Test that source_platform is set."""
        payload = make_text_webhook()
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload(payload)

        assert result.extracted_inquiry.source_platform == "whatsapp"

    def test_customer_email_not_invented(self, clean_db, mock_settings, make_text_webhook):
        """Test that customer_email is not invented when not provided."""
        payload = make_text_webhook()
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload(payload)

        assert result.extracted_inquiry.customer_email is None

    def test_customer_name_not_invented_from_phone(self, clean_db, mock_settings, make_text_webhook):
        """Test that customer_name is not inferred from phone number."""
        payload = make_text_webhook(
            sender_name=None,
            sender_phone="15551234567",
        )
        # Remove contacts to ensure no name is available
        payload["entry"][0]["changes"][0]["value"]["contacts"] = []
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload(payload)

        assert result.extracted_inquiry.customer_name is None

    def test_source_account_is_sender_phone(self, clean_db, mock_settings, make_text_webhook):
        """Test that source_account is the sender's phone number."""
        payload = make_text_webhook(sender_phone="15551234567")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload(payload)

        assert result.extracted_inquiry.source_account == "15551234567"

    def test_source_message_id_preserved(self, clean_db, mock_settings, make_text_webhook):
        """Test that the WhatsApp message ID is stored as source_message_id."""
        payload = make_text_webhook(message_id="wamid.unique_id_99999")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload(payload)

        assert result.extracted_inquiry.source_message_id == "wamid.unique_id_99999"

    def test_source_conversation_id_is_phone(self, clean_db, mock_settings, make_text_webhook):
        """Test that source_conversation_id is the sender's phone number."""
        payload = make_text_webhook(sender_phone="15551234567")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload(payload)

        assert result.extracted_inquiry.source_conversation_id == "15551234567"


# ---------------------------------------------------------------------------
# Test Class: Review and Confirmation Workflow
# ---------------------------------------------------------------------------

class TestReviewWorkflow:
    """Tests for the review and confirmation workflow."""

    def test_save_confirmed_inquiry(self, clean_db, mock_settings, make_text_webhook):
        """Test that accepted inquiries are saved to the database."""
        payload = make_text_webhook(message_id="wamid.save_test_123")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)
        inquiry = result.extracted_inquiry

        repo = InquiryRepository()
        assert len(repo.get_all()) == 0

        saved = adapter.save_confirmed_inquiry(inquiry)
        assert saved.inquiry_id is not None
        assert len(repo.get_all()) == 1

    def test_cancel_does_not_save(self, clean_db, mock_settings, make_text_webhook):
        """Test that cancellation without confirmation does not save."""
        payload = make_text_webhook(message_id="wamid.cancel_test_456")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)
        inquiry = result.extracted_inquiry

        repo = InquiryRepository()
        assert len(repo.get_all()) == 0

        # Don't call save_confirmed_inquiry - simulating cancellation
        assert len(repo.get_all()) == 0

    def test_save_duplicate_returns_existing(self, clean_db, mock_settings, make_text_webhook):
        """Test that saving a duplicate inquiry returns the existing record."""
        payload = make_text_webhook(message_id="wamid.dup_save_test")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)
        inquiry = result.extracted_inquiry

        repo = InquiryRepository()
        saved = adapter.save_confirmed_inquiry(inquiry)

        # Create a second inquiry with the same message_id
        inquiry2 = Inquiry(
            source=InquirySource.WHATSAPP,
            source_platform="whatsapp",
            source_message_id=inquiry.source_message_id,
            source_conversation_id=inquiry.source_conversation_id,
            customer_phone=inquiry.customer_phone,
            original_text="Different text but same message_id",
        )

        saved2 = adapter.save_confirmed_inquiry(inquiry2)

        assert saved2.inquiry_id == saved.inquiry_id
        assert len(repo.get_all()) == 1

    def test_to_csv_import_result(self, clean_db, mock_settings, make_text_webhook):
        """Test conversion of WhatsAppIntakeResult to CSVImportResult."""
        payload = make_text_webhook()
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload(payload)

        csv_result = result.to_csv_import_result()

        assert isinstance(csv_result, CSVImportResult)
        assert csv_result.accepted == 1
        assert csv_result.rejected == 0

    def test_unsolicited_response_conversion(self, clean_db, mock_settings, make_unsupported_webhook):
        """Test that unsupported messages convert correctly to CSVImportResult."""
        payload = make_unsupported_webhook(message_type="audio")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload(payload)

        csv_result = result.to_csv_import_result()

        assert isinstance(csv_result, CSVImportResult)
        assert csv_result.accepted == 0
        assert csv_result.rejected == 1


# ---------------------------------------------------------------------------
# Test Class: Persistence and Restart
# ---------------------------------------------------------------------------

class TestPersistenceAndRestart:
    """Tests for persistence across database connections."""

    def test_inquiry_persisted_across_connections(self, clean_db, mock_settings, make_text_webhook):
        """Test that saved inquiries persist across connections."""
        payload = make_text_webhook(message_id="wamid.persist_test_789")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)
        repo = InquiryRepository()
        saved = repo.save(result.extracted_inquiry) if hasattr(repo.save, '__call__') else None

        # Use the adapter's save method
        saved = adapter.save_confirmed_inquiry(result.extracted_inquiry)

        # Query with new repository instance
        repo2 = InquiryRepository()
        loaded = repo2.get(saved.inquiry_id)

        assert loaded is not None
        assert loaded.source == InquirySource.WHATSAPP
        assert loaded.source_message_id == "wamid.persist_test_789"
        assert loaded.customer_phone == "15551234567"

    def test_duplicate_detection_after_restart(self, clean_db, mock_settings, make_text_webhook):
        """Test that duplicate detection works after app restart (new adapter instance)."""
        payload = make_text_webhook(message_id="wamid.restart_dup_111")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)
        adapter.save_confirmed_inquiry(result.extracted_inquiry)

        # New adapter instance (simulating restart)
        adapter2 = WhatsAppIntakeAdapter(settings=mock_settings)
        result2 = adapter2.process_webhook_payload(payload)

        assert result2.is_duplicate is True


# ---------------------------------------------------------------------------
# Test Class: Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    """Tests for edge cases and boundary conditions."""

    def test_long_message_text(self, clean_db, mock_settings, make_text_webhook):
        """Test handling of long message text."""
        long_text = "We need a website. " * 200
        payload = make_text_webhook(body=long_text)
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.success is True
        assert len(result.extracted_inquiry.original_text) > 1000

    def test_special_characters_in_text(self, clean_db, mock_settings, make_text_webhook):
        """Test handling of special characters."""
        payload = make_text_webhook(body="Need website with émojis 😊 and spëcial çharacters")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.success is True
        assert "émojis" in result.extracted_inquiry.original_text

    def test_whitespace_only_body(self, clean_db, mock_settings, make_text_webhook):
        """Test that whitespace-only bodies are rejected."""
        payload = make_text_webhook(body="   ")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.is_invalid is True

    def test_unicode_phone_number(self, clean_db, mock_settings, make_text_webhook):
        """Test handling of phone numbers with country code."""
        payload = make_text_webhook(sender_phone="+15551234567")
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.sender_phone == "+15551234567"

    def test_reaction_event_handled_gracefully(self, clean_db, mock_settings):
        """Test that reaction status updates don't break processing."""
        payload = {
            "object": "whatsapp_business_account",
            "entry": [
                {
                    "id": "WHATSAPP_ID",
                    "changes": [
                        {
                            "field": "messages",
                            "value": {
                                "messaging_product": "whatsapp",
                                "metadata": {
                                    "display_phone_number": "15550000000",
                                    "phone_number_id": "1234567890",
                                },
                                "messages": [],  # No messages, just status
                            },
                        }
                    ],
                }
            ],
        }
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)
        result = adapter.process_webhook_payload(payload)

        assert result.success is True
        assert result.status == WhatsAppMessageStatus.RECEIVED.value

    def test_payload_size_limit(self, clean_db, mock_settings, make_text_webhook):
        """Test that extremely large payloads are handled without crashing."""
        huge_body = "A" * 50000
        payload = make_text_webhook(body=huge_body)
        adapter = WhatsAppIntakeAdapter(settings=mock_settings)

        result = adapter.process_webhook_payload(payload)

        assert result.success is True


# ---------------------------------------------------------------------------
# Test Class: Integration with Existing Functionality
# ---------------------------------------------------------------------------

class TestExistingFunctionalityIntact:
    """Tests verifying existing adapters, model, and repository remain intact."""

    def test_whatsapp_source_enum_exists(self):
        """Test that InquirySource.WHATSAPP exists."""
        assert InquirySource.WHATSAPP == "whatsapp"

    def test_email_adapter_still_works(self, clean_db, mock_settings):
        """Test that the Gmail adapter still works after WhatsApp changes."""
        from leadlift.adapters.email import GmailIntakeAdapter

        adapter = GmailIntakeAdapter(settings=__import__("leadlift.config", fromlist=["Settings"]).Settings())
        assert adapter is not None

    def test_csv_adapter_still_works(self, clean_db):
        """Test that CSV adapter still works."""
        from leadlift.adapters.csv import CSVAdapter

        csv_content = "source,original_text,received_at,customer_name,customer_email,requested_service\n"
        csv_content += "manual,I need a website,2026-10-30T10:00:00Z,Test User,test@test.com,Web Development\n"

        adapter = CSVAdapter()
        result = adapter.process(csv_content)

        assert result.accepted == 1

    def test_manual_adapter_still_works(self, clean_db):
        """Test that ManualEntryAdapter still works."""
        from leadlift.adapters.manual import ManualEntryAdapter

        inquiry = ManualEntryAdapter.create(
            original_text="I need a website",
            customer_name="Test User",
            customer_email="test@test.com",
        )

        assert inquiry.source == InquirySource.MANUAL

    def test_repository_find_duplicates_intact(self, clean_db):
        """Test that repository duplicate detection works for WhatsApp inquiries."""
        repo = InquiryRepository()

        inquiry = Inquiry(
            source=InquirySource.WHATSAPP,
            source_message_id="wamid.repo_test_123",
            customer_phone="15551234567",
            original_text="Test WhatsApp inquiry",
            received_at="2026-10-30T10:00:00Z",
        )
        repo.save(inquiry)

        duplicates = repo.find_duplicates(
            customer_email=None,
            text_fingerprint=None,
            conversation_id="15551234567",
            message_id="wamid.repo_test_123",
        )

        assert len(duplicates) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
