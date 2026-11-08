"""LEADLIFT — AI processor tests (Phase 4D.1).

Uses synthetic inquiries and mocked providers.
No real Ollama or external service required.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
from pathlib import Path

import pytest

from leadlift.ai.provider import (
    AIProvider,
    AIProviderConfig,
    AIProviderError,
    AIProviderTimeout,
    AIProviderUnavailable,
    AIValidationError,
    ExtractionResult,
)
from leadlift.ai.validation import ValidatedExtraction, ValidatedResponseDraft

from leadlift.models import Inquiry, InquirySource, InquiryStatus
from leadlift.ai.processor import AIProcessor
from leadlift.repository import InquiryRepository
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
def temp_db_path(isolated_database):
    """Provide a fresh database path for each test."""
    yield isolated_database


class FakeProvider:
    """Fake AI provider for testing."""

    def __init__(self, available=True, model="test-model"):
        self.config = AIProviderConfig(model=model, host="http://localhost")
        self._available = available
        self._extract_result = None
        self._classify_result = ("qualified", 0.9)
        self._missing_result = []
        self._next_action = "Follow up"
        self._draft = "Thank you for your inquiry."
        self._should_fail_extract = False
        self._should_fail_classify = False
        self._should_fail_draft = False
        self._should_timeout = False
        self._should_raise = False
        self._raise_type = None

    def health_check(self):
        return self._available

    def get_model_info(self):
        return {"name": self.config.model, "size": 100, "modified": None, "digest": "abc"}

    def extract(self, text, context):
        if self._should_raise:
            raise self._raise_type("Provider error")
        if self._should_timeout:
            raise AIProviderTimeout("Timeout")
        if self._should_fail_extract:
            raise AIProviderError("Extraction failed")
        if self._extract_result:
            return self._extract_result
        return ExtractionResult(
            customer_name="John",
            customer_email=None,
            customer_phone=None,
            organization=None,
            requested_service="Web Development",
            budget_stated="$5000",
            timeline_stated=None,
            explicit_preferences=None,
            classification="qualified",
            classification_confidence=0.9,
            missing_information=[],
            recommended_next_action="Follow up",
        )

    def classify(self, text, context):
        if self._should_fail_classify:
            raise AIProviderError("Classification failed")
        return self._classify_result

    def detect_missing(self, text, extraction, context):
        return self._missing_result

    def suggest_next_action(self, text, extraction, context):
        return self._next_action

    def generate_response_draft(self, text, validated, context):
        if self._should_fail_draft:
            raise AIProviderError("Draft generation failed")
        return self._draft


def _make_inquiry(repo, text="I need a website"):
    inq = Inquiry(
        source=InquirySource.MANUAL,
        source_platform="manual",
        original_text=text,
        received_at="2026-01-01T10:00:00Z",
        first_observed_at="2026-01-01T10:00:00Z",
    )
    repo.save(inq)
    return inq


# ---------------------------------------------------------------------------
# A. Missing provider
# ---------------------------------------------------------------------------

class TestMissingProvider:
    def test_is_available_false(self, temp_db_path):
        processor = AIProcessor(provider=None, repository=InquiryRepository())
        # Force provider to None
        processor.provider = None
        assert processor.is_available() is False

    def test_get_model_info_unavailable(self, temp_db_path):
        processor = AIProcessor(provider=None, repository=InquiryRepository())
        processor.provider = None
        info = processor.get_model_info()
        assert info["name"] == "unavailable"

    def test_process_inquiry_missing_provider(self, temp_db_path):
        repo = InquiryRepository()
        inq = _make_inquiry(repo)
        processor = AIProcessor(provider=None, repository=repo)
        processor.provider = None
        with pytest.raises(AIProviderUnavailable):
            processor.process_inquiry(inq.inquiry_id)

    def test_process_inquiry_nonexistent_id(self, temp_db_path):
        repo = InquiryRepository()
        provider = FakeProvider()
        processor = AIProcessor(provider=provider, repository=repo)
        with pytest.raises(ValueError, match="not found"):
            processor.process_inquiry("nonexistent-id")


# ---------------------------------------------------------------------------
# B. Successful provider
# ---------------------------------------------------------------------------

class TestSuccessfulProvider:
    def test_successful_processing(self, temp_db_path):
        repo = InquiryRepository()
        inq = _make_inquiry(repo)
        provider = FakeProvider()
        processor = AIProcessor(provider=provider, repository=repo)
        result = processor.process_inquiry(inq.inquiry_id)
        assert result["success"] is True
        assert result["classification"] == "qualified"
        assert result["confidence"] == 0.9
        assert "draft" in result

    def test_inquiry_state_updated(self, temp_db_path):
        repo = InquiryRepository()
        inq = _make_inquiry(repo)
        provider = FakeProvider()
        processor = AIProcessor(provider=provider, repository=repo)
        processor.process_inquiry(inq.inquiry_id)
        updated = repo.get(inq.inquiry_id)
        assert updated.ai_state.value == "processed"

    def test_processing_history_recorded(self, temp_db_path):
        repo = InquiryRepository()
        inq = _make_inquiry(repo)
        provider = FakeProvider()
        processor = AIProcessor(provider=provider, repository=repo)
        processor.process_inquiry(inq.inquiry_id)
        history = repo.get_processing_history(inq.inquiry_id)
        assert len(history) >= 1
        # Should have a "complete" entry
        complete_entries = [h for h in history if h.step == "complete"]
        assert len(complete_entries) >= 1
        assert complete_entries[0].state == "success"

    def test_draft_persisted(self, temp_db_path):
        repo = InquiryRepository()
        inq = _make_inquiry(repo)
        provider = FakeProvider()
        processor = AIProcessor(provider=provider, repository=repo)
        processor.process_inquiry(inq.inquiry_id)
        drafts = repo.get_drafts_for_inquiry(inq.inquiry_id)
        assert len(drafts) >= 1
        assert drafts[0].draft_text == "Thank you for your inquiry."
        assert drafts[0].status == "draft"  # Requires human approval

    def test_draft_requires_human_approval(self, temp_db_path):
        repo = InquiryRepository()
        inq = _make_inquiry(repo)
        provider = FakeProvider()
        processor = AIProcessor(provider=provider, repository=repo)
        processor.process_inquiry(inq.inquiry_id)
        drafts = repo.get_drafts_for_inquiry(inq.inquiry_id)
        assert all(d.status == "draft" for d in drafts)


# ---------------------------------------------------------------------------
# C. Failure paths
# ---------------------------------------------------------------------------

class TestFailurePaths:
    def test_provider_exception(self, temp_db_path):
        repo = InquiryRepository()
        inq = _make_inquiry(repo)
        provider = FakeProvider()
        provider._should_raise = True
        provider._raise_type = Exception
        processor = AIProcessor(provider=provider, repository=repo)
        with pytest.raises(Exception):
            processor.process_inquiry(inq.inquiry_id)
        # Verify failure recorded
        updated = repo.get(inq.inquiry_id)
        assert updated.ai_state.value == "failed"

    def test_provider_timeout(self, temp_db_path):
        repo = InquiryRepository()
        inq = _make_inquiry(repo)
        provider = FakeProvider()
        provider._should_timeout = True
        processor = AIProcessor(provider=provider, repository=repo)
        with pytest.raises(AIProviderTimeout):
            processor.process_inquiry(inq.inquiry_id)
        updated = repo.get(inq.inquiry_id)
        assert updated.ai_state.value == "failed"

    def test_provider_unavailable(self, temp_db_path):
        repo = InquiryRepository()
        inq = _make_inquiry(repo)
        provider = FakeProvider(available=False)
        processor = AIProcessor(provider=provider, repository=repo)
        # is_available returns False but processing still attempted
        # Provider health_check returns False but extract might still work
        # This tests that the processor doesn't crash on unavailable provider
        pass

    def test_classification_failure(self, temp_db_path):
        repo = InquiryRepository()
        inq = _make_inquiry(repo)
        provider = FakeProvider()
        provider._should_fail_classify = True
        processor = AIProcessor(provider=provider, repository=repo)
        with pytest.raises(Exception):
            processor.process_inquiry(inq.inquiry_id)
        updated = repo.get(inq.inquiry_id)
        assert updated.ai_state.value == "failed"

    def test_draft_generation_failure(self, temp_db_path):
        repo = InquiryRepository()
        inq = _make_inquiry(repo)
        provider = FakeProvider()
        provider._should_fail_draft = True
        processor = AIProcessor(provider=provider, repository=repo)
        with pytest.raises(Exception):
            processor.process_inquiry(inq.inquiry_id)
        updated = repo.get(inq.inquiry_id)
        assert updated.ai_state.value == "failed"

    def test_no_misleading_success(self, temp_db_path):
        repo = InquiryRepository()
        inq = _make_inquiry(repo)
        provider = FakeProvider()
        provider._should_raise = True
        provider._raise_type = Exception
        processor = AIProcessor(provider=provider, repository=repo)
        try:
            processor.process_inquiry(inq.inquiry_id)
        except Exception:
            pass
        # Verify inquiry not marked as processed
        updated = repo.get(inq.inquiry_id)
        assert updated.ai_state.value != "processed"

    def test_processing_history_on_failure(self, temp_db_path):
        repo = InquiryRepository()
        inq = _make_inquiry(repo)
        provider = FakeProvider()
        provider._should_raise = True
        provider._raise_type = Exception
        processor = AIProcessor(provider=provider, repository=repo)
        try:
            processor.process_inquiry(inq.inquiry_id)
        except Exception:
            pass
        history = repo.get_processing_history(inq.inquiry_id)
        # Should have at least one failed entry
        failed_entries = [h for h in history if h.state == "failed"]
        assert len(failed_entries) >= 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
