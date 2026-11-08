"""LEADLIFT — Pydantic data models for inquiries, tasks, and history."""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from typing import Any, List, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class InquirySource(str, Enum):
    MANUAL = "manual"
    CSV = "csv"
    BROWSER = "browser"
    OCR = "ocr"
    EMAIL = "email"
    WHATSAPP = "whatsapp"
    INSTAGRAM = "instagram"
    FACEBOOK = "facebook"
    TELEGRAM = "telegram"
    WEBSITE_FORM = "website_form"


class InquiryStatus(str, Enum):
    NEW = "New"
    REVIEWED = "Reviewed"
    CONTACTED = "Contacted"
    QUALIFIED = "Qualified"
    PROPOSAL_SENT = "Proposal Sent"
    WON = "Won"
    LOST = "Lost"
    ARCHIVED = "Archived"

    @classmethod
    def valid_transitions(cls) -> dict[InquiryStatus, list[InquiryStatus]]:
        return {
            cls.NEW: [cls.REVIEWED, cls.ARCHIVED],
            cls.REVIEWED: [cls.CONTACTED, cls.ARCHIVED],
            cls.CONTACTED: [cls.QUALIFIED, cls.LOST, cls.ARCHIVED],
            cls.QUALIFIED: [cls.PROPOSAL_SENT, cls.LOST, cls.ARCHIVED],
            cls.PROPOSAL_SENT: [cls.WON, cls.LOST, cls.ARCHIVED],
            cls.WON: [cls.ARCHIVED],
            cls.LOST: [cls.ARCHIVED],
            cls.ARCHIVED: [],
        }


class AIProcessingState(str, Enum):
    PENDING = "pending"
    PROCESSED = "processed"
    FAILED = "failed"
    SKIPPED = "skipped"


class ClassificationSource(str, Enum):
    RULE = "rule"
    AI = "ai"
    MANUAL = "manual"


def generate_id(prefix: str = "") -> str:
    """Generate a unique ID with optional prefix."""
    return f"{prefix}{uuid.uuid4().hex[:12]}"


def now_iso() -> str:
    """Current time in ISO 8601 format (UTC)."""
    return datetime.utcnow().isoformat() + "Z"


class MissingInformation(BaseModel):
    """Structured representation of missing information."""
    field: str
    description: str
    importance: str = "medium"  # low, medium, high


