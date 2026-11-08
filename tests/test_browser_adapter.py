"""LEADLIFT — Browser adapter automated tests."""

import sys
import os
import asyncio
from datetime import datetime

sys.path.insert(0, ".")

import pytest

from pathlib import Path

from leadlift.models import Inquiry, InquirySource, MissingInformation
from leadlift.repository import InquiryRepository
from leadlift.adapters.browser import (
    BrowserAdapter,
    BrowserExtractionResult,
    BrowserIntakeResult,
    ExtractionStatus,
    extract_from_file,
    save_confirmed_inquiry,
)
from tests.test_support import reset_test_database


@pytest.fixture(autouse=True)
def clean_db(isolated_database):
    """Ensure fresh database for each test."""
    reset_test_database(isolated_database, Path(isolated_database).parent)
    yield
    reset_test_database(isolated_database, Path(isolated_database).parent)


FIXTURE_DIR = os.path.join(os.path.dirname(__file__), "..", "data")


class TestBrowserAdapter:
    """Test the browser adapter with local HTML fixtures."""

    @pytest.mark.asyncio
    async def test_extract_complete_inquiry(self):
        """Test extraction of a complete inquiry with all details."""
        result = await extract_from_file(os.path.join(FIXTURE_DIR, "test_msg_complete.html"))
        
        assert result.success is True
        assert result.extraction_result.status == ExtractionStatus.SUCCESS
        assert result.inquiry is not None
        
        inquiry = result.inquiry
        assert inquiry.customer_name == "Sarah Johnson"
        assert inquiry.customer_email == "sarah@artisanjewelry.com"
        assert inquiry.organization == "Artisan Jewelry Co."
        assert inquiry.requested_service == "E-Commerce"  # Text says "e-commerce website"
        assert inquiry.budget_stated is not None
        assert inquiry.timeline_stated is not None
        assert inquiry.source == InquirySource.BROWSER
        assert inquiry.original_text is not None
        assert len(inquiry.original_text) > 50
        
    @pytest.mark.asyncio
    async def test_extract_inquiry_with_missing_info(self):
        """Test extraction of inquiry with missing information."""
        result = await extract_from_file(os.path.join(FIXTURE_DIR, "test_msg_missing_phone.html"))
        
        assert result.success is True
        assert result.extraction_result.status == ExtractionStatus.SUCCESS
        assert result.inquiry is not None
        
        inquiry = result.inquiry
        assert inquiry.customer_name == "Mike Chen"
        assert inquiry.customer_email == "mike@denverplumbing.com"
        assert inquiry.organization == "Denver Plumbing Pros"
        assert inquiry.requested_service == "SEO"
        assert inquiry.budget_stated is not None
        # Phone should be missing
        assert inquiry.customer_phone is None
        # Should have missing info for phone
        missing_fields = [m.field for m in inquiry.missing_information]
        assert "customer_phone" in missing_fields

    @pytest.mark.asyncio
    async def test_adapter_extract_specific_message(self):
        """Test adapter extracting from specific HTML content."""
        html = """
        <html><body>
            <div class="message">
                From: Jane Doe <jane@test.com><br>
                Company: Test Corp<br>
                We need a website. Budget $5000, timeline 1 month.
            </div>
        </body></html>
        """
        
        async with BrowserAdapter() as adapter:
            result = await adapter.extract_from_html(html)
            
        assert result.status == ExtractionStatus.SUCCESS
        assert result.inquiry is not None
        assert result.inquiry.customer_name == "Jane Doe"
        assert result.inquiry.customer_email == "jane@test.com"
        assert result.inquiry.organization == "Test Corp"
        assert result.inquiry.requested_service == "Web Development"
        assert result.inquiry.budget_stated == "$5000"
        assert result.inquiry.timeline_stated == "1 month"

    @pytest.mark.asyncio
    async def test_adapter_extract_missing_phone(self):
        """Test extraction when phone is missing."""
        html = """
        <html><body>
            <div class="message">
                From: John Smith <john@example.com><br>
                Company: Acme Inc<br>
                We need SEO services. Budget $2000/month.
            </div>
        </body></html>
        """
        
        async with BrowserAdapter() as adapter:
            result = await adapter.extract_from_html(html)
            
        assert result.status == ExtractionStatus.SUCCESS
        assert result.inquiry is not None
        assert result.inquiry.customer_name == "John Smith"
        assert result.inquiry.customer_email == "john@example.com"
        assert result.inquiry.customer_phone is None  # Missing
        # Should have missing info for phone
        missing_fields = [m.field for m in result.inquiry.missing_information]
        assert "customer_phone" in missing_fields

    @pytest.mark.asyncio
    async def test_adapter_non_inquiry_message(self):
        """Test that non-inquiry messages are rejected."""
        result = await extract_from_file(os.path.join(FIXTURE_DIR, "test_msg_non_inquiry.html"))
        
        assert result.success is False
        assert result.extraction_result.status == ExtractionStatus.NO_INQUIRY
        assert result.inquiry is None

    @pytest.mark.asyncio
    async def test_adapter_spam_message(self):
        """Test that spam-like messages are rejected."""
        result = await extract_from_file(os.path.join(FIXTURE_DIR, "test_msg_spam.html"))
        
        assert result.success is False
        assert result.extraction_result.status == ExtractionStatus.NO_INQUIRY
        assert result.inquiry is None

    @pytest.mark.asyncio
    async def test_adapter_insufficient_content(self):
        """Test that insufficient content is rejected."""
        html = """
        <html><body>
            <div class="message">
                Hi
            </div>
        </body></html>
        """
        
        async with BrowserAdapter() as adapter:
            result = await adapter.extract_from_html(html)
            
        assert result.status == ExtractionStatus.INSUFFICIENT
        assert result.inquiry is None

    @pytest.mark.asyncio
    async def test_duplicate_detection(self, clean_db):
        """Test that duplicate extraction doesn't create duplicate records."""
        fixture_path = os.path.join(FIXTURE_DIR, "test_msg_duplicate.html")
        
        result1 = await extract_from_file(fixture_path)
        assert result1.success is True
        
        # Save the inquiry
        from leadlift.adapters.browser import save_confirmed_inquiry
        saved = await save_confirmed_inquiry(result1.inquiry)
        
        # Second extraction of same content
        result2 = await extract_from_file(fixture_path)
        assert result2.success is True
        
        # Check duplicate detection
        from leadlift.repository import get_repository
        repo = InquiryRepository()
        duplicates = repo.find_duplicates(
            customer_email=result2.inquiry.customer_email,
            text_fingerprint=result2.inquiry.text_fingerprint,
            conversation_id=result2.inquiry.source_conversation_id,
            message_id=result2.inquiry.source_message_id,
        )
        
        assert len(duplicates) >= 1
        assert duplicates[0].inquiry_id == saved.inquiry_id

    @pytest.mark.asyncio
    async def test_user_cancellation_no_save(self, clean_db):
        """Test that user can cancel without saving."""
        fixture_path = os.path.join(FIXTURE_DIR, "test_msg_duplicate.html")
        
        result = await extract_from_file(fixture_path)
        assert result.success is True
        
        # User cancels - don't call save_confirmed_inquiry
        # Verify nothing was saved
        from leadlift.repository import get_repository
        repo = InquiryRepository()
        all_inquiries = repo.get_all()
        
        # Should not have our inquiry
        found = any(i.customer_email == "repeat@test.com" for i in all_inquiries)
        assert not found

    @pytest.mark.asyncio
    async def test_explicit_confirmation_saves(self, clean_db):
        """Test that explicit confirmation saves the inquiry."""
        fixture_path = os.path.join(FIXTURE_DIR, "test_msg_confirm.html")
        
        result = await extract_from_file(fixture_path)
        assert result.success is True
        
        # User confirms - save the inquiry
        from leadlift.adapters.browser import save_confirmed_inquiry
        saved = await save_confirmed_inquiry(result.inquiry)
        
        # Verify it was saved
        from leadlift.repository import get_repository
        repo = InquiryRepository()
        retrieved = repo.get(saved.inquiry_id)
        
        assert retrieved is not None
        assert retrieved.customer_email == "confirm@test.com"
        assert retrieved.source == InquirySource.BROWSER

    @pytest.mark.asyncio
    async def test_original_text_preserved(self):
        """Test that original message text is preserved."""
        original_text = "We need a custom web application for our business. Budget $25000, timeline 3 months."
        html = f"""
        <html><body>
            <div class="message">
                From: Test User <test@test.com><br>
                Company: Test Corp<br>
                {original_text}
            </div>
        </body></html>
        """
        
        async with BrowserAdapter() as adapter:
            result = await adapter.extract_from_html(html)
            
        assert result.status == ExtractionStatus.SUCCESS
        assert result.inquiry is not None
        # Original text should be preserved (truncated to 10000 chars)
        assert original_text in result.inquiry.original_text

    @pytest.mark.asyncio
    async def test_missing_service_identified(self):
        """Test that missing service is identified in missing_information."""
        fixture_path = os.path.join(FIXTURE_DIR, "test_msg_incomplete.html")
        
        result = await extract_from_file(fixture_path)
        
        # Should either be insufficient or have missing service
        if result.success:
            missing_fields = [m.field for m in result.inquiry.missing_information]
            assert "requested_service" in missing_fields

    @pytest.mark.asyncio
    async def test_inference_not_stated_as_fact(self):
        """Test that inferred information is not stored as explicit."""
        html = """
        <html><body>
            <div class="message">
                From: Test User <test@test.com><br>
                We need a website built.
            </div>
        </body></html>
        """
        
        async with BrowserAdapter() as adapter:
            result = await adapter.extract_from_html(html)
            
        assert result.status == ExtractionStatus.SUCCESS
        assert result.inquiry is not None
        # Budget and timeline should be None (not inferred)
        assert result.inquiry.budget_stated is None
        assert result.inquiry.timeline_stated is None
        assert result.inquiry.budget_inferred is None
        assert result.inquiry.timeline_inferred is None

    @pytest.mark.asyncio
    async def test_classification_not_confident(self):
        """Test that ambiguous content gets low confidence classification."""
        fixture_path = os.path.join(FIXTURE_DIR, "test_msg_incomplete.html")
        
        result = await extract_from_file(fixture_path)
        
        # Should either be NO_INQUIRY or INSUFFICIENT
        assert result.extraction_result.status in (ExtractionStatus.NO_INQUIRY, ExtractionStatus.INSUFFICIENT)

    @pytest.mark.asyncio
    async def test_repeated_extraction_no_duplicates(self, clean_db):
        """Test that repeated extraction of same message doesn't create duplicates."""
        fixture_path = os.path.join(FIXTURE_DIR, "test_msg_duplicate.html")
        
        from leadlift.adapters.browser import save_confirmed_inquiry
        
        # Extract and save first time
        result1 = await extract_from_file(fixture_path)
        await save_confirmed_inquiry(result1.inquiry)
        
        # Extract again
        result2 = await extract_from_file(fixture_path)
        await save_confirmed_inquiry(result2.inquiry)
        
        # Extract third time
        result3 = await extract_from_file(fixture_path)
        await save_confirmed_inquiry(result3.inquiry)
        
        # Should only have one record
        from leadlift.repository import get_repository
        repo = InquiryRepository()
        all_inquiries = repo.get_all()
        
        repeat_count = sum(1 for i in all_inquiries if i.customer_email == "repeat@test.com")
        assert repeat_count == 1


class TestBrowserAdapterEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_empty_html(self):
        """Test handling of empty HTML."""
        html = "<html><body></body></html>"
        
        async with BrowserAdapter() as adapter:
            result = await adapter.extract_from_html(html)
            
        assert result.status == ExtractionStatus.INSUFFICIENT

    @pytest.mark.asyncio
    async def test_malformed_html(self):
        """Test handling of malformed HTML."""
        html = "<html><body><div>Unclosed tags"
        
        async with BrowserAdapter() as adapter:
            # Should not crash
            result = await adapter.extract_from_html(html)
            
        # Should handle gracefully
        assert result.status in (ExtractionStatus.ERROR, ExtractionStatus.INSUFFICIENT)

    @pytest.mark.asyncio
    async def test_special_characters(self):
        """Test handling of special characters in content."""
        result = await extract_from_file(os.path.join(FIXTURE_DIR, "test_msg_special.html"))
        
        assert result.success is True
        assert result.extraction_result.status == ExtractionStatus.SUCCESS
        assert result.inquiry is not None
        # Name should be extracted
        assert result.inquiry.customer_name is not None
        assert ("José" in result.inquiry.customer_name or "María" in result.inquiry.customer_name or "Jose" in result.inquiry.customer_name)
        assert result.inquiry.customer_email == "jose@test.com"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])