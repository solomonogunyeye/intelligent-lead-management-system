"""LEADLIFT — Website contact-form inbound intake adapter.

Phase 3G: Generic website contact-form webhook intake.

Security considerations:
- Webhook payloads validated for structure before processing
- Shared-secret authentication via X-Webhook-Secret header (or payload field)
- Constant-time comparison
- Fail-closed on missing security configuration
- Malformed payload handling
- Replay/duplicate protection via submission ID
- No outbound messaging; read-only webhook processing
- Sensitive data never logged
- No URL fetching, no HTML/JS execution
- Reasonable field and payload size limits
"""

from __future__ import annotations
import uuid
# ... (existing imports)

import hashlib
import hmac
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set
from dataclasses import dataclass, field
from enum import Enum
from urllib.parse import urlparse

from leadlift.models import (
    Inquiry,
    InquirySource,
    CSVImportResult,
)
from leadlift.repository import InquiryRepository
from leadlift.adapters.base import BaseAdapter
from leadlift.logging_config import get_logger, safe_log
import logging

_logger = get_logger("adapters.website_form")


class WebsiteFormMessageStatus(str, Enum):
    """Status of website form submission processing."""
    RECEIVED = "received"
    EXTRACTED = "extracted"
    NO_INQUIRY = "no_inquiry"
    INSUFFICIENT = "insufficient"
    INVALID = "invalid"
    DUPLICATE = "duplicate"


@dataclass
class WebsiteFormSubmission:
    """Normalized website form submission."""
    submission_id: str
    form_id: Optional[str]
    website: Optional[str]
    customer_name: Optional[str]
    customer_email: Optional[str]
    customer_phone: Optional[str]
    subject: Optional[str]
    message: str
    submitted_at: Optional[str]
    page_url: Optional[str]
    consent: Optional[bool]
    custom_fields: Optional[Dict[str, Any]]
    raw_payload: Optional[Dict[str, Any]] = None


@dataclass
class WebsiteFormIntakeResult:
    """Result of website form inbound intake."""
    success: bool
    status: str = "received"
    submission_id: Optional[str] = None
    submission_id_source: str = "unknown"  # "provider" or "generated"
    form_id: Optional[str] = None
    website: Optional[str] = None
    extracted_inquiry: Optional[Inquiry] = None
    errors: List[dict] = field(default_factory=list)
    warnings: List[dict] = field(default_factory=list)
    is_duplicate: bool = False
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