class Inquiry(BaseModel):
    """
    Core inquiry record.

    Distinguishes three categories of information:
    - explicit_*: Stated directly by the customer
    - inferred_*: Derived from message content (not directly stated)
    - missing_*: Identified as absent but potentially relevant
    """
    inquiry_id: str = Field(default_factory=lambda: generate_id("inq_"))
    source: InquirySource = InquirySource.MANUAL
    source_platform: Optional[str] = None
    source_account: Optional[str] = None
    source_conversation_id: Optional[str] = None
    source_message_id: Optional[str] = None
    text_fingerprint: Optional[str] = None

    # Customer info (explicitly stated)
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    organization: Optional[str] = None

    # Request details (explicitly stated)
    requested_service: Optional[str] = None
    budget_stated: Optional[str] = None
    timeline_stated: Optional[str] = None

    # Inferred fields (derived from message, not explicit)
    budget_inferred: Optional[str] = None
    timeline_inferred: Optional[str] = None
    classification: Optional[str] = None
    classification_source: ClassificationSource = ClassificationSource.RULE

    # Missing information (structured)
    missing_information: List[MissingInformation] = Field(default_factory=list)

    # Recommended next action
    recommended_next_action: Optional[str] = None

    # Priority (deterministic)
    priority_score: float = 0.0
    priority_explanation: Optional[str] = None

    # Workflow
    status: InquiryStatus = InquiryStatus.NEW
    ai_state: AIProcessingState = AIProcessingState.PENDING

    # Original message (preserved exactly)
    original_text: str = ""

    # Timestamps
    received_at: str = Field(default_factory=now_iso)
    first_observed_at: str = Field(default_factory=now_iso)
    last_processed_at: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @field_validator("original_text")
    @classmethod
    def _non_empty_original(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("original_text must not be empty")
        return v.strip()

    @model_validator(mode="after")
    def _touch_updated(self) -> "Inquiry":
        self.updated_at = now_iso()
        return self

    def to_db_dict(self) -> dict:
        """Convert to dictionary suitable for database insertion."""
        return {
            "inquiry_id": self.inquiry_id,
            "source": self.source.value,
            "source_platform": self.source_platform,
            "source_account": self.source_account,
            "source_conversation_id": self.source_conversation_id,
            "source_message_id": self.source_message_id,
            "text_fingerprint": self.text_fingerprint,
            "customer_name": self.customer_name,
            "customer_email": self.customer_email,
            "customer_phone": self.customer_phone,
            "organization": self.organization,
            "requested_service": self.requested_service,
            "budget_stated": self.budget_stated,
            "budget_inferred": self.budget_inferred,
            "timeline_stated": self.timeline_stated,
            "timeline_inferred": self.timeline_inferred,
            "classification": self.classification,
            "classification_source": self.classification_source.value,
            "missing_information_json": json.dumps([m.model_dump() for m in self.missing_information]),
            "recommended_next_action": self.recommended_next_action,
            "priority_score": self.priority_score,
            "priority_explanation": self.priority_explanation,
            "status": self.status.value,
            "ai_state": self.ai_state.value,
            "original_text": self.original_text,
            "received_at": self.received_at,
            "first_observed_at": self.first_observed_at,
            "last_processed_at": self.last_processed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_db_row(cls, row: Any) -> "Inquiry":
        """Create an Inquiry from a database row."""
        data = dict(row)
        data["missing_information"] = json.loads(data.pop("missing_information_json", "[]"))
        data["source"] = InquirySource(data["source"])
        data["classification_source"] = ClassificationSource(data["classification_source"])
        data["status"] = InquiryStatus(data["status"])
        data["ai_state"] = AIProcessingState(data["ai_state"])
        return cls(**data)


class StatusHistoryEntry(BaseModel):
    id: Optional[int] = None
    inquiry_id: str
    previous_status: Optional[InquiryStatus] = None
    new_status: InquiryStatus
    changed_at: str = Field(default_factory=now_iso)
    reason: Optional[str] = None


class ProcessingHistoryEntry(BaseModel):
    id: Optional[int] = None
    inquiry_id: str
    step: str
    provider: Optional[str] = None
    state: str  # success, failed, timeout, etc.
    error_message: Optional[str] = None
    details: Optional[dict] = None
    processed_at: str = Field(default_factory=now_iso)

    def to_db_dict(self) -> dict:
        return {
            "inquiry_id": self.inquiry_id,
            "step": self.step,
            "provider": self.provider,
            "state": self.state,
            "error_message": self.error_message,
            "details_json": json.dumps(self.details) if self.details else None,
            "processed_at": self.processed_at,
        }


class FollowUpTask(BaseModel):
    task_id: str = Field(default_factory=lambda: generate_id("tsk_"))
    inquiry_id: str
    description: str
    due_at: str  # ISO 8601 with timezone
    assigned_to: Optional[str] = None
    notes: Optional[str] = None
    completed: bool = False
    completed_at: Optional[str] = None
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    @field_validator("due_at")
    @classmethod
    def _validate_due_at(cls, v: str) -> str:
        # Ensure it's a valid ISO format; we accept with or without Z
        try:
            datetime.fromisoformat(v.replace("Z", "+00:00"))
        except ValueError:
            raise ValueError("due_at must be a valid ISO 8601 datetime")
        return v

    @model_validator(mode="after")
    def _touch_updated(self) -> "FollowUpTask":
        self.updated_at = now_iso()
        return self

    def is_overdue(self, now: Optional[datetime] = None) -> bool:
        """Check if task is overdue. Computed at query time."""
        if self.completed:
            return False
        now = now or datetime.utcnow()
        due_str = self.due_at.replace("Z", "+00:00")
        due = datetime.fromisoformat(due_str)
        # Ensure both are timezone-aware or both naive
        if due.tzinfo is None:
            now = now.replace(tzinfo=None) if now.tzinfo else now
        else:
            if now.tzinfo is None:
                now = now.replace(tzinfo=timezone.utc)
        return due < now

    def to_db_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "inquiry_id": self.inquiry_id,
            "description": self.description,
            "due_at": self.due_at,
            "assigned_to": self.assigned_to,
            "notes": self.notes,
            "completed": 1 if self.completed else 0,
            "completed_at": self.completed_at,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_db_row(cls, row: Any) -> "FollowUpTask":
        data = dict(row)
        data["completed"] = bool(data["completed"])
        return cls(**data)


class BusinessConfig(BaseModel):
    services: List[str] = Field(default_factory=list)
    stale_hours: int = 48
    communication_tone: str = "Professional"

    def to_db_dict(self) -> dict:
        return {
            "services_json": json.dumps(self.services),
            "stale_hours": self.stale_hours,
            "communication_tone": self.communication_tone,
            "updated_at": now_iso(),
        }

    @classmethod
    def from_db_row(cls, row: Any) -> "BusinessConfig":
        data = dict(row)
        data["services"] = json.loads(data.pop("services_json", "[]"))
        return cls(**data)


class CSVImportResult(BaseModel):
    """Result of a CSV import operation."""
    accepted: int = 0
    rejected: int = 0
    duplicates: int = 0
    errors: List[dict] = Field(default_factory=list)
    warnings: List[dict] = Field(default_factory=list)
    preview: Optional[List[Inquiry]] = Field(default_factory=list, exclude=True)


class CSVImportRowError(BaseModel):
    row_number: int
    field: Optional[str] = None
    message: str
    severity: str = "error"  # error, warning


class ResponseDraft(BaseModel):
    """Response draft for an inquiry."""
    draft_id: str = Field(default_factory=lambda: generate_id("drf_"))
    inquiry_id: str
    draft_text: str
    generated_by: str  # "ai", "manual"
    model: Optional[str] = None
    status: str = "draft"  # draft, edited, sent, archived
    created_at: str = Field(default_factory=now_iso)
    updated_at: str = Field(default_factory=now_iso)

    def to_db_dict(self) -> dict:
        return {
            "draft_id": self.draft_id,
            "inquiry_id": self.inquiry_id,
            "draft_text": self.draft_text,
            "generated_by": self.generated_by,
            "model": self.model,
            "status": self.status,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }

    @classmethod
    def from_db_row(cls, row: Any) -> "ResponseDraft":
        return cls(**dict(row))

    @model_validator(mode="after")
    def _touch_updated(self) -> "ResponseDraft":
        self.updated_at = now_iso()
        return self