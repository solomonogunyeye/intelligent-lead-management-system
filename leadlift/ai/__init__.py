"""LEADLIFT — AI package exports."""

from .provider import (
    AIProvider,
    AIProviderConfig,
    AIProviderError,
    AIProviderTimeout,
    AIProviderUnavailable,
    AIValidationError,
    ExtractionResult,
)
try:
    from .ollama import OllamaProvider, create_provider
except ImportError:
    OllamaProvider = None
    create_provider = None
from .validation import (
    ValidatedExtraction,
    ValidatedResponseDraft,
    validate_extraction,
    validate_response_draft,
    sanitize_for_storage,
)
from .processor import AIProcessor, process_inquiry

__all__ = [
    "AIProvider",
    "AIProviderConfig",
    "AIProviderError",
    "AIProviderTimeout",
    "AIProviderUnavailable",
    "AIValidationError",
    "ExtractionResult",
    "OllamaProvider",
    "create_provider",
    "ValidatedExtraction",
    "ValidatedResponseDraft",
    "validate_extraction",
    "validate_response_draft",
    "sanitize_for_storage",
    "AIProcessor",
    "process_inquiry",
]