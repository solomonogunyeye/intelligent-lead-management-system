"""LEADLIFT — Website form intake adapter comprehensive tests.

Uses synthetic payloads and mocked settings.
No real website, customer data, or network connection required.
"""

from __future__ import annotations

import hashlib
import hmac
import json
from datetime import datetime
from typing import Optional

import pytest

from pathlib import Path

from leadlift.models import Inquiry, InquirySource, CSVImportResult
from leadlift.repository import InquiryRepository
from leadlift.adapters.website_form import (
    WebsiteFormIntakeAdapter,
    WebsiteFormIntakeResult,
    WebsiteFormMessageStatus,
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
        "website_form_webhook_secret": "test_webhook_secret_12345",
        "website_form_max_payload_bytes": 65536,
        "website_form_max_field_length": 10000,
    }


def _valid_payload(submission_id="sub-123", message="I need a website built. Budget $5000.", **kwargs):
    payload = {
        "submission_id": submission_id,
        "message": message,
    }
    payload.update(kwargs)
    return payload


def _secret():
    return "test_webhook_secret_12345"


# ---------------------------------------------------------------------------
# Test Class: Secret Validation
# ---------------------------------------------------------------------------

class TestSecretValidation:
    def test_valid(self, mock_settings):
        a = WebsiteFormIntakeAdapter(settings=mock_settings)
        assert a.validate_secret("test_webhook_secret_12345", mock_settings["website_form_webhook_secret"]) is True

    def test_invalid(self, mock_settings):
        a = WebsiteFormIntakeAdapter(settings=mock_settings)
        assert a.validate_secret("wrong_secret", mock_settings["website_form_webhook_secret"]) is False

    def test_missing_config(self):
        a = WebsiteFormIntakeAdapter(settings={})
        assert a.validate_secret("any", None) is False

    def test_missing_provided(self, mock_settings):
        a = WebsiteFormIntakeAdapter(settings=mock_settings)
        assert a.validate_secret(None, mock_settings["website_form_webhook_secret"]) is False

    def test_empty_config(self, mock_settings):
        a = WebsiteFormIntakeAdapter(settings=mock_settings)
        assert a.validate_secret("secret", "") is False

    def test_constant_time(self, mock_settings):
        import inspect
        a = WebsiteFormIntakeAdapter(settings=mock_settings)
        src = inspect.getsource(a.validate_secret)
        assert "compare_digest" in src


# ---------------------------------------------------------------------------
# Test Class: Webhook Verification (GET)
# ---------------------------------------------------------------------------

class TestWebhookVerification:
    def test_returns_none(self, mock_settings):
        a = WebsiteFormIntakeAdapter(settings=mock_settings)
        assert a.verify_webhook(mode="webhook", token="x", challenge="ch") is None


# ---------------------------------------------------------------------------
# Test Class: Valid Submissions
# ---------------------------------------------------------------------------

