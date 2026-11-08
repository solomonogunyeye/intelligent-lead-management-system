"""LEADLIFT — CSV adapter for inquiry intake.

Supports:
- Required-column validation (source, original_text, received_at)
- Missing-value handling for optional fields
- Type normalization (dates, emails, phones)
- Duplicate detection (by email, source_message_id, source_conversation_id)
- Row-level error and warning reporting
- Import preview and safe rejection of malformed rows
- Preserves original inquiry text and all source metadata
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from leadlift.models import (
    CSVImportResult,
    CSVImportRowError,
    FollowUpTask,
    Inquiry,
    InquirySource,
    MissingInformation,
)
from leadlift.repository import get_repository


class CSVAdapter:
    """
    Adapter for processing CSV inquiry imports.

    Expected CSV columns (case-insensitive):
    - source (required): "manual", "email", "web", etc.
    - original_text (required): The customer's full inquiry
    - received_at (required): ISO 8601 timestamp
    - customer_name (optional)
    - customer_email (optional)
    - customer_phone (optional)
    - organization (optional)
    - requested_service (optional)
    - source_platform (optional)
    - source_account (optional)
    - source_conversation_id (optional)
    - source_message_id (optional)
    - budget_stated (optional)
    - budget_inferred (optional)
    - timeline_stated (optional)
    - timeline_inferred (optional)
    - classification (optional)
    - missing_information_json (optional)
    - recommended_next_action (optional)
    - priority_score (optional)
    - priority_explanation (optional)
    - status (optional)
    """

    def __init__(self, settings=None):
        self.settings = settings or {}
        self._field_map: dict | None = None
        self._original_fields: list | None = None
        self._normalized_headers: list | None = None

    def process(self, csv_content: str, preview: bool = False) -> CSVImportResult:
        """
        Process CSV content.

        Returns a CSVImportResult with counts of accepted/rejected/duplicate rows,
        detailed errors/warnings, and optionally a preview of accepted rows.
        """
        if not csv_content or not csv_content.strip():
            return CSVImportResult(
                accepted=0,
                rejected=1,
                duplicates=0,
                errors=[{"row_number": 0, "message": "CSV content is empty"}],
                warnings=[],
            )

        # Parse CSV
        reader = csv.DictReader(io.StringIO(csv_content))
        self._original_fields = reader.fieldnames or []
        if not self._original_fields:
            return CSVImportResult(
                accepted=0,
                rejected=1,
                duplicates=0,
                errors=[{"row_number": 0, "message": "CSV has no headers"}],
                warnings=[],
            )

        # Normalize header names (case-insensitive)
        self._normalized_headers = [
            h.strip().lower().replace(" ", "_") for h in self._original_fields
        ]
        field_map: dict = {}
        for i, h in enumerate(self._original_fields):
            field_map[i] = self._normalized_headers[i]

        # Required fields (case-insensitive check)
        required_normalized = {"source", "original_text", "received_at"}
        missing_required = required_normalized - set(self._normalized_headers)
        if missing_required:
            return CSVImportResult(
                accepted=0,
                rejected=1,
                duplicates=0,
                errors=[{
                    "row_number": 0,
                    "message": f"Missing required columns: {', '.join(missing_required)}",
                    "severity": "error",
                }],
                warnings=[],
            )

        # Process rows
        result = CSVImportResult(accepted=0, rejected=0, duplicates=0, errors=[], warnings=[])
        repository = get_repository()

        # Track seen identifiers within this batch for duplicate detection
        seen_emails = set()
        seen_message_ids = set()
        seen_conversation_ids = set()
        seen_text_fingerprints = set()

        # Prepare for preview if requested
        preview_rows = [] if preview else None

        for i, row in enumerate(reader, start=2):  # row 2 = first data row
            row_result = self._process_row(
                row, i, preview_rows,
                seen_emails, seen_message_ids, seen_conversation_ids, seen_text_fingerprints,
            )

            # Handle duplicates
            if row_result.get("duplicate"):
                result.duplicates += 1
                if row_result.get("warning"):
                    result.warnings.append(row_result["warning"])
                continue

            # Handle rejections
            if row_result.get("rejected"):
                result.rejected += 1
                result.errors.extend(row_result.get("errors", []))
                continue

            # Accept the inquiry
            result.accepted += 1
            # Track identifiers for within-batch duplicate detection
            accepted_inquiry = row_result["inquiry"]
            if accepted_inquiry.customer_email:
                seen_emails.add(accepted_inquiry.customer_email)
            if accepted_inquiry.source_message_id:
                seen_message_ids.add(accepted_inquiry.source_message_id)
            if accepted_inquiry.source_conversation_id:
                seen_conversation_ids.add(accepted_inquiry.source_conversation_id)
            if accepted_inquiry.text_fingerprint:
                seen_text_fingerprints.add(accepted_inquiry.text_fingerprint)
            if preview and preview_rows is not None:
                preview_rows.append(accepted_inquiry)
            else:
                # Save to database for non-preview mode
                repository.save(accepted_inquiry)

            # Update results with details
            if row_result.get("warnings"):
                for w in row_result["warnings"]:
                    result.warnings.append({
                        "row_number": i,
                        "field": w.get("field"),
                        "message": w.get("message"),
                        "severity": w.get("severity"),
                    })

        if preview_rows is not None:
            result.preview = preview_rows  # type: ignore

        return result

    def _process_row(
        self,
        row: dict,
        row_number: int,
        preview_rows: Optional[List] = None,
        seen_emails: Optional[set] = None,
        seen_message_ids: Optional[set] = None,
        seen_conversation_ids: Optional[set] = None,
        seen_text_fingerprints: Optional[set] = None,
    ) -> dict:
        """
        Process a single CSV row and return result structure.

        Returns a dict with keys:
        - duplicate: bool (whether this is a duplicate)
        - rejected: bool (whether this row was rejected)
        - inquiry: Inquiry (if accepted)
        - warnings: list of warning dicts (for accepted rows)
        - errors: list of error dicts (for rejected rows)
        """
        errors: list = []
        warnings: list = []

        # Extract fields using field mapping
        def get_field(name: str) -> Optional[str]:
            """Get a field from the row, trying various name formats."""
            # Try exact match
            val = row.get(name)
            if val:
                return self._normalize_text(val)

            # Try normalized version (lowercase, underscores)
            normalized = name.strip().lower().replace(" ", "_")
            val = row.get(normalized)
            if val:
                return self._normalize_text(val)

            # Try with spaces replaced by underscores
            alt = name.strip().replace(" ", "_")
            val = row.get(alt)
            if val:
                return self._normalize_text(val)

            # Try header index
            if self._field_map and int(name) in self._field_map:
                idx = self._field_map[int(name)]
                if idx < len(self._original_fields):
                    col_name = self._original_fields[idx]
                    val = row.get(col_name)
                    if val:
                        return self._normalize_text(val)

            return None

        # Map source fields
        source_raw = get_field("source") or get_field(0)
        original_text_raw = get_field("original_text") or get_field(1)
        received_at_raw = get_field("received_at") or get_field(2)

        # Compute text fingerprint for content-based duplicate detection
        text_fingerprint = None
        if original_text_raw:
            text_fingerprint = hashlib.sha256(original_text_raw.encode("utf-8")).hexdigest()

        # Check for duplicates before validation
        customer_email = self._normalize_text(
            row.get("customer_email") or row.get("customer Email") or ""
        ) or None
        conversation_id = self._normalize_text(
            row.get("source_conversation_id") or row.get("source Conversation ID") or ""
        ) or None
        message_id = self._normalize_text(
            row.get("source_message_id") or row.get("source Message ID") or ""
        ) or None

        # Check within-batch duplicates first
        is_batch_duplicate = False
        batch_dup_reason = ""
        if customer_email and customer_email in (seen_emails or set()):
            is_batch_duplicate = True
            batch_dup_reason = f"Duplicate email in this import: {customer_email}"
        elif message_id and message_id in (seen_message_ids or set()):
            is_batch_duplicate = True
            batch_dup_reason = f"Duplicate message_id in this import: {message_id}"
        elif conversation_id and conversation_id in (seen_conversation_ids or set()):
            is_batch_duplicate = True
            batch_dup_reason = f"Duplicate conversation_id in this import: {conversation_id}"
        elif text_fingerprint and text_fingerprint in (seen_text_fingerprints or set()):
            is_batch_duplicate = True
            batch_dup_reason = f"Duplicate content in this import"

        if is_batch_duplicate:
            return {
                "duplicate": True,
                "rejected": False,
                "inquiry": None,
                "warnings": [{
                    "row_number": row_number,
                    "field": "duplicate_detection",
                    "message": batch_dup_reason,
                    "severity": "warning",
                }],
            }

        # Check database for existing duplicates
        repository = get_repository()
        duplicate_candidates = repository.find_duplicates(
            customer_email=customer_email,
            text_fingerprint=text_fingerprint,
            conversation_id=conversation_id,
            message_id=message_id,
        )

        if duplicate_candidates:
            return {
                "duplicate": True,
                "rejected": False,
                "inquiry": None,
                "warnings": [{
                    "row_number": row_number,
                    "field": "duplicate_detection",
                    "message": f"Possible duplicate of inquiry {duplicate_candidates[0].inquiry_id}",
                    "severity": "warning",
                }],
            }

        # Extract fields using field mapping
        def get_field(name: str) -> Optional[str]:
            """Get a field from the row, trying various name formats."""
            # Try exact match
            val = row.get(name)
            if val:
                return self._normalize_text(val)

            # Try normalized version (lowercase, underscores)
            normalized = name.strip().lower().replace(" ", "_")
            val = row.get(normalized)
            if val:
                return self._normalize_text(val)

            # Try with spaces replaced by underscores
            alt = name.strip().replace(" ", "_")
            val = row.get(alt)
            if val:
                return self._normalize_text(val)

            # Try header index
            if self._field_map and int(name) in self._field_map:
                idx = self._field_map[int(name)]
                if idx < len(self._original_fields):
                    col_name = self._original_fields[idx]
                    val = row.get(col_name)
                    if val:
                        return self._normalize_text(val)

            return None

        # Map source fields
        source_raw = get_field("source") or get_field(0)
        original_text_raw = get_field("original_text") or get_field(1)
        received_at_raw = get_field("received_at") or get_field(2)

        # Compute text fingerprint for content-based duplicate detection
        text_fingerprint = None
        if original_text_raw:
            text_fingerprint = hashlib.sha256(original_text_raw.encode("utf-8")).hexdigest()

        # Check for duplicates before validation
        customer_email = self._normalize_text(
            row.get("customer_email") or row.get("customer Email") or ""
        ) or None
        conversation_id = self._normalize_text(
            row.get("source_conversation_id") or row.get("source Conversation ID") or ""
        ) or None
        message_id = self._normalize_text(
            row.get("source_message_id") or row.get("source Message ID") or ""
        ) or None

        # Validate required fields
        if not source_raw or not source_raw.strip():
            errors.append({
                "row_number": row_number,
                "field": "source",
                "message": "Source field is required",
                "severity": "error",
            })

        if not original_text_raw or not original_text_raw.strip():
            errors.append({
                "row_number": row_number,
                "field": "original_text",
                "message": "Original text field is required",
                "severity": "error",
            })

        # Parse and validate received_at
        received_at = None
        if received_at_raw:
            try:
                received_at = self._normalize_date(received_at_raw)
            except ValueError as e:
                errors.append({
                    "row_number": row_number,
                    "field": "received_at",
                    "message": f"Invalid received_at date: {e}",
                    "severity": "error",
                })

        # Build inquiry
        inquiry = self._build_inquiry(
            source=source_raw or "manual",
            original_text=original_text_raw,
            received_at=received_at or self._now_iso(),
            # Optional fields from row
            customer_name=self._normalize_text(
                get_field("customer_name") or get_field("customer Name") or ""
            ),
            customer_email=customer_email,
            customer_phone=self._normalize_text(
                get_field("customer_phone") or get_field("customer Phone") or ""
            ),
            organization=self._normalize_text(
                get_field("organization") or get_field("Organisation") or ""
            ),
            requested_service=self._normalize_text(
                get_field("requested_service") or get_field("Requested Service") or ""
            ),
            budget_stated=self._normalize_text(
                get_field("budget_stated") or get_field("Budget Stated") or ""
            ),
            budget_inferred=self._normalize_text(
                get_field("budget_inferred") or get_field("Budget Inferred") or ""
            ),
            timeline_stated=self._normalize_text(
                get_field("timeline_stated") or get_field("Timeline Stated") or ""
            ),
            timeline_inferred=self._normalize_text(
                get_field("timeline_inferred") or get_field("Timeline Inferred") or ""
            ),
            classification=self._normalize_text(
                get_field("classification") or get_field("Classification") or ""
            ),
            source_platform=self._normalize_text(
                get_field("source_platform") or get_field("Source Platform") or ""
            ),
            source_account=self._normalize_text(
                get_field("source_account") or get_field("Source Account") or ""
            ),
            source_conversation_id=self._normalize_text(
                get_field("source_conversation_id") or get_field("Source Conversation ID") or ""
            ),
            source_message_id=self._normalize_text(
                get_field("source_message_id") or get_field("Source Message ID") or ""
            ),
            missing_information_json=self._normalize_text(
                get_field("missing_information_json") or get_field("Missing Information JSON") or ""
            ),
            recommended_next_action=self._normalize_text(
                get_field("recommended_next_action") or get_field("Recommended Next Action") or ""
            ),
            priority_score=get_field("priority_score"),
            priority_explanation=self._normalize_text(
                get_field("priority_explanation") or get_field("Priority Explanation") or ""
            ),
            status=get_field("status") or "New",
        )

        # Validate basic inquiry structure
        base_errors = self._validate_inquiry_basic(inquiry)
        if base_errors:
            errors.extend(base_errors)

        # Handle warnings (non-critical issues)
        customer_phone = self._normalize_text(
            get_field("customer_phone") or get_field("customer Phone") or ""
        )

        if customer_email and not self._is_valid_email(customer_email):
            warnings.append({
                "row_number": row_number,
                "field": "customer_email",
                "message": "Email format appears invalid",
                "severity": "warning",
            })

        if customer_phone and not self._is_valid_phone(customer_phone):
            warnings.append({
                "row_number": row_number,
                "field": "customer_phone",
                "message": "Phone format appears invalid",
                "severity": "warning",
            })

        # Build result
        if errors:
            return {
                "rejected": True,
                "inquiry": None,
                "errors": errors,
                "warnings": [],
            }
        else:
            return {
                "duplicate": False,
                "rejected": False,
                "inquiry": inquiry,
                "warnings": warnings,
            }

    def _validate_inquiry_basic(self, inquiry: Inquiry) -> List[dict]:
        """Validate basic inquiry structure."""
        errors: List[dict] = []

        # Original text must not be empty
        if not inquiry.original_text or not inquiry.original_text.strip():
            errors.append({
                "field": "original_text",
                "message": "Original inquiry text must not be empty",
                "severity": "error",
            })

        # At least some meaningful information must be present
        has_info = any([
            inquiry.customer_name,
            inquiry.organization,
            inquiry.requested_service,
            inquiry.original_text.strip(),
        ])
        if not has_info:
            errors.append({
                "field": "minimal_info",
                "message": "Inquiry must contain at least a customer name, organization, "
                           "or requested service description",
                "severity": "warning",
            })

        return errors

    def _build_inquiry(
        self,
        source: str,
        original_text: str,
        received_at: str,
        **fields,
    ) -> Inquiry:
        """Build an Inquiry object from extracted fields."""
        # Parse missing information JSON if present
        missing_info_list = []
        if fields.get("missing_information_json"):
            try:
                missing_info_list = json.loads(fields["missing_information_json"])
                # Convert dicts to MissingInformation objects
                missing_info_list = [MissingInformation(**m) for m in missing_info_list]
            except (json.JSONDecodeError, TypeError):
                pass

        # Parse priority_score if present
        priority_score = 0.0
        if fields.get("priority_score"):
            try:
                priority_score = float(fields["priority_score"])
            except (ValueError, TypeError):
                pass

        # Parse status if present
        status = fields.get("status") or "New"

        return Inquiry(
            source=InquirySource(source.lower()),
            source_platform=fields.get("source_platform"),
            source_account=fields.get("source_account"),
            source_conversation_id=fields.get("source_conversation_id"),
            source_message_id=fields.get("source_message_id"),
            customer_name=fields.get("customer_name"),
            customer_email=fields.get("customer_email"),
            customer_phone=fields.get("customer_phone"),
            organization=fields.get("organization"),
            requested_service=fields.get("requested_service"),
            budget_stated=fields.get("budget_stated"),
            budget_inferred=fields.get("budget_inferred"),
            timeline_stated=fields.get("timeline_stated"),
            timeline_inferred=fields.get("timeline_inferred"),
            classification=fields.get("classification"),
            missing_information=missing_info_list,
            recommended_next_action=fields.get("recommended_next_action"),
            priority_score=priority_score,
            priority_explanation=fields.get("priority_explanation"),
            status=status,
            original_text=self._normalize_text(original_text),
            received_at=received_at,
            first_observed_at=received_at,
        )

    def _normalize_text(self, text) -> Optional[str]:
        """Normalize text field: strip, convert None."""
        if text is None:
            return None
        if isinstance(text, str):
            return text.strip()
        return str(text).strip()

    def _normalize_date(self, date_input) -> str:
        """Parse various date formats and return ISO string."""
        if not date_input:
            return self._now_iso()

        if isinstance(date_input, str):
            input_str = date_input.strip()
        else:
            input_str = str(date_input).strip()

        # Try ISO format first
        if "T" in input_str:
            try:
                dt = datetime.fromisoformat(input_str.replace("Z", "+00:00"))
                return dt.isoformat().replace("+00:00", "Z")
            except ValueError:
                pass

        # Try common formats
        formats = [
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%d %H:%M",
            "%Y-%m-%d",
            "%m/%d/%Y %H:%M:%S",
            "%m/%d/%Y",
            "%d/%m/%Y %H:%M:%S",
            "%d/%m/%Y",
            "%b %d, %Y",
            "%B %d, %Y",
        ]

        for fmt in formats:
            try:
                dt = datetime.strptime(input_str, fmt)
                return dt.isoformat() + "Z"
            except ValueError:
                continue

        raise ValueError(f"Unrecognized date format: {input_str}")

    def _now_iso(self) -> str:
        """Return current UTC timestamp in ISO format."""
        return datetime.utcnow().isoformat() + "Z"

    def _is_valid_email(self, email: str) -> bool:
        """Basic email format validation."""
        return "@" in email and "." in email.split("@")[-1]

    def _is_valid_phone(self, phone: str) -> bool:
        """Basic phone format validation."""
        if not phone:
            return False
        digits = "".join(c for c in phone if c.isdigit())
        return 10 <= len(digits) <= 15