class WebsiteFormIntakeAdapter(BaseAdapter):
    """Website contact-form intake adapter.

    Provider-neutral: accepts a documented structured JSON payload.

    Features:
    - Shared-secret authentication (constant-time comparison)
    - Payload structure validation
    - Field and payload size limits
    - Email format validation
    - URL validation (without fetching)
    - HTML/script content handling (non-executing)
    - Duplicate detection via submission ID
    - Review workflow integration
    - No outbound messaging; read-only processing only
    """

    # Required fields
    REQUIRED_FIELDS = {"message"}

    # Optional recognized fields
    OPTIONAL_FIELDS = {
        "submission_id", "form_id", "website", "customer_name",
        "customer_email", "customer_phone", "subject", "submitted_at",
        "page_url", "consent", "custom_fields",
    }

    # Maximum field length (configurable via settings)
    DEFAULT_MAX_FIELD_LENGTH = 10000
    DEFAULT_MAX_PAYLOAD_BYTES = 65536

    def __init__(self, settings=None):
        super().__init__(settings)
        self.settings = settings or {}
        self._seen_submission_ids: Set[str] = set()
        self._repository: Optional[InquiryRepository] = None

    @property
    def repository(self) -> InquiryRepository:
        if self._repository is None:
            self._repository = InquiryRepository()
        return self._repository

    # ------------------------------------------------------------------
    # Secret validation
    # ------------------------------------------------------------------

    def validate_secret(self, provided_secret: Optional[str], configured_secret: Optional[str]) -> bool:
        """Validate webhook secret using constant-time comparison.

        Fail-closed when configured secret is missing.
        """
        if not configured_secret:
            return False
        if not provided_secret:
            return False
        return hmac.compare_digest(provided_secret, configured_secret)

    # Alias for consistency
    def validate_signature(self, payload_body: bytes, signature_header: Optional[str], app_secret: Optional[str]) -> bool:
        """Alias for validate_secret for API consistency."""
        return self.validate_secret(signature_header, app_secret)

    # ------------------------------------------------------------------
    # Webhook verification (GET) - not used by this adapter
    # ------------------------------------------------------------------

    def verify_webhook(self, mode: str, token: str, challenge: str, app_secret: Optional[str] = None) -> Optional[str]:
        """Website forms do not use GET verification handshake."""
        return None

    # ------------------------------------------------------------------
    # Payload processing
    # ------------------------------------------------------------------

    def process_webhook_payload(self, payload: Dict[str, Any], secret: Optional[str] = None, configured_secret: Optional[str] = None) -> WebsiteFormIntakeResult:
        """Process a website contact-form webhook payload.

        Args:
            payload: The JSON payload from the website form
            secret: The secret provided in the request (header or payload)
            configured_secret: The configured secret from settings

        Returns:
            WebsiteFormIntakeResult with status and optional extracted inquiry
        """
        # Determine configured secret
        if configured_secret is None:
            configured_secret = self.settings.get("website_form_webhook_secret", "")

        # Validate secret (fail-closed)
        if not self.validate_secret(secret, configured_secret):
            safe_log(_logger, logging.WARNING, "Website form webhook authentication failed")
            return WebsiteFormIntakeResult(
                success=False,
                status=WebsiteFormMessageStatus.INVALID.value,
                is_invalid=True,
                errors=[{"field": "authentication", "message": "Invalid or missing webhook secret"}],
            )

        # Validate payload is a dict
        if not isinstance(payload, dict):
            return WebsiteFormIntakeResult(
                success=False,
                status=WebsiteFormMessageStatus.INVALID.value,
                is_invalid=True,
                errors=[{"field": "payload", "message": "Payload must be a JSON object"}],
            )

        # Size limits
        max_payload_bytes = self.settings.get("website_form_max_payload_bytes", self.DEFAULT_MAX_PAYLOAD_BYTES)
        payload_str = json.dumps(payload)
        if len(payload_str.encode("utf-8")) > max_payload_bytes:
            return WebsiteFormIntakeResult(
                success=False,
                status=WebsiteFormMessageStatus.INVALID.value,
                is_invalid=True,
                errors=[{"field": "payload", "message": f"Payload exceeds maximum size of {max_payload_bytes} bytes"}],
            )

        # Extract and validate fields
        max_field_length = self.settings.get("website_form_max_field_length", self.DEFAULT_MAX_FIELD_LENGTH)

        # Required field: message
        message = payload.get("message")
        if not message or not isinstance(message, str) or not message.strip():
            return WebsiteFormIntakeResult(
                success=False,
                status=WebsiteFormMessageStatus.INVALID.value,
                is_invalid=True,
                errors=[{"field": "message", "message": "Message is required and must be non-empty"}],
            )

        # Truncate oversized fields (but report as warning)
        warnings = []
        if len(message) > max_field_length:
            message = message[:max_field_length]
            warnings.append({
                "field": "message",
                "message": f"Message truncated to {max_field_length} characters",
                "severity": "warning",
            })

        # Extract optional fields
        submission_id = payload.get("submission_id")
        form_id = payload.get("form_id")
        website = payload.get("website")
        customer_name = payload.get("customer_name")
        customer_email = payload.get("customer_email")
        customer_phone = payload.get("customer_phone")
        subject = payload.get("subject")
        submitted_at = payload.get("submitted_at")
        page_url = payload.get("page_url")
        consent = payload.get("consent")
        custom_fields = payload.get("custom_fields")

        # Validate email format if provided
        if customer_email is not None and customer_email != "":
            if not self._is_valid_email(customer_email):
                return WebsiteFormIntakeResult(
                    success=False,
                    status=WebsiteFormMessageStatus.INVALID.value,
                    is_invalid=True,
                    errors=[{"field": "customer_email", "message": "Invalid email format"}],
                )

        # Validate URL if provided (without fetching)
        if page_url is not None and page_url != "":
            if not self._is_valid_url(page_url):
                return WebsiteFormIntakeResult(
                    success=False,
                    status=WebsiteFormMessageStatus.INVALID.value,
                    is_invalid=True,
                    errors=[{"field": "page_url", "message": "Invalid URL format"}],
                )
            if len(str(page_url)) > max_field_length:
                page_url = str(page_url)[:max_field_length]
                warnings.append({
                    "field": "page_url",
                    "message": f"page_url truncated to {max_field_length} characters",
                    "severity": "warning",
                })

        # Validate website if provided
        if website is not None and website != "":
            if not self._is_valid_hostname(website):
                return WebsiteFormIntakeResult(
                    success=False,
                    status=WebsiteFormMessageStatus.INVALID.value,
                    is_invalid=True,
                    errors=[{"field": "website", "message": "Invalid website/domain format"}],
                )
            if len(str(website)) > max_field_length:
                website = str(website)[:max_field_length]
                warnings.append({
                    "field": "website",
                    "message": f"website truncated to {max_field_length} characters",
                    "severity": "warning",
                })

        # Truncate form_id and submission_id
        if isinstance(form_id, str) and len(form_id) > max_field_length:
            form_id = form_id[:max_field_length]
            warnings.append({
                "field": "form_id",
                "message": f"form_id truncated to {max_field_length} characters",
                "severity": "warning",
            })
        if isinstance(submission_id, str) and len(submission_id) > max_field_length:
            submission_id = submission_id[:max_field_length]
            warnings.append({
                "field": "submission_id",
                "message": f"submission_id truncated to {max_field_length} characters",
                "severity": "warning",
            })

        # Validate custom_fields type
        if custom_fields is not None and not isinstance(custom_fields, dict):
            return WebsiteFormIntakeResult(
                success=False,
                status=WebsiteFormMessageStatus.INVALID.value,
                is_invalid=True,
                errors=[{"field": "custom_fields", "message": "custom_fields must be an object/dict"}],
            )

        # Validate consent type
        if consent is not None and not isinstance(consent, bool):
            return WebsiteFormIntakeResult(
                success=False,
                status=WebsiteFormMessageStatus.INVALID.value,
                is_invalid=True,
                errors=[{"field": "consent", "message": "consent must be a boolean"}],
            )

        # Truncate oversized optional fields
        if isinstance(customer_name, str) and len(customer_name) > max_field_length:
            customer_name = customer_name[:max_field_length]
            warnings.append({
                "field": "customer_name",
                "message": f"customer_name truncated to {max_field_length} characters",
                "severity": "warning",
            })
        if isinstance(subject, str) and len(subject) > max_field_length:
            subject = subject[:max_field_length]
            warnings.append({
                "field": "subject",
                "message": f"subject truncated to {max_field_length} characters",
                "severity": "warning",
            })
        if isinstance(customer_phone, str) and len(customer_phone) > max_field_length:
            customer_phone = customer_phone[:max_field_length]
            warnings.append({
                "field": "customer_phone",
                "message": f"customer_phone truncated to {max_field_length} characters",
                "severity": "warning",
            })
        if isinstance(customer_email, str) and len(customer_email) > max_field_length:
            customer_email = customer_email[:max_field_length]
            warnings.append({
                "field": "customer_email",
                "message": f"customer_email truncated to {max_field_length} characters",
                "severity": "warning",
            })

        # Generate submission_id if not provided
        provider_supplied = bool(submission_id)
        if not submission_id:
            submission_id = self._generate_submission_id(payload)
            warnings.append({
                "field": "submission_id",
                "message": "No provider submission_id provided; generated internal ID (not reliable for cross-delivery dedup)",
                "severity": "info",
            })

        # Check for duplicate submission_id
        if submission_id in self._seen_submission_ids:
            safe_log(_logger, logging.WARNING, "Duplicate submission_id replay: %s", submission_id)
            return WebsiteFormIntakeResult(
                success=True,
                status=WebsiteFormMessageStatus.DUPLICATE.value,
                submission_id=submission_id,
                form_id=form_id,
                website=website,
                is_duplicate=True,
                errors=[],
                warnings=[{
                    "field": "duplicate_detection",
                    "message": f"Duplicate submission_id replay: {submission_id}",
                    "severity": "warning",
                }],
            )

        # Track this submission_id
        self._seen_submission_ids.add(submission_id)

        # Database-level duplicate check
        if self._is_duplicate_submission(submission_id):
            return WebsiteFormIntakeResult(
                success=True,
                status=WebsiteFormMessageStatus.DUPLICATE.value,
                submission_id=submission_id,
                form_id=form_id,
                website=website,
                is_duplicate=True,
                errors=[],
                warnings=[{
                    "field": "duplicate_detection",
                    "message": f"Duplicate submission_id: {submission_id}",
                    "severity": "warning",
                }],
            )

        # Handle HTML/script content (sanitize by keeping as text, not executing)
        # The message is stored as-is (text), not rendered as HTML
        # No additional processing needed - original_text preserves it

        # Build normalized submission
        submission = WebsiteFormSubmission(
            submission_id=submission_id,
            form_id=form_id,
            website=website,
            customer_name=customer_name,
            customer_email=customer_email,
            customer_phone=customer_phone,
            subject=subject,
            message=message.strip(),
            submitted_at=submitted_at or datetime.now(timezone.utc).isoformat(),
            page_url=page_url,
            consent=consent if isinstance(consent, bool) else None,
            custom_fields=custom_fields if isinstance(custom_fields, dict) else None,
            raw_payload=payload,
        )

        # Map to inquiry
        inquiry = self._map_to_inquiry(submission)

        return WebsiteFormIntakeResult(
            success=True,
            status=WebsiteFormMessageStatus.EXTRACTED.value,
            submission_id=submission_id,
            form_id=form_id,
            website=website,
            extracted_inquiry=inquiry,
            warnings=warnings,
        )

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    def _is_valid_email(self, email: str) -> bool:
        """Basic email format validation (syntax only, not ownership)."""
        if not isinstance(email, str) or len(email) > 254:
            return False
        # Basic RFC 5322-ish pattern
        pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        return bool(re.match(pattern, email.strip()))

    def _is_valid_url(self, url: str) -> bool:
        """Basic URL format validation (syntax only, no fetching)."""
        if not isinstance(url, str) or len(url) > 2048:
            return False
        try:
            parsed = urlparse(url.strip())
            return parsed.scheme in ("http", "https") and bool(parsed.netloc)
        except Exception:
            return False

    def _is_valid_hostname(self, hostname: str) -> bool:
        """Basic hostname/domain format validation."""
        if not isinstance(hostname, str) or len(hostname) > 253:
            return False
        # Allow domains with or without protocol
        h = hostname.strip().lower()
        if h.startswith("http://") or h.startswith("https://"):
            parsed = urlparse(h)
            h = parsed.netloc or parsed.path
        # Basic hostname pattern
        pattern = r'^[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?(\.[a-zA-Z0-9]([a-zA-Z0-9-]*[a-zA-Z0-9])?)*$'
        return bool(re.match(pattern, h))

    def _generate_submission_id(self, payload: Dict[str, Any]) -> str:
        """Generate a cryptographically random internal submission ID.

        This is NOT based on payload content, so identical submissions
        from different customers will not be falsely classified as duplicates.
        Note: submissions without a provider-supplied ID cannot be reliably
        deduplicated across webhook retries or application restarts.
        """
        return "wf_" + uuid.uuid4().hex

    # ------------------------------------------------------------------
    # Duplicate detection
    # ------------------------------------------------------------------

    def _is_duplicate_submission(self, submission_id: str) -> bool:
        """Check if this submission ID was seen before (database-level)."""
        if not submission_id:
            return False
        duplicates = self.repository.find_duplicates(
            customer_email=None,
            text_fingerprint=None,
            conversation_id=None,
            message_id=submission_id,
        )
        return len(duplicates) > 0

    # ------------------------------------------------------------------
    # Inquiry mapping
    # ------------------------------------------------------------------

    def _map_to_inquiry(self, submission: WebsiteFormSubmission) -> Inquiry:
        """Map website form submission to an Inquiry object."""
        # Build original text from subject + message
        if submission.subject:
            original_text = f"Subject: {submission.subject}\n\n{submission.message}"
        else:
            original_text = submission.message

        # Determine source account (website/domain)
        source_account = submission.website or submission.form_id or "website_form"

        # Build recommended next action
        recommended = "Manual review required for website form inquiries"

        return Inquiry(
            source=InquirySource.WEBSITE_FORM,
            source_platform="website_form",
            source_account=source_account,
            source_conversation_id=submission.submission_id,
            source_message_id=submission.submission_id,
            customer_name=submission.customer_name,
            customer_email=submission.customer_email,
            customer_phone=submission.customer_phone,
            original_text=original_text,
            received_at=submission.submitted_at or datetime.now(timezone.utc).isoformat(),
            first_observed_at=submission.submitted_at or datetime.now(timezone.utc).isoformat(),
            recommended_next_action=recommended,
        )

    # ------------------------------------------------------------------
    # Review workflow - save confirmed inquiry
    # ------------------------------------------------------------------

    def save_confirmed_inquiry(self, inquiry: Inquiry) -> Inquiry:
        """Persist a confirmed inquiry to the database.

        Uses atomic insertion to prevent concurrent duplicates.
        """
        saved, inserted = self.repository.save_if_not_duplicate(inquiry)
        return saved

    def _check_for_duplicates(self, inquiry: Inquiry) -> List[Inquiry]:
        """Check for existing inquiries that match the new inquiry."""
        return self.repository.find_duplicates(
            customer_email=inquiry.customer_email,
            text_fingerprint=None,
            conversation_id=inquiry.source_conversation_id,
            message_id=inquiry.source_message_id,
        )

    # ------------------------------------------------------------------
    # Convenience entry
    # ------------------------------------------------------------------

    def process(self, payload: Dict[str, Any], secret: Optional[str] = None, configured_secret: Optional[str] = None) -> WebsiteFormIntakeResult:
        """Synchronous entry point for webhook processing."""
        return self.process_webhook_payload(payload, secret=secret, configured_secret=configured_secret)