class TestValidSubmissions:
    def test_basic(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.success is True
        assert r.status == WebsiteFormMessageStatus.EXTRACTED.value
        assert r.extracted_inquiry is not None
        assert r.extracted_inquiry.source == InquirySource.WEBSITE_FORM
        assert r.extracted_inquiry.source_platform == "website_form"

    def test_all_fields(self, clean_db, mock_settings):
        payload = _valid_payload(
            submission_id="sub-full",
            form_id="contact-form",
            website="example.com",
            customer_name="John Doe",
            customer_email="john@example.com",
            customer_phone="+1234567890",
            subject="Inquiry about services",
            submitted_at="2026-10-30T10:00:00Z",
            page_url="https://example.com/contact",
            consent=True,
            custom_fields={"budget": "$5000"},
        )
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.success is True
        inq = r.extracted_inquiry
        assert inq.customer_name == "John Doe"
        assert inq.customer_email == "john@example.com"
        assert inq.customer_phone == "+1234567890"
        assert inq.source_account == "example.com"

    def test_message_id_preserved(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(submission_id="sub-custom"), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.submission_id == "sub-custom"
        assert r.extracted_inquiry.source_message_id == "sub-custom"

    def test_missing_optional_fields(self, clean_db, mock_settings):
        payload = {"submission_id": "sub-min", "message": "I need help."}
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.success is True
        inq = r.extracted_inquiry
        assert inq.customer_name is None
        assert inq.customer_email is None
        assert inq.customer_phone is None

    def test_missing_submission_id_generates(self, clean_db, mock_settings):
        payload = {"message": "I need help."}
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.submission_id is not None
        assert len(r.submission_id) > 0

    def test_original_text_preserved(self, clean_db, mock_settings):
        msg = "We need a website. Budget $5000."
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(message=msg), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert msg in r.extracted_inquiry.original_text

    def test_subject_in_original_text(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(subject="Website inquiry"), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert "Subject: Website inquiry" in r.extracted_inquiry.original_text

    def test_timestamp_preserved(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(submitted_at="2026-10-30T10:00:00Z"), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.extracted_inquiry.received_at == "2026-10-30T10:00:00Z"

    def test_no_timestamp_generates(self, clean_db, mock_settings):
        payload = {"submission_id": "sub-nots", "message": "Hello"}
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.extracted_inquiry.received_at is not None

    def test_no_invention(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        inq = r.extracted_inquiry
        # Only provided fields should be set
        assert inq.customer_name is None
        assert inq.customer_email is None
        assert inq.customer_phone is None


# ---------------------------------------------------------------------------
# Test Class: Missing Required Fields
# ---------------------------------------------------------------------------

class TestMissingRequired:
    def test_missing_message(self, clean_db, mock_settings):
        payload = {"submission_id": "sub-1", "customer_name": "John"}
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.is_invalid is True

    def test_empty_message(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(message="   "), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.is_invalid is True

    def test_whitespace_message(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(message=""), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.is_invalid is True


# ---------------------------------------------------------------------------
# Test Class: Malformed Payloads
# ---------------------------------------------------------------------------

class TestMalformedPayloads:
    def test_not_dict(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            "not a dict", secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.is_invalid is True

    def test_oversized_payload(self, clean_db, mock_settings):
        settings = {"website_form_webhook_secret": "test_webhook_secret_12345", "website_form_max_payload_bytes": 100}
        payload = _valid_payload(message="A" * 200)
        r = WebsiteFormIntakeAdapter(settings=settings).process_webhook_payload(
            payload, secret=_secret(), configured_secret="test_webhook_secret_12345")
        assert r.is_invalid is True


# ---------------------------------------------------------------------------
# Test Class: Authentication
# ---------------------------------------------------------------------------

class TestAuthentication:
    def test_missing_secret(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(), secret=None, configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.is_invalid is True
        assert any("secret" in e.get("message", "").lower() or "auth" in e.get("message", "").lower() for e in r.errors)

    def test_wrong_secret(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(), secret="wrong_secret", configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.is_invalid is True

    def test_missing_config(self, clean_db):
        r = WebsiteFormIntakeAdapter(settings={}).process_webhook_payload(
            _valid_payload(), secret="any", configured_secret="")
        assert r.is_invalid is True

    def test_no_secret_configured_fails_closed(self, clean_db):
        r = WebsiteFormIntakeAdapter(settings={}).process_webhook_payload(
            _valid_payload(), secret="test", configured_secret=None)
        assert r.is_invalid is True


# ---------------------------------------------------------------------------
# Test Class: Email Validation
# ---------------------------------------------------------------------------

class TestEmailValidation:
    def test_valid_email(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(customer_email="john@example.com"), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.success is True

    def test_invalid_email(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(customer_email="not-an-email"), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.is_invalid is True

    def test_empty_email_allowed(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(customer_email=""), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.success is True

    def test_none_email_allowed(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.success is True


# ---------------------------------------------------------------------------
# Test Class: URL Validation
# ---------------------------------------------------------------------------

class TestUrlValidation:
    def test_valid_url(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(page_url="https://example.com/contact"), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.success is True

    def test_invalid_url(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(page_url="not-a-url"), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.is_invalid is True

    def test_url_not_fetched(self, clean_db, mock_settings):
        # Ensure no network calls are made - just validation
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(page_url="https://example.com/page"), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.success is True


# ---------------------------------------------------------------------------
# Test Class: HTML/Script Handling
# ---------------------------------------------------------------------------

class TestHtmlScriptHandling:
    def test_html_in_message(self, clean_db, mock_settings):
        msg = "<script>alert('xss')</script>Hello"
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(message=msg), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.success is True
        # Message preserved as text, not executed
        assert "<script>" in r.extracted_inquiry.original_text

    def test_html_in_name(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(customer_name="<b>John</b>"), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.success is True


# ---------------------------------------------------------------------------
# Test Class: Duplicate Detection
# ---------------------------------------------------------------------------

class TestDuplicateDetection:
    def test_replay(self, clean_db, mock_settings):
        payload = _valid_payload(submission_id="sub-replay")
        a = WebsiteFormIntakeAdapter(settings=mock_settings)
        first = a.process_webhook_payload(payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        second = a.process_webhook_payload(payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert first.status == WebsiteFormMessageStatus.EXTRACTED.value
        assert second.status == WebsiteFormMessageStatus.DUPLICATE.value
        assert second.is_duplicate is True

    def test_different_ids(self, clean_db, mock_settings):
        a = WebsiteFormIntakeAdapter(settings=mock_settings)
        r1 = a.process_webhook_payload(_valid_payload(submission_id="sub-a", message="First message"), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        r2 = a.process_webhook_payload(_valid_payload(submission_id="sub-b", message="Second message"), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r1.status == WebsiteFormMessageStatus.EXTRACTED.value
        assert r2.status == WebsiteFormMessageStatus.EXTRACTED.value

    def test_db_duplicate(self, clean_db, mock_settings):
        payload = _valid_payload(submission_id="sub-dbdup")
        a = WebsiteFormIntakeAdapter(settings=mock_settings)
        r = a.process_webhook_payload(payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        a.save_confirmed_inquiry(r.extracted_inquiry)
        a2 = WebsiteFormIntakeAdapter(settings=mock_settings)
        r2 = a2.process_webhook_payload(payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r2.is_duplicate is True

    def test_persistence(self, clean_db, mock_settings):
        payload = _valid_payload(submission_id="sub-persist")
        a = WebsiteFormIntakeAdapter(settings=mock_settings)
        r = a.process_webhook_payload(payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        a.save_confirmed_inquiry(r.extracted_inquiry)
        a2 = WebsiteFormIntakeAdapter(settings=mock_settings)
        r2 = a2.process_webhook_payload(payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r2.is_duplicate is True

    def test_same_customer_different_submissions(self, clean_db, mock_settings):
        a = WebsiteFormIntakeAdapter(settings=mock_settings)
        r1 = a.process_webhook_payload(_valid_payload(submission_id="sub-1", customer_email="john@example.com", message="First inquiry"), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        r2 = a.process_webhook_payload(_valid_payload(submission_id="sub-2", customer_email="john@example.com", message="Second inquiry"), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r1.status == WebsiteFormMessageStatus.EXTRACTED.value
        assert r2.status == WebsiteFormMessageStatus.EXTRACTED.value
        assert r2.is_duplicate is False


# ---------------------------------------------------------------------------
# Test Class: Inquiry Mapping
# ---------------------------------------------------------------------------

class TestInquiryMapping:
    def test_source(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.extracted_inquiry.source == InquirySource.WEBSITE_FORM

    def test_platform(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.extracted_inquiry.source_platform == "website_form"

    def test_no_invention(self, clean_db, mock_settings):
        payload = {"submission_id": "sub-min", "message": "Hello"}
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        inq = r.extracted_inquiry
        assert inq.customer_name is None
        assert inq.customer_email is None
        assert inq.customer_phone is None

    def test_conversation_id(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(submission_id="sub-conv"), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.extracted_inquiry.source_conversation_id == "sub-conv"


# ---------------------------------------------------------------------------
# Test Class: Review Workflow
# ---------------------------------------------------------------------------

class TestReviewWorkflow:
    def test_save(self, clean_db, mock_settings):
        payload = _valid_payload(submission_id="sub-save")
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        repo = InquiryRepository()
        assert len(repo.get_all()) == 0
        saved = WebsiteFormIntakeAdapter(settings=mock_settings).save_confirmed_inquiry(r.extracted_inquiry)
        assert saved.inquiry_id is not None
        assert len(repo.get_all()) == 1

    def test_cancel_no_save(self, clean_db, mock_settings):
        payload = _valid_payload(submission_id="sub-cancel")
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        repo = InquiryRepository()
        assert len(repo.get_all()) == 0

    def test_duplicate_save(self, clean_db, mock_settings):
        payload = _valid_payload(submission_id="sub-dupsave")
        a = WebsiteFormIntakeAdapter(settings=mock_settings)
        r = a.process_webhook_payload(payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        saved = a.save_confirmed_inquiry(r.extracted_inquiry)
        inq2 = Inquiry(source=InquirySource.WEBSITE_FORM, source_platform="website_form", source_message_id=r.extracted_inquiry.source_message_id, source_conversation_id=r.extracted_inquiry.source_conversation_id, original_text="Different")
        saved2 = a.save_confirmed_inquiry(inq2)
        assert saved2.inquiry_id == saved.inquiry_id

    def test_csv_result(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        csv = r.to_csv_import_result()
        assert isinstance(csv, CSVImportResult)
        assert csv.accepted == 1

    def test_invalid_csv(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            {"message": ""}, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        csv = r.to_csv_import_result()
        assert csv.accepted == 0
        assert csv.rejected == 1


# ---------------------------------------------------------------------------
# Test Class: Persistence
# ---------------------------------------------------------------------------

class TestPersistence:
    def test_persisted(self, clean_db, mock_settings):
        payload = _valid_payload(submission_id="sub-persist")
        a = WebsiteFormIntakeAdapter(settings=mock_settings)
        r = a.process_webhook_payload(payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        saved = a.save_confirmed_inquiry(r.extracted_inquiry)
        repo2 = InquiryRepository()
        loaded = repo2.get(saved.inquiry_id)
        assert loaded is not None
        assert loaded.source == InquirySource.WEBSITE_FORM

    def test_restart_dup(self, clean_db, mock_settings):
        payload = _valid_payload(submission_id="sub-restartdup")
        a = WebsiteFormIntakeAdapter(settings=mock_settings)
        r = a.process_webhook_payload(payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        a.save_confirmed_inquiry(r.extracted_inquiry)
        a2 = WebsiteFormIntakeAdapter(settings=mock_settings)
        r2 = a2.process_webhook_payload(payload, secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r2.is_duplicate is True


# ---------------------------------------------------------------------------
# Test Class: Security
# ---------------------------------------------------------------------------

class TestSecurity:
    def test_no_print(self):
        import inspect, re
        src = inspect.getsource(WebsiteFormIntakeAdapter)
        assert len(re.findall(r'\bprint\s*\(', src)) == 0

    def test_no_dangerous(self):
        import inspect
        src = inspect.getsource(WebsiteFormIntakeAdapter)
        for p in ["requests.get", "urllib.request", "eval(", "exec("]:
            assert p not in src

    def test_no_url_fetching(self):
        import inspect
        src = inspect.getsource(WebsiteFormIntakeAdapter)
        assert "urlopen" not in src
        assert "requests.get" not in src


# ---------------------------------------------------------------------------
# Test Class: Edge Cases
# ---------------------------------------------------------------------------

class TestEdgeCases:
    def test_long_message_truncated(self, clean_db, mock_settings):
        settings = {"website_form_webhook_secret": "test_webhook_secret_12345", "website_form_max_field_length": 100}
        payload = _valid_payload(message="A" * 200)
        r = WebsiteFormIntakeAdapter(settings=settings).process_webhook_payload(
            payload, secret=_secret(), configured_secret="test_webhook_secret_12345")
        assert r.success is True
        assert len(r.extracted_inquiry.original_text) <= 100  # truncated to max_field_length

    def test_special_chars(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(message="Need website with émojis 😊"), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.success is True

    def test_oversized_field(self, clean_db, mock_settings):
        settings = {"website_form_webhook_secret": "test_webhook_secret_12345", "website_form_max_field_length": 50}
        payload = _valid_payload(customer_name="A" * 100)
        r = WebsiteFormIntakeAdapter(settings=settings).process_webhook_payload(
            payload, secret=_secret(), configured_secret="test_webhook_secret_12345")
        assert r.success is True
        assert len(r.extracted_inquiry.customer_name) <= 50

    def test_custom_fields(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(custom_fields={"budget": "$5000", "source": "google"}), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.success is True

    def test_consent_boolean(self, clean_db, mock_settings):
        r = WebsiteFormIntakeAdapter(settings=mock_settings).process_webhook_payload(
            _valid_payload(consent=True), secret=_secret(), configured_secret=mock_settings["website_form_webhook_secret"])
        assert r.success is True

    def test_enum_exists(self):
        assert InquirySource.WEBSITE_FORM == "website_form"

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

    def test_telegram_still_works(self, clean_db, mock_settings):
        from leadlift.adapters.telegram import TelegramIntakeAdapter
        assert TelegramIntakeAdapter(settings=mock_settings) is not None

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
        inq = Inquiry(source=InquirySource.WEBSITE_FORM, source_message_id="sub.repo", source_conversation_id="sub.repo", original_text="Test", received_at="2026-10-30T10:00:00Z")
        repo.save(inq)
        dups = repo.find_duplicates(customer_email=None, text_fingerprint=None, conversation_id="sub.repo", message_id="sub.repo")
        assert len(dups) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
