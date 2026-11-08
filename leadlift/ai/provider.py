"""LEADLIFT — AI provider abstraction for structured extraction."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any, Optional


@dataclass
class AIProviderConfig:
    """Configuration for an AI provider."""
    model: str
    host: str = "http://127.0.0.1:11434"
    timeout: int = 30
    temperature: float = 0.1
    max_tokens: Optional[int] = None


@dataclass
class ExtractionResult:
    """Result of structured extraction from an inquiry."""
    # Explicit facts stated by the customer
    customer_name: Optional[str] = None
    customer_email: Optional[str] = None
    customer_phone: Optional[str] = None
    organization: Optional[str] = None
    requested_service: Optional[str] = None
    budget_stated: Optional[str] = None
    timeline_stated: Optional[str] = None
    explicit_preferences: Optional[str] = None

    # Inferences made by the model
    budget_inferred: Optional[str] = None
    timeline_inferred: Optional[str] = None
    industry: Optional[str] = None
    project_scope: Optional[str] = None
    buying_intent: Optional[str] = None  # high, medium, low, unknown
    urgency: Optional[str] = None  # high, medium, low

    # Classification
    classification: Optional[str] = None  # e.g., "qualified", "spam", "low-quality"
    classification_confidence: Optional[float] = None

    # Missing information
    missing_information: list[str] = None

    # Recommended next action
    recommended_next_action: Optional[str] = None

    # Raw response for debugging
    raw_response: Optional[str] = None

    def __post_init__(self):
        if self.missing_information is None:
            self.missing_information = []


class AIProviderError(Exception):
    """Base exception for AI provider errors."""
    pass


class AIProviderTimeout(AIProviderError):
    """Raised when the provider times out."""
    pass


class AIProviderUnavailable(AIProviderError):
    """Raised when the provider is unavailable."""
    pass


class AIValidationError(AIProviderError):
    """Raised when AI output fails validation."""
    pass


class AIProvider(ABC):
    """Abstract base class for AI providers."""

    def __init__(self, config: AIProviderConfig):
        self.config = config

    @abstractmethod
    def extract(self, inquiry_text: str, context: dict = None) -> ExtractionResult:
        """
        Extract structured information from an inquiry text.

        Args:
            inquiry_text: The original customer message
            context: Optional context (business services, previous history, etc.)

        Returns:
            ExtractionResult with explicit/inferred/unknown fields

        Raises:
            AIProviderTimeout: If the request times out
            AIProviderUnavailable: If the provider cannot be reached
            AIValidationError: If the output fails validation
        """
        pass

    @abstractmethod
    def classify(self, inquiry_text: str, context: dict = None) -> tuple[str, float]:
        """
        Classify an inquiry.

        Returns:
            (classification, confidence) tuple
        """
        pass

    @abstractmethod
    def detect_missing(self, inquiry_text: str, extracted: ExtractionResult, context: dict = None) -> list[str]:
        """
        Detect missing information.

        Returns:
            List of missing information descriptions
        """
        pass

    @abstractmethod
    def suggest_next_action(self, inquiry_text: str, extracted: ExtractionResult, context: dict = None) -> str:
        """
        Suggest next action based on extraction.

        Returns:
            Recommended next action string
        """
        pass

    @abstractmethod
    def health_check(self) -> bool:
        """Check if the provider is available."""
        pass

    @abstractmethod
    def get_model_info(self) -> dict:
        """Get information about the current model."""
        pass