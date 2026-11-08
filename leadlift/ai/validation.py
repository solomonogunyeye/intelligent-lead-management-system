"""LEADLIFT — AI output validation using Pydantic models."""

from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field, field_validator, model_validator

from leadlift.ai.provider import ExtractionResult


class ValidatedExtraction(BaseModel):
    """Validated extraction result with strict type checking."""

    # Explicit customer-stated fields
    customer_name: Optional[str] = Field(None, max_length=200)
    customer_email: Optional[str] = Field(None, max_length=200)
    customer_phone: Optional[str] = Field(None, max_length=50)
    organization: Optional[str] = Field(None, max_length=200)
    requested_service: Optional[str] = Field(None, max_length=200)
    budget_stated: Optional[str] = Field(None, max_length=200)
    timeline_stated: Optional[str] = Field(None, max_length=200)
    explicit_preferences: Optional[str] = Field(None, max_length=500)

    # Inferred fields
    budget_inferred: Optional[str] = Field(None, max_length=200)
    timeline_inferred: Optional[str] = Field(None, max_length=200)
    industry: Optional[str] = Field(None, max_length=200)
    project_scope: Optional[str] = Field(None, max_length=500)
    buying_intent: Optional[str] = Field(None, max_length=50)
    urgency: Optional[str] = Field(None, max_length=50)

    # Classification
    classification: Optional[str] = Field(None, max_length=50)
    classification_confidence: Optional[float] = Field(None, ge=0.0, le=1.0)

    # Missing information
    missing_information: list[str] = Field(default_factory=list)

    # Next action
    recommended_next_action: Optional[str] = Field(None, max_length=500)

    @field_validator("missing_information", mode="before")
    @classmethod
    def clean_missing_info(cls, v):
        if not isinstance(v, list):
            return []
        # Filter out None, empty strings, and non-strings
        return [str(item).strip() for item in v if item and str(item).strip()]

    @field_validator("customer_email")
    @classmethod
    def validate_email(cls, v: Optional[str]) -> Optional[str]:
        if v and "@" not in v:
            raise ValueError("Invalid email format")
        return v

    @field_validator("buying_intent")
    @classmethod
    def validate_buying_intent(cls, v: Optional[str]) -> Optional[str]:
        if v and v.lower() not in ("high", "medium", "low", "unknown"):
            raise ValueError("buying_intent must be high, medium, low, or unknown")
        return v.lower() if v else v

    @field_validator("urgency")
    @classmethod
    def validate_urgency(cls, v: Optional[str]) -> Optional[str]:
        if v and v.lower() not in ("high", "medium", "low"):
            raise ValueError("urgency must be high, medium, or low")
        return v.lower() if v else v

    @field_validator("classification")
    @classmethod
    def validate_classification(cls, v: Optional[str]) -> Optional[str]:
        if v and v.lower() not in ("qualified", "spam", "low-quality", "existing-customer", "unknown"):
            raise ValueError("Invalid classification")
        return v.lower() if v else v

    @model_validator(mode="after")
    def ensure_consistency(self) -> "ValidatedExtraction":
        """Ensure internal consistency."""
        # If classification is spam, clear buying intent
        if self.classification == "spam":
            self.buying_intent = "low"
        return self

    def to_extraction_result(self) -> ExtractionResult:
        """Convert to ExtractionResult for compatibility."""
        return ExtractionResult(
            customer_name=self.customer_name,
            customer_email=self.customer_email,
            customer_phone=self.customer_phone,
            organization=self.organization,
            requested_service=self.requested_service,
            budget_stated=self.budget_stated,
            timeline_stated=self.timeline_stated,
            explicit_preferences=self.explicit_preferences,
            budget_inferred=self.budget_inferred,
            timeline_inferred=self.timeline_inferred,
            industry=self.industry,
            project_scope=self.project_scope,
            buying_intent=self.buying_intent,
            urgency=self.urgency,
            classification=self.classification,
            classification_confidence=self.classification_confidence,
            missing_information=self.missing_information,
            recommended_next_action=self.recommended_next_action,
        )


class ValidatedResponseDraft(BaseModel):
    """Validated response draft."""

    draft: str = Field(..., min_length=10, max_length=5000)

    @field_validator("draft")
    @classmethod
    def validate_draft(cls, v: str) -> str:
        v = v.strip()
        if len(v) < 10:
            raise ValueError("Draft too short")
        # Check for patterns that suggest FABRICATED prices/guarantees (not customer-stated)
        # Allow references to customer's stated budget/timeline
        import re
        
        # Patterns that indicate fabricated claims
        forbidden_patterns = [
            r"\bguarantee\b",
            r"\bpromise\b",
            r"we (can|will) (commit|contract)",
            r"delivery by \w+ \d+",
            r"available on \w+ \d+",
            r"discount of \d+%",
            r"we charge \$[\d,]+",
            r"price is \$[\d,]+",
        ]
        for pattern in forbidden_patterns:
            if re.search(pattern, v, re.IGNORECASE):
                raise ValueError(f"Draft contains potentially fabricated claim: {pattern}")
        return v


def validate_extraction(raw: ExtractionResult) -> ValidatedExtraction:
    """Validate an ExtractionResult and return ValidatedExtraction."""
    # Convert ExtractionResult to dict for validation
    data = {
        "customer_name": raw.customer_name,
        "customer_email": raw.customer_email,
        "customer_phone": raw.customer_phone,
        "organization": raw.organization,
        "requested_service": raw.requested_service,
        "budget_stated": raw.budget_stated,
        "timeline_stated": raw.timeline_stated,
        "explicit_preferences": raw.explicit_preferences,
        "budget_inferred": raw.budget_inferred,
        "timeline_inferred": raw.timeline_inferred,
        "industry": raw.industry,
        "project_scope": raw.project_scope,
        "buying_intent": raw.buying_intent,
        "urgency": raw.urgency,
        "classification": raw.classification,
        "classification_confidence": raw.classification_confidence,
        "missing_information": raw.missing_information,
        "recommended_next_action": raw.recommended_next_action,
    }

    try:
        return ValidatedExtraction(**data)
    except Exception as e:
        # Try to salvage by clearing invalid fields
        cleaned = {}
        for key, value in data.items():
            try:
                # Test individual field validation
                temp = ValidatedExtraction(**{key: value})
                cleaned[key] = value
            except Exception:
                cleaned[key] = None

        # Always allow missing_information and next_action through
        cleaned["missing_information"] = data.get("missing_information", [])
        cleaned["recommended_next_action"] = data.get("recommended_next_action")

        try:
            return ValidatedExtraction(**cleaned)
        except Exception as e2:
            # Return minimal valid object
            return ValidatedExtraction(
                missing_information=data.get("missing_information", []),
                recommended_next_action=data.get("recommended_next_action"),
            )


def validate_response_draft(draft: str) -> ValidatedResponseDraft:
    """Validate a response draft."""
    try:
        return ValidatedResponseDraft(draft=draft)
    except Exception as e:
        raise ValueError(f"Draft validation failed: {e}")


def sanitize_for_storage(data: dict) -> dict:
    """Sanitize data for database storage."""
    sanitized = {}
    for key, value in data.items():
        if isinstance(value, str):
            # Remove potential injection attempts
            sanitized[key] = value.replace("\x00", "").strip()
        elif isinstance(value, list):
            sanitized[key] = [sanitize_for_storage({"item": v})["item"] for v in value]
        elif isinstance(value, dict):
            sanitized[key] = sanitize_for_storage(value)
        else:
            sanitized[key] = value
    return sanitized