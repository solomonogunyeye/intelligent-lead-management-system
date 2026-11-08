"""LEADLIFT — Instagram intake adapter comprehensive tests.

Uses synthetic webhook payloads and mocked settings.
No real Instagram account, access token, or network connection required.
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
from leadlift.adapters.instagram import (
    InstagramIntakeAdapter,
    InstagramIntakeResult,
    InstagramMessageStatus,
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
        "instagram_verify_token": "test_verify_token_12345",
        "instagram_app_secret": "test_app_secret_12345",
    }


def _text_event(mid="mid.1234567890", sender="17841405792314149", body="Hi, I need a website. Budget $5000.", ts="1729999999000", name=None):
    event = {
        "object": "instagram",
        "entry": [{"id": "17841400000000000", "time": 1729999999, "messaging": [{"sender": {"id": sender}, "recipient": {"id": "17841400000000000"}, "timestamp": ts, "message": {"mid": mid, "text": body}}]}],
    }
    if name:
        event["entry"][0]["messaging"][0]["sender"]["name"] = name
    return event


def _echo_event(mid="mid.echo.123", body="Thanks!"):
    return {"object": "instagram", "entry": [{"id": "17841400000000000", "time": 1729999999, "messaging": [{"sender": {"id": "17841400000000000"}, "recipient": {"id": "17841405792314149"}, "timestamp": "1729999999000", "message": {"mid": mid, "text": body, "is_echo": True}}]}]}


def _reaction_event(mid="mid.react.123"):
    return {"object": "instagram", "entry": [{"id": "17841400000000000", "time": 1729999999, "messaging": [{"sender": {"id": "17841405792314149"}, "recipient": {"id": "17841400000000000"}, "timestamp": "1729999999000", "reaction": {"mid": mid, "action": "react", "reaction": "love"}}]}]}


def _delete_event(mid="mid.delete.123"):
    return {"object": "instagram", "entry": [{"id": "17841400000000000", "time": 1729999999, "messaging": [{"sender": {"id": "17841405792314149"}, "recipient": {"id": "17841400000000000"}, "timestamp": "1729999999000", "message": {"mid": mid, "is_deleted": True}}]}]}


def _read_event():
    return {"object": "instagram", "entry": [{"id": "17841400000000000", "time": 1729999999, "messaging": [{"sender": {"id": "17841405792314149"}, "recipient": {"id": "17841400000000000"}, "timestamp": "1729999999000", "read": {"mid": "mid.read.123", "watermark": 1729999999000}}]}]}


def _delivery_event():
    return {"object": "instagram", "entry": [{"id": "17841400000000000", "time": 1729999999, "messaging": [{"sender": {"id": "17841405792314149"}, "recipient": {"id": "17841400000000000"}, "timestamp": "1729999999000", "delivery": {"mids": ["mid.delivery.123"], "watermark": 1729999999000}}]}]}


def _media_event(mid="mid.media.123", mtype="image", body=None):
    msg = {"mid": mid, "attachments": [{"type": mtype, "payload": {"url": "https://example.com/media.jpg"}}]}
    if body:
        msg["text"] = body
    return {"object": "instagram", "entry": [{"id": "17841400000000000", "time": 1729999999, "messaging": [{"sender": {"id": "17841405792314149"}, "recipient": {"id": "17841400000000000"}, "timestamp": "1729999999000", "message": msg}]}]}


def _unsupported_event(mid="mid.unsup.123", atype="share"):
    return {"object": "instagram", "entry": [{"id": "17841400000000000", "time": 1729999999, "messaging": [{"sender": {"id": "17841405792314149"}, "recipient": {"id": "17841400000000000"}, "timestamp": "1729999999000", "message": {"mid": mid, "attachments": [{"type": atype, "payload": {"url": "https://example.com/share"}}]}}]}]}


# ---------------------------------------------------------------------------
# Test Class: Webhook Verification (GET)
# ---------------------------------------------------------------------------

class TestWebhookVerification:
    def test_successful(self, mock_settings):
        a = InstagramIntakeAdapter(settings=mock_settings)
        assert a.verify_webhook(mode="subscribe", token="test_verify_token_12345", challenge="ch") == "ch"

    def test_wrong_token(self, mock_settings):
        a = InstagramIntakeAdapter(settings=mock_settings)
        assert a.verify_webhook(mode="subscribe", token="wrong", challenge="ch") is None

    def test_no_token(self):
        a = InstagramIntakeAdapter(settings={})
        assert a.verify_webhook(mode="subscribe", token="x", challenge="ch") is None


# ---------------------------------------------------------------------------
# Test Class: Signature Validation
# ---------------------------------------------------------------------------

class TestSignatureValidation:
    def test_valid(self, mock_settings):
        a = InstagramIntakeAdapter(settings=mock_settings)
        secret = mock_settings["instagram_app_secret"]
        payload = b'{"object": "instagram"}'
        sig = hmac.new(secret.encode(), msg=payload, digestmod=hashlib.sha256).hexdigest()
        assert a.validate_signature(payload, sig, secret) is True

    def test_valid_prefix(self, mock_settings):
        a = InstagramIntakeAdapter(settings=mock_settings)
        secret = mock_settings["instagram_app_secret"]
        payload = b'{"object": "instagram"}'
        sig = "sha256=" + hmac.new(secret.encode(), msg=payload, digestmod=hashlib.sha256).hexdigest()
        assert a.validate_signature(payload, sig, secret) is True

    def test_tampered(self, mock_settings):
        a = InstagramIntakeAdapter(settings=mock_settings)
        secret = mock_settings["instagram_app_secret"]
        sig = hmac.new(secret.encode(), msg=b'{"object": "instagram"}', digestmod=hashlib.sha256).hexdigest()
        assert a.validate_signature(b'{"object": "evil"}', sig, secret) is False

    def test_wrong_secret(self, mock_settings):
        a = InstagramIntakeAdapter(settings=mock_settings)
        payload = b'{"object": "instagram"}'
        sig = hmac.new(b"wrong", msg=payload, digestmod=hashlib.sha256).hexdigest()
        assert a.validate_signature(payload, sig, mock_settings["instagram_app_secret"]) is False

    def test_missing_header(self, mock_settings):
        a = InstagramIntakeAdapter(settings=mock_settings)
        assert a.validate_signature(b'{}', None, mock_settings["instagram_app_secret"]) is False

    def test_missing_secret(self, mock_settings):
        a = InstagramIntakeAdapter(settings=mock_settings)
        assert a.validate_signature(b'{}', "abc", None) is False

    def test_empty_header(self, mock_settings):
        a = InstagramIntakeAdapter(settings=mock_settings)
        assert a.validate_signature(b'{}', "", mock_settings["instagram_app_secret"]) is False


# ---------------------------------------------------------------------------
# Test Class: Valid Text Messages
# ---------------------------------------------------------------------------

class TestValidTextMessages:
    def test_extraction(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event())
        assert r.success is True
        assert r.status == InstagramMessageStatus.EXTRACTED.value
        assert r.extracted_inquiry is not None
        assert r.extracted_inquiry.source == InquirySource.INSTAGRAM

    def test_message_id(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event(mid="mid.custom"))
        assert r.message_id == "mid.custom"
        assert r.extracted_inquiry.source_message_id == "mid.custom"

    def test_sender_id(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event(sender="17841409999999999"))
        assert r.sender_id == "17841409999999999"
        assert r.extracted_inquiry.source_account == "17841409999999999"

    def test_sender_name(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event(name="alice_ig"))
        assert r.sender_name == "alice_ig"
        assert r.extracted_inquiry.customer_name == "alice_ig"

    def test_missing_sender_name(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event())
        assert r.sender_name is None
        assert r.extracted_inquiry.customer_name is None

    def test_conversation_id(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event(sender="17841405792314149"))
        assert r.conversation_id == "17841405792314149"

    def test_original_text(self, clean_db, mock_settings):
        body = "We need a website. Budget $5000."
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event(body=body))
        assert r.extracted_inquiry.original_text == body

    def test_received_at(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event(ts="1729999999000"))
        assert r.extracted_inquiry.received_at == "1729999999000"

    def test_missing_timestamp(self, clean_db, mock_settings):
        payload = _text_event()
        del payload["entry"][0]["messaging"][0]["timestamp"]
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(payload)
        assert r.success is True

    def test_missing_message_id(self, clean_db, mock_settings):
        payload = _text_event()
        del payload["entry"][0]["messaging"][0]["message"]["mid"]
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(payload)
        assert r.message_id is not None

    def test_empty_text(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event(body="   "))
        assert r.is_invalid is True

    def test_missing_text(self, clean_db, mock_settings):
        payload = _text_event()
        del payload["entry"][0]["messaging"][0]["message"]["text"]
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(payload)
        assert r.is_invalid is True


# ---------------------------------------------------------------------------
# Test Class: Multiple Messages
# ---------------------------------------------------------------------------

class TestMultipleMessages:
    def test_multiple_in_one_event(self, clean_db, mock_settings):
        payload = {"object": "instagram", "entry": [{"id": "17841400000000000", "time": 1729999999, "messaging": [
            {"sender": {"id": "17841405792314149"}, "recipient": {"id": "17841400000000000"}, "timestamp": "1729999999000", "message": {"mid": "mid.first", "text": "First message"}},
            {"sender": {"id": "17841405792314150"}, "recipient": {"id": "17841400000000000"}, "timestamp": "1729999998000", "message": {"mid": "mid.second", "text": "Second message"}},
        ]}]}
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(payload)
        assert r.success is True


# ---------------------------------------------------------------------------
# Test Class: Unsupported Events
# ---------------------------------------------------------------------------

class TestUnsupportedEvents:
    def test_image(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_media_event(mtype="image"))
        assert r.is_unsupported is True

    def test_video(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_media_event(mtype="video"))
        assert r.is_unsupported is True

    def test_audio(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_media_event(mtype="audio"))
        assert r.is_unsupported is True

    def test_unsupported_attachment(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_unsupported_event())
        assert r.is_unsupported is True

    def test_reaction(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_reaction_event())
        assert r.is_unsupported is True
        assert r.event_type == "reaction"

    def test_delete(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_delete_event())
        assert r.is_unsupported is True
        assert r.event_type == "message_delete"

    def test_echo(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_echo_event())
        assert r.is_echo is True
        assert r.is_unsupported is True

    def test_read_receipt(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_read_event())
        assert r.is_unsupported is True
        assert r.event_type == "read"

    def test_delivery(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_delivery_event())
        assert r.is_unsupported is True
        assert r.event_type == "delivery"

    def test_media_with_text_still_unsupported(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_media_event(mtype="image", body="Check this"))
        assert r.is_unsupported is True


# ---------------------------------------------------------------------------
# Test Class: Malformed Payloads
# ---------------------------------------------------------------------------

class TestMalformedPayloads:
    def test_not_dict(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload("not a dict")
        assert r.is_invalid is True

    def test_missing_entry(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload({"object": "instagram"})
        assert r.is_invalid is True

    def test_empty_entry(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload({"object": "instagram", "entry": []})
        assert r.is_invalid is True

    def test_wrong_object(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload({"object": "facebook", "entry": [{}]})
        assert r.is_invalid is True

    def test_no_messaging(self, clean_db, mock_settings):
        payload = _text_event()
        del payload["entry"][0]["messaging"]
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(payload)
        assert r.success is True
        assert r.status == InstagramMessageStatus.RECEIVED.value

    def test_empty_messaging(self, clean_db, mock_settings):
        payload = _text_event()
        payload["entry"][0]["messaging"] = []
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(payload)
        assert r.success is True


# ---------------------------------------------------------------------------
# Test Class: Duplicate Detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection:
    def test_replay(self, clean_db, mock_settings):
        payload = _text_event(mid="mid.replay")
        a = InstagramIntakeAdapter(settings=mock_settings)
        first = a.process_webhook_payload(payload)
        second = a.process_webhook_payload(payload)
        assert first.status == InstagramMessageStatus.EXTRACTED.value
        assert second.status == InstagramMessageStatus.DUPLICATE.value
        assert second.is_duplicate is True

    def test_different_ids(self, clean_db, mock_settings):
        a = InstagramIntakeAdapter(settings=mock_settings)
        r1 = a.process_webhook_payload(_text_event(mid="mid.a", body="First message"))
        r2 = a.process_webhook_payload(_text_event(mid="mid.b", body="Second message"))
        assert r1.status == InstagramMessageStatus.EXTRACTED.value
        assert r2.status == InstagramMessageStatus.EXTRACTED.value

    def test_db_duplicate(self, clean_db, mock_settings):
        payload = _text_event(mid="mid.dbdup")
        a = InstagramIntakeAdapter(settings=mock_settings)
        r = a.process_webhook_payload(payload)
        a.save_confirmed_inquiry(r.extracted_inquiry)
        a2 = InstagramIntakeAdapter(settings=mock_settings)
        r2 = a2.process_webhook_payload(payload)
        assert r2.is_duplicate is True

    def test_persistence(self, clean_db, mock_settings):
        payload = _text_event(mid="mid.persist")
        a = InstagramIntakeAdapter(settings=mock_settings)
        r = a.process_webhook_payload(payload)
        a.save_confirmed_inquiry(r.extracted_inquiry)
        a2 = InstagramIntakeAdapter(settings=mock_settings)
        r2 = a2.process_webhook_payload(payload)
        assert r2.is_duplicate is True


# ---------------------------------------------------------------------------
# Test Class: Inquiry Mapping
# ---------------------------------------------------------------------------

class TestInquiryMapping:
    def test_source(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event())
        assert r.extracted_inquiry.source == InquirySource.INSTAGRAM

    def test_platform(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event())
        assert r.extracted_inquiry.source_platform == "instagram"

    def test_no_email(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event())
        assert r.extracted_inquiry.customer_email is None

    def test_no_phone(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event())
        assert r.extracted_inquiry.customer_phone is None

    def test_sender_not_phone(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event(sender="17841405792314149"))
        assert r.extracted_inquiry.source_account == "17841405792314149"
        assert r.extracted_inquiry.customer_phone is None

    def test_message_id(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event(mid="mid.unique"))
        assert r.extracted_inquiry.source_message_id == "mid.unique"

    def test_conversation_id(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event(sender="17841405792314149"))
        assert r.extracted_inquiry.source_conversation_id == "17841405792314149"


# ---------------------------------------------------------------------------
# Test Class: Review Workflow
# ---------------------------------------------------------------------------

class TestReviewWorkflow:
    def test_save(self, clean_db, mock_settings):
        payload = _text_event(mid="mid.save")
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(payload)
        repo = InquiryRepository()
        assert len(repo.get_all()) == 0
        saved = InstagramIntakeAdapter(settings=mock_settings).save_confirmed_inquiry(r.extracted_inquiry)
        assert saved.inquiry_id is not None
        assert len(repo.get_all()) == 1

    def test_cancel_no_save(self, clean_db, mock_settings):
        payload = _text_event(mid="mid.cancel")
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(payload)
        repo = InquiryRepository()
        assert len(repo.get_all()) == 0

    def test_duplicate_save(self, clean_db, mock_settings):
        payload = _text_event(mid="mid.dupsave")
        a = InstagramIntakeAdapter(settings=mock_settings)
        r = a.process_webhook_payload(payload)
        saved = a.save_confirmed_inquiry(r.extracted_inquiry)
        inq2 = Inquiry(source=InquirySource.INSTAGRAM, source_platform="instagram", source_message_id=r.extracted_inquiry.source_message_id, source_conversation_id=r.extracted_inquiry.source_conversation_id, original_text="Different")
        saved2 = a.save_confirmed_inquiry(inq2)
        assert saved2.inquiry_id == saved.inquiry_id

    def test_csv_result(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event())
        csv = r.to_csv_import_result()
        assert isinstance(csv, CSVImportResult)
        assert csv.accepted == 1

    def test_unsupported_csv(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_reaction_event())
        csv = r.to_csv_import_result()
        assert csv.accepted == 0
        assert csv.rejected == 1


# ---------------------------------------------------------------------------
# Test Class: Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_persisted(self, clean_db, mock_settings):
        payload = _text_event(mid="mid.persist")
        a = InstagramIntakeAdapter(settings=mock_settings)
        r = a.process_webhook_payload(payload)
        saved = a.save_confirmed_inquiry(r.extracted_inquiry)
        repo2 = InquiryRepository()
        loaded = repo2.get(saved.inquiry_id)
        assert loaded is not None
        assert loaded.source == InquirySource.INSTAGRAM

    def test_restart_dup(self, clean_db, mock_settings):
        payload = _text_event(mid="mid.restartdup")
        a = InstagramIntakeAdapter(settings=mock_settings)
        r = a.process_webhook_payload(payload)
        a.save_confirmed_inquiry(r.extracted_inquiry)
        a2 = InstagramIntakeAdapter(settings=mock_settings)
        r2 = a2.process_webhook_payload(payload)
        assert r2.is_duplicate is True


# ---------------------------------------------------------------------------
# Test Class: Security
# ---------------------------------------------------------------------------

class TestSecurity:
    def test_no_print(self):
        import inspect, re
        src = inspect.getsource(InstagramIntakeAdapter)
        assert len(re.findall(r'\bprint\s*\(', src)) == 0

    def test_no_dangerous(self):
        import inspect
        src = inspect.getsource(InstagramIntakeAdapter)
        for p in ["messages.send", "messages.delete", "chats.delete"]:
            assert p not in src


# ---------------------------------------------------------------------------
# Test Class: Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_long_text(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event(body="We need a website. " * 200))
        assert r.success is True

    def test_special_chars(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event(body="Need website with émojis 😊"))
        assert r.success is True

    def test_whitespace(self, clean_db, mock_settings):
        r = InstagramIntakeAdapter(settings=mock_settings).process_webhook_payload(_text_event(body="   "))
        assert r.is_invalid is True

    def test_enum_exists(self):
        assert InquirySource.INSTAGRAM == "instagram"

    def test_email_still_works(self, clean_db):
        from leadlift.adapters.email import GmailIntakeAdapter
        from leadlift.config import Settings
        assert GmailIntakeAdapter(settings=Settings()) is not None

    def test_whatsapp_still_works(self, clean_db, mock_settings):
        from leadlift.adapters.whatsapp import WhatsAppIntakeAdapter
        assert WhatsAppIntakeAdapter(settings=mock_settings) is not None

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
        inq = Inquiry(source=InquirySource.INSTAGRAM, source_message_id="mid.repo", source_account="123", original_text="Test", received_at="2026-10-30T10:00:00Z")
        repo.save(inq)
        dups = repo.find_duplicates(customer_email=None, text_fingerprint=None, conversation_id="123", message_id="mid.repo")
        assert len(dups) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
