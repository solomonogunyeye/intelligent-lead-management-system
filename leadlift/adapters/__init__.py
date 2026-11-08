"""LEADLIFT — Intake adapters package."""

from .base import BaseAdapter
from .manual import ManualEntryAdapter
from .csv import CSVAdapter

try:
    from .browser import (
        BrowserAdapter,
        BrowserExtractionResult,
        BrowserIntakeResult,
        ExtractionStatus,
        extract_from_file,
        save_confirmed_inquiry,
    )
    _browser_available = True
except ImportError:
    _browser_available = False
    BrowserAdapter = None
    BrowserExtractionResult = None
    BrowserIntakeResult = None
    ExtractionStatus = None
    extract_from_file = None
    save_confirmed_inquiry = None

__all__ = [
    "BaseAdapter",
    "ManualEntryAdapter",
    "CSVAdapter",
]

if _browser_available:
    __all__.extend([
        "BrowserAdapter",
        "BrowserExtractionResult",
        "BrowserIntakeResult",
        "ExtractionStatus",
        "extract_from_file",
        "save_confirmed_inquiry",
    ])