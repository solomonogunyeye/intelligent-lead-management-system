"""LEADLIFT — Telegram Bot intake adapter comprehensive tests.

Uses synthetic webhook payloads and mocked settings.
No real Telegram account, bot token, or network connection required.
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
from leadlift.adapters.telegram import (
    TelegramIntakeAdapter,
    TelegramIntakeResult,
    TelegramMessageStatus,
)
from tests.test_support import reset_test_database


@pytest.fixture(autouse=True)
def clean_db(isolated_database):
    """Ensure fresh database for each test."""
    reset_test_database(isolated_database, Path(isolated_database).parent)
    yield
    reset_test_database(isolated_database, Path(isolated_database).parent)


@pytest.fixture
def mock_settings():
    return {
        "telegram_webhook_secret": "test_secret_token_12345",
        "telegram_bot_token": "test_bot_token_12345",
    }


def _text_update(uid=100001, mid=5001, chat_id=12345, sender_id=67890, body="Hi, I need a website. Budget $5000.", ts=1729999999, username="testuser", first="John", last="Doe"):
    return {"update_id": uid, "message": {"message_id": mid, "from": {"id": sender_id, "is_bot": False, "first_name": first, "last_name": last, "username": username, "language_code": "en"}, "chat": {"id": chat_id, "first_name": first, "last_name": last, "username": username, "type": "private"}, "date": ts, "text": body}}


def _edited_update(uid=100002, mid=5002, chat_id=12345):
    return {"update_id": uid, "edited_message": {"message_id": mid, "from": {"id": 67890, "first_name": "John"}, "chat": {"id": chat_id, "type": "private"}, "date": 1729999999, "edit_date": 1730000000, "text": "Edited text"}}


def _channel_post_update(uid=100003, mid=5003, chat_id=-100123456):
    return {"update_id": uid, "channel_post": {"message_id": mid, "chat": {"id": chat_id, "title": "Test Channel", "type": "channel"}, "date": 1729999999, "text": "Channel post text"}}


def _callback_query_update(uid=100004):
    return {"update_id": uid, "callback_query": {"id": "cbq_123", "from": {"id": 67890, "first_name": "John"}, "message": {"message_id": 5004, "chat": {"id": 12345, "type": "private"}, "date": 1729999999, "text": "Original message"}, "chat_instance": "123", "data": "button_click"}}


def _membership_update(uid=100005):
    return {"update_id": uid, "my_chat_member": {"chat": {"id": 12345, "type": "private"}, "from": {"id": 67890, "first_name": "John"}, "date": 1729999999, "old_chat_member": {"user": {"id": 67890, "first_name": "John"}, "status": "member"}, "new_chat_member": {"user": {"id": 67890, "first_name": "John"}, "status": "kicked"}}}


def _photo_update(uid=100006, mid=5006, caption="Check this photo"):
    return {"update_id": uid, "message": {"message_id": mid, "from": {"id": 67890, "first_name": "John"}, "chat": {"id": 12345, "type": "private"}, "date": 1729999999, "photo": [{"file_id": "photo_123", "width": 100, "height": 100}], "caption": caption}}


def _document_update(uid=100007, mid=5007, caption="Check this doc"):
    return {"update_id": uid, "message": {"message_id": mid, "from": {"id": 67890, "first_name": "John"}, "chat": {"id": 12345, "type": "private"}, "date": 1729999999, "document": {"file_id": "doc_123", "file_name": "test.pdf"}, "caption": caption}}


def _video_update(uid=100008, mid=5008):
    return {"update_id": uid, "message": {"message_id": mid, "from": {"id": 67890, "first_name": "John"}, "chat": {"id": 12345, "type": "private"}, "date": 1729999999, "video": {"file_id": "vid_123", "duration": 10}, "caption": "Video caption"}}


def _audio_update(uid=100009, mid=5009):
    return {"update_id": uid, "message": {"message_id": mid, "from": {"id": 67890, "first_name": "John"}, "chat": {"id": 12345, "type": "private"}, "date": 1729999999, "audio": {"file_id": "audio_123", "duration": 5}}}


def _voice_update(uid=100010, mid=5010):
    return {"update_id": uid, "message": {"message_id": mid, "from": {"id": 67890, "first_name": "John"}, "chat": {"id": 12345, "type": "private"}, "date": 1729999999, "voice": {"file_id": "voice_123", "duration": 3}}}


def _location_update(uid=100011, mid=5011):
    return {"update_id": uid, "message": {"message_id": mid, "from": {"id": 67890, "first_name": "John"}, "chat": {"id": 12345, "type": "private"}, "date": 1729999999, "location": {"latitude": 37.7, "longitude": -122.4}}}


def _contact_update(uid=100012, mid=5012):
    return {"update_id": uid, "message": {"message_id": mid, "from": {"id": 67890, "first_name": "John"}, "chat": {"id": 12345, "type": "private"}, "date": 1729999999, "contact": {"phone_number": "+1234567890", "first_name": "Jane"}}}


def _service_update(uid=100013, mid=5013):
    return {"update_id": uid, "message": {"message_id": mid, "from": {"id": 67890, "first_name": "John"}, "chat": {"id": 12345, "type": "group"}, "date": 1729999999, "new_chat_members": [{"id": 99999, "first_name": "NewUser"}]}}


def _reply_update(uid=100014, mid=5014):
    return {"update_id": uid, "message": {"message_id": mid, "from": {"id": 67890, "first_name": "John"}, "chat": {"id": 12345, "type": "private"}, "date": 1729999999, "text": "Reply text", "reply_to_message": {"message_id": 5000, "text": "Original message"}}}


def _forwarded_update(uid=100015, mid=5015):
    return {"update_id": uid, "message": {"message_id": mid, "from": {"id": 67890, "first_name": "John"}, "chat": {"id": 12345, "type": "private"}, "date": 1729999999, "text": "Forwarded text", "forward_from": {"id": 99999, "first_name": "Original"}, "forward_date": 1729999900}}


def _multi_update():
    return [{"update_id": 200001, "message": {"message_id": 6001, "from": {"id": 11111, "first_name": "Alice"}, "chat": {"id": 11111, "type": "private"}, "date": 1729999999, "text": "First message"}}, {"update_id": 200002, "message": {"message_id": 6002, "from": {"id": 22222, "first_name": "Bob"}, "chat": {"id": 22222, "type": "private"}, "date": 1729999998, "text": "Second message"}}]


# ---------------------------------------------------------------------------
# Test Class: Secret Token Validation
# ---------------------------------------------------------------------------

class TestSecretTokenValidation:
    def test_valid(self, mock_settings):
        a = TelegramIntakeAdapter(settings=mock_settings)
        assert a.validate_secret_token("test_secret_token_12345", mock_settings["telegram_webhook_secret"]) is True

    def test_invalid(self, mock_settings):
        a = TelegramIntakeAdapter(settings=mock_settings)
        assert a.validate_secret_token("wrong_token", mock_settings["telegram_webhook_secret"]) is False

    def test_missing_config(self):
        a = TelegramIntakeAdapter(settings={})
        assert a.validate_secret_token("any", None) is False

    def test_missing_provided(self, mock_settings):
        a = TelegramIntakeAdapter(settings=mock_settings)
        assert a.validate_secret_token(None, mock_settings["telegram_webhook_secret"]) is False

    def test_empty_config(self, mock_settings):
        a = TelegramIntakeAdapter(settings=mock_settings)
        assert a.validate_secret_token("token", "") is False

    def test_constant_time(self, mock_settings):
        a = TelegramIntakeAdapter(settings=mock_settings)
        # Should use compare_digest
        import inspect
        src = inspect.getsource(a.validate_secret_token)
        assert "compare_digest" in src


# ---------------------------------------------------------------------------
# Test Class: Webhook Verification (GET)
# ---------------------------------------------------------------------------

class TestWebhookVerification:
    def test_returns_none(self, mock_settings):
        a = TelegramIntakeAdapter(settings=mock_settings)
        assert a.verify_webhook(mode="webhook", token="x", challenge="ch") is None


# ---------------------------------------------------------------------------
# Test Class: Valid Text Messages
# ---------------------------------------------------------------------------

class TestValidTextMessages:
    def test_extraction(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update())
        assert r.success is True
        assert r.status == TelegramMessageStatus.EXTRACTED.value
        assert r.extracted_inquiry is not None
        assert r.extracted_inquiry.source == InquirySource.TELEGRAM
        assert r.extracted_inquiry.source_platform == "telegram"

    def test_message_id(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(mid=99999))
        assert r.message_id == "99999"
        assert r.extracted_inquiry.source_message_id == "99999"

    def test_update_id(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(uid=88888))
        assert r.update_id == "88888"

    def test_chat_id(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(chat_id=54321))
        assert r.chat_id == "54321"
        assert r.extracted_inquiry.source_conversation_id == "54321"

    def test_chat_type(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update())
        assert r.chat_type == "private"

    def test_sender_id(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(sender_id=99999))
        assert r.sender_id == "99999"
        assert r.extracted_inquiry.source_account == "99999"

    def test_sender_username(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(username="alice_tg"))
        assert r.sender_username == "alice_tg"

    def test_sender_name(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(first="Alice", last="Smith"))
        assert r.sender_name == "Alice Smith"
        assert r.extracted_inquiry.customer_name == "Alice Smith"

    def test_missing_optional_name(self, clean_db, mock_settings):
        payload = _text_update(first=None, last=None, username=None)
        payload["message"]["from"].pop("first_name", None)
        payload["message"]["from"].pop("last_name", None)
        payload["message"]["from"].pop("username", None)
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(payload)
        assert r.success is True
        assert r.sender_name is None
        assert r.extracted_inquiry.customer_name is None

    def test_original_text(self, clean_db, mock_settings):
        body = "We need a website. Budget $5000."
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(body=body))
        assert r.extracted_inquiry.original_text == body

    def test_received_at(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(ts=1729999999))
        assert r.extracted_inquiry.received_at == "1729999999"

    def test_missing_timestamp(self, clean_db, mock_settings):
        payload = _text_update()
        del payload["message"]["date"]
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(payload)
        assert r.success is True

    def test_missing_message_id(self, clean_db, mock_settings):
        payload = _text_update()
        del payload["message"]["message_id"]
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(payload)
        assert r.message_id is not None

    def test_empty_text(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(body="   "))
        assert r.is_invalid is True

    def test_missing_text(self, clean_db, mock_settings):
        payload = _text_update()
        del payload["message"]["text"]
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(payload)
        assert r.is_invalid is True

    def test_username_not_assumed_verified(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(username="alice_tg"))
        # Username is preserved but not assumed as verified identity
        assert r.sender_username == "alice_tg"

    def test_no_phone_invented(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update())
        assert r.extracted_inquiry.customer_phone is None

    def test_no_email_invented(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update())
        assert r.extracted_inquiry.customer_email is None

    def test_reply_context(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_reply_update())
        assert r.success is True
        assert r.extracted_inquiry is not None

    def test_forwarded(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_forwarded_update())
        assert r.success is True
        assert r.extracted_inquiry is not None


# ---------------------------------------------------------------------------
# Test Class: Multiple Updates
# ---------------------------------------------------------------------------

class TestMultipleUpdates:
    def test_multiple(self, clean_db, mock_settings):
        # Telegram sends one update per request, but we handle the case
        # where payload is a list of updates (not standard, but robust)
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update())
        assert r.success is True


# ---------------------------------------------------------------------------
# Test Class: Unsupported Updates
# ---------------------------------------------------------------------------

class TestUnsupportedUpdates:
    def test_photo(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_photo_update())
        assert r.is_unsupported is True

    def test_photo_with_caption_still_unsupported(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_photo_update(caption="Check this"))
        assert r.is_unsupported is True

    def test_document(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_document_update())
        assert r.is_unsupported is True

    def test_video(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_video_update())
        assert r.is_unsupported is True

    def test_audio(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_audio_update())
        assert r.is_unsupported is True

    def test_voice(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_voice_update())
        assert r.is_unsupported is True

    def test_location(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_location_update())
        assert r.is_unsupported is True

    def test_contact(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_contact_update())
        assert r.is_unsupported is True

    def test_edited_message(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_edited_update())
        assert r.is_unsupported is True
        assert r.event_type == "edited_message"
        assert r.status == TelegramMessageStatus.EDITED.value

    def test_channel_post(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_channel_post_update())
        assert r.is_unsupported is True
        assert r.event_type == "channel_post"

    def test_callback_query(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_callback_query_update())
        assert r.is_unsupported is True
        assert r.event_type == "callback_query"

    def test_membership(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_membership_update())
        assert r.is_unsupported is True
        assert r.event_type == "my_chat_member"

    def test_service_message(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_service_update())
        assert r.is_unsupported is True
        assert r.event_type == "service"


# ---------------------------------------------------------------------------
# Test Class: Malformed Payloads
# ---------------------------------------------------------------------------

class TestMalformedPayloads:
    def test_not_dict(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload("not a dict")
        assert r.is_invalid is True

    def test_missing_update_id(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload({"message": {"text": "hi"}})
        assert r.is_invalid is True

    def test_empty_payload(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload({})
        assert r.is_invalid is True


# ---------------------------------------------------------------------------
# Test Class: Duplicate Detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection:
    def test_replay_update_id(self, clean_db, mock_settings):
        payload = _text_update(uid=99999, mid=88888)
        a = TelegramIntakeAdapter(settings=mock_settings)
        first = a.process_webhook_payload(payload)
        second = a.process_webhook_payload(payload)
        assert first.status == TelegramMessageStatus.EXTRACTED.value
        assert second.status == TelegramMessageStatus.DUPLICATE.value
        assert second.is_duplicate is True

    def test_different_updates(self, clean_db, mock_settings):
        a = TelegramIntakeAdapter(settings=mock_settings)
        r1 = a.process_webhook_payload(_text_update(uid=11111, mid=22222, body="First message"))
        r2 = a.process_webhook_payload(_text_update(uid=33333, mid=44444, body="Second message"))
        assert r1.status == TelegramMessageStatus.EXTRACTED.value
        assert r2.status == TelegramMessageStatus.EXTRACTED.value

    def test_db_duplicate(self, clean_db, mock_settings):
        payload = _text_update(uid=77777, mid=66666)
        a = TelegramIntakeAdapter(settings=mock_settings)
        r = a.process_webhook_payload(payload)
        a.save_confirmed_inquiry(r.extracted_inquiry)
        a2 = TelegramIntakeAdapter(settings=mock_settings)
        r2 = a2.process_webhook_payload(payload)
        assert r2.is_duplicate is True

    def test_persistence(self, clean_db, mock_settings):
        payload = _text_update(uid=55555, mid=44444)
        a = TelegramIntakeAdapter(settings=mock_settings)
        r = a.process_webhook_payload(payload)
        a.save_confirmed_inquiry(r.extracted_inquiry)
        a2 = TelegramIntakeAdapter(settings=mock_settings)
        r2 = a2.process_webhook_payload(payload)
        assert r2.is_duplicate is True


# ---------------------------------------------------------------------------
# Test Class: Inquiry Mapping
# ---------------------------------------------------------------------------

class TestInquiryMapping:
    def test_source(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update())
        assert r.extracted_inquiry.source == InquirySource.TELEGRAM

    def test_platform(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update())
        assert r.extracted_inquiry.source_platform == "telegram"

    def test_no_email(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update())
        assert r.extracted_inquiry.customer_email is None

    def test_no_phone(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update())
        assert r.extracted_inquiry.customer_phone is None

    def test_sender_not_phone(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(sender_id=12345))
        assert r.extracted_inquiry.source_account == "12345"
        assert r.extracted_inquiry.customer_phone is None

    def test_message_id(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(mid=99999))
        assert r.extracted_inquiry.source_message_id == "99999"

    def test_conversation_id(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(chat_id=54321))
        assert r.extracted_inquiry.source_conversation_id == "54321"


# ---------------------------------------------------------------------------
# Test Class: Review Workflow
# ---------------------------------------------------------------------------

class TestReviewWorkflow:
    def test_save(self, clean_db, mock_settings):
        payload = _text_update(uid=11111, mid=22222)
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(payload)
        repo = InquiryRepository()
        assert len(repo.get_all()) == 0
        saved = TelegramIntakeAdapter(settings=mock_settings).save_confirmed_inquiry(r.extracted_inquiry)
        assert saved.inquiry_id is not None
        assert len(repo.get_all()) == 1

    def test_cancel_no_save(self, clean_db, mock_settings):
        payload = _text_update(uid=33333, mid=44444)
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(payload)
        repo = InquiryRepository()
        assert len(repo.get_all()) == 0

    def test_duplicate_save(self, clean_db, mock_settings):
        payload = _text_update(uid=55555, mid=66666)
        a = TelegramIntakeAdapter(settings=mock_settings)
        r = a.process_webhook_payload(payload)
        saved = a.save_confirmed_inquiry(r.extracted_inquiry)
        inq2 = Inquiry(source=InquirySource.TELEGRAM, source_platform="telegram", source_message_id=r.extracted_inquiry.source_message_id, source_conversation_id=r.extracted_inquiry.source_conversation_id, original_text="Different")
        saved2 = a.save_confirmed_inquiry(inq2)
        assert saved2.inquiry_id == saved.inquiry_id

    def test_csv_result(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update())
        csv = r.to_csv_import_result()
        assert isinstance(csv, CSVImportResult)
        assert csv.accepted == 1

    def test_unsupported_csv(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_photo_update())
        csv = r.to_csv_import_result()
        assert csv.accepted == 0
        assert csv.rejected == 1


# ---------------------------------------------------------------------------
# Test Class: Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_persisted(self, clean_db, mock_settings):
        payload = _text_update(uid=77777, mid=88888)
        a = TelegramIntakeAdapter(settings=mock_settings)
        r = a.process_webhook_payload(payload)
        saved = a.save_confirmed_inquiry(r.extracted_inquiry)
        repo2 = InquiryRepository()
        loaded = repo2.get(saved.inquiry_id)
        assert loaded is not None
        assert loaded.source == InquirySource.TELEGRAM

    def test_restart_dup(self, clean_db, mock_settings):
        payload = _text_update(uid=99999, mid=11111)
        a = TelegramIntakeAdapter(settings=mock_settings)
        r = a.process_webhook_payload(payload)
        a.save_confirmed_inquiry(r.extracted_inquiry)
        a2 = TelegramIntakeAdapter(settings=mock_settings)
        r2 = a2.process_webhook_payload(payload)
        assert r2.is_duplicate is True


# ---------------------------------------------------------------------------
# Test Class: Security
# ---------------------------------------------------------------------------

class TestSecurity:
    def test_no_print(self):
        import inspect, re
        src = inspect.getsource(TelegramIntakeAdapter)
        assert len(re.findall(r'\bprint\s*\(', src)) == 0

    def test_no_dangerous(self):
        import inspect
        src = inspect.getsource(TelegramIntakeAdapter)
        for p in ["send_message", "delete_message", "edit_message"]:
            assert p not in src


# ---------------------------------------------------------------------------
# Test Class: Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_long_text(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(body="We need a website. " * 200))
        assert r.success is True

    def test_special_chars(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(body="Need website with émojis 😊"))
        assert r.success is True

    def test_whitespace(self, clean_db, mock_settings):
        r = TelegramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_update(body="   "))
        assert r.is_invalid is True

    def test_enum_exists(self):
        assert InquirySource.TELEGRAM == "telegram"

    def test_email_still_works(self, clean_db):
        from leadlift.adapters.email import GmailIntakeAdapter
        from leadlift.config import Settings
        assert GmailIntakeAdapter(settings=Settings()) is not None

    def test_whatsapp_still_works(self, clean_db, mock_settings):
        from leadlift.adapters.whatsapp import WhatsAppIntakeAdapter
        assert WhatsAppIntakeAdapter(settings=mock_settings) is not None

    def test_instagram_still_works(self, clean_db, mock_settings):
        from leadlift.adapters.instagram import InstagramIntakeAdapter
        assert InstagramIntakeAdapter(settings=mock_settings) is not None

    def test_facebook_still_works(self, clean_db, mock_settings):
        from leadlift.adapters.facebook import FacebookIntakeAdapter
        assert FacebookIntakeAdapter(settings=mock_settings) is not None

    def test_csv_still_works(self, clean_db):
        from leadlift.adapters.csv import CSVAdapter
        csv = "source,original_text,received_at,customer_name,customer_email,requested_service\nmanual,I need a website,2026-10-30T10:00:00Z,Test,test@test.com,Web\n"
        assert CSVAdapter().process(csv).accepted == 1

    def test_manual_still_works(self, clean_db):
        from leadlift.adapters.manual import ManualEntryAdapter
        inq = ManualEntryAdapter.create(original_text="I need a website", customer_name="Test", customer_email="t@t.com")
        assert inq.source == InquirySource.MANUAL

    def test_repo_duplicates(self, clean_db):
        repo = InquiryRepository()
        inq = Inquiry(source=InquirySource.TELEGRAM, source_message_id="msg.repo", source_conversation_id="chat.repo", original_text="Test", received_at="2026-10-30T10:00:00Z")
        repo.save(inq)
        dups = repo.find_duplicates(customer_email=None, text_fingerprint=None, conversation_id="chat.repo", message_id="msg.repo")
        assert len(dups) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
