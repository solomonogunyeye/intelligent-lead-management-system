"""LEADLIFT — AI processing orchestrator."""

from __future__ import annotations

import time
from typing import Any, Optional

from leadlift.ai.provider import (
    AIProvider,
    AIProviderConfig,
    AIProviderError,
    AIProviderTimeout,
    AIProviderUnavailable,
    AIValidationError,
    ExtractionResult,
)
try:
    from leadlift.ai.ollama import create_provider
except ImportError:
    create_provider = None
from leadlift.ai.validation import validate_extraction, validate_response_draft, sanitize_for_storage
from leadlift.models import ClassificationSource
from leadlift.config import get_settings
from leadlift.models import (
    Inquiry,
    BusinessConfig,
    ProcessingHistoryEntry,
)
from leadlift.repository import InquiryRepository
from leadlift.logging_config import get_logger, safe_log
import logging

_logger = get_logger("ai.processor")


class AIProcessor:
    """Orchestrates AI processing for inquiries."""

    def __init__(
        self,
        provider: Optional[AIProvider] = None,
        repository: Optional[InquiryRepository] = None,
    ):
        self.settings = get_settings()
        self.provider = provider or (create_provider() if create_provider else None)
        self.repository = repository or InquiryRepository()

    def process_inquiry(self, inquiry_id: str) -> dict:
        """
        Process a single inquiry through the full AI pipeline.

        Returns a dict with processing results and metadata.
        """
        start_time = time.time()

        if self.provider is None:
            raise AIProviderUnavailable("AI provider is not available (Ollama not installed)")

        # Get the inquiry
        inquiry = self.repository.get(inquiry_id)
        if not inquiry:
            raise ValueError(f"Inquiry {inquiry_id} not found")

        # Record processing start
        self.repository.add_processing_entry(
            ProcessingHistoryEntry(
                inquiry_id=inquiry_id,
                step="extraction",
                provider=self.provider.config.model,
                state="started",
            )
        )

        try:
            # Get business context
            business_config = self.repository.get_business_config()
            context = {
                "business_services": business_config.services,
                "business_name": "LEADLIFT Business",  # Could be configurable
                "tone": business_config.communication_tone,
            }

            # Step 1: Extraction
            self.repository.add_processing_entry(
                ProcessingHistoryEntry(
                    inquiry_id=inquiry_id,
                    step="extraction",
                    provider=self.provider.config.model,
                    state="completed",
                    details={"duration_ms": int((time.time() - start_time) * 1000)},
                )
            )

            extraction = self.provider.extract(inquiry.original_text, context)
            extraction_start = time.time()

            # Step 2: Classification
            classification, confidence = self.provider.classify(inquiry.original_text, context)

            # Handle spam/low-quality: clear extraction fields that came from spam content
            is_spam = classification == "spam" and confidence >= 0.9
            is_low_quality = classification == "low-quality"
            
            if is_spam or is_low_quality:
                # Clear extracted fields that may contain spam content
                extraction.requested_service = None
                extraction.budget_stated = None
                extraction.timeline_stated = None
                extraction.customer_name = None
                extraction.customer_email = None
                extraction.customer_phone = None
                extraction.organization = None
                extraction.explicit_preferences = None
                # Keep classification and confidence for review
                extraction.classification = classification
                extraction.classification_confidence = confidence

            # Step 3: Missing information detection
            missing = self.provider.detect_missing(inquiry.original_text, extraction, context)

            # Step 4: Next action suggestion
            next_action = self.provider.suggest_next_action(inquiry.original_text, extraction, context)

            # Combine all results into ExtractionResult
            extraction.classification = classification
            extraction.classification_confidence = confidence
            extraction.missing_information = missing
            extraction.recommended_next_action = next_action

            # Step 5: Validate extraction
            validated = validate_extraction(extraction)

            # Step 6: Generate response draft
            draft = self.provider.generate_response_draft(inquiry.original_text, validated, context)
            validated_draft = validate_response_draft(draft)

            # Update inquiry with results
            self._update_inquiry_from_validation(inquiry, validated, validated_draft.draft)

            total_duration = int((time.time() - start_time) * 1000)

            # Record final processing result
            self.repository.add_processing_entry(
                ProcessingHistoryEntry(
                    inquiry_id=inquiry_id,
                    step="complete",
                    provider=self.provider.config.model,
                    state="success",
                    details=sanitize_for_storage({
                        "duration_ms": total_duration,
                        "extraction_ms": int((time.time() - extraction_start) * 1000),
                        "classification": classification,
                        "confidence": confidence,
                        "missing_count": len(missing),
                        "draft_length": len(validated_draft.draft),
                    }),
                )
            )

            # Update AI state
            self.repository.update_ai_state(inquiry_id, "processed")

            return {
                "success": True,
                "inquiry_id": inquiry_id,
                "extraction": validated.to_extraction_result(),
                "draft": validated_draft.draft,
                "classification": classification,
                "confidence": confidence,
                "missing_information": missing,
                "next_action": next_action,
                "processing_time_ms": total_duration,
            }

        except AIProviderTimeout as e:
            self._record_failure(inquiry_id, "timeout", str(e), time.time() - start_time)
            raise
        except AIProviderUnavailable as e:
            self._record_failure(inquiry_id, "unavailable", str(e), time.time() - start_time)
            raise
        except AIValidationError as e:
            self._record_failure(inquiry_id, "validation", str(e), time.time() - start_time)
            raise
        except AIProviderError as e:
            self._record_failure(inquiry_id, "error", str(e), time.time() - start_time)
            raise
        except Exception as e:
            self._record_failure(inquiry_id, "unexpected", str(e), time.time() - start_time)
            raise AIProviderError(f"Unexpected processing error: {e}")

    def _update_inquiry_from_validation(self, inquiry: Inquiry, validated: "ValidatedExtraction", draft: str) -> None:
        """Update inquiry with validated extraction results."""
        # Only update fields that were not already explicitly set by customer
        # Preserve original customer-stated information

        # Update inferred fields only
        if validated.budget_inferred and not inquiry.budget_inferred:
            inquiry.budget_inferred = validated.budget_inferred
        if validated.timeline_inferred and not inquiry.timeline_inferred:
            inquiry.timeline_inferred = validated.timeline_inferred

        # Update classification if not set or if AI provides higher confidence
        if validated.classification and (not inquiry.classification or validated.classification_confidence):
            inquiry.classification = validated.classification
            inquiry.classification_source = ClassificationSource.AI

        # Update missing information
        if validated.missing_information:
            from leadlift.models import MissingInformation
            inquiry.missing_information = [
                MissingInformation(field=f"missing_{i}", description=desc, importance="medium")
                for i, desc in enumerate(validated.missing_information)
            ]

        # Update recommended next action
        if validated.recommended_next_action:
            inquiry.recommended_next_action = validated.recommended_next_action

        # Apply updates
        self.repository.update(inquiry)

        # Store draft in response_drafts table
        from leadlift.models import ResponseDraft
        draft_obj = ResponseDraft(
            inquiry_id=inquiry.inquiry_id,
            draft_text=draft,
            generated_by="ai",
            model=self.provider.config.model,
            status="draft",
        )
        self.repository.save_draft(draft_obj)

        # Store reference in processing history for audit trail (not full draft)
        self.repository.add_processing_entry(
            ProcessingHistoryEntry(
                inquiry_id=inquiry.inquiry_id,
                step="response_draft",
                provider=self.provider.config.model,
                state="success",
                details={"draft_id": draft_obj.draft_id, "draft_length": len(draft)},
            )
        )

    def _record_failure(self, inquiry_id: str, step: str, error: str, duration: float) -> None:
        """Record a processing failure."""
        self.repository.add_processing_entry(
            ProcessingHistoryEntry(
                inquiry_id=inquiry_id,
                step=step,
                provider=self.provider.config.model,
                state="failed",
                error_message=error,
                details={"duration_ms": int(duration * 1000)},
            )
        )
        self.repository.update_ai_state(inquiry_id, "failed")

    def retry_failed(self, inquiry_id: str) -> dict:
        """Retry processing for a failed inquiry."""
        inquiry = self.repository.get(inquiry_id)
        if not inquiry:
            raise ValueError(f"Inquiry {inquiry_id} not found")

        # Reset AI state to pending
        self.repository.update_ai_state(inquiry_id, "pending")

        return self.process_inquiry(inquiry_id)

    def is_available(self) -> bool:
        """Check if AI processing is available."""
        if self.provider is None:
            return False
        return self.provider.health_check()

    def get_model_info(self) -> dict:
        """Get current model information."""
        if self.provider is None:
            return {"name": "unavailable", "size": 0, "modified": None, "digest": ""}
        return self.provider.get_model_info()


def process_inquiry(inquiry_id: str) -> dict:
    """Convenience function to process an inquiry."""
    processor = AIProcessor()
    return processor.process_inquiry(inquiry_id)