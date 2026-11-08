"""LEADLIFT — Gmail intake adapter for inquiry extraction from emails.

Phase 3B: Gmail Read-Only Email Intake Integration

Security considerations:
- Uses Gmail API with read-only OAuth2 (scope: https://www.googleapis.com/auth/gmail.readonly)
- Token storage: Windows Credential Manager via Python keyring (preferred)
- No email sending, deleting, archiving, or message modification
- HTML parsing with non-executing parser (BeautifulSoup)
- All email content treated as untrusted input
"""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import re
import time
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Set, Tuple
from dataclasses import dataclass
from enum import Enum

import keyring
import keyring.errors as keyring_errors
from bs4 import BeautifulSoup

from leadlift.models import (
    Inquiry,
    InquirySource,
    MissingInformation,
    BusinessConfig,
    CSVImportResult,
)
from leadlift.repository import InquiryRepository
from leadlift.adapters.base import BaseAdapter
from leadlift.logging_config import get_logger, safe_log
import logging

_logger = get_logger("adapters.email")
from leadlift.config import get_settings


class GmailExtractionStatus(str, Enum):
    """Status of Gmail extraction attempt."""
    SUCCESS = "success"
    NO_INQUIRY = "no_inquiry"
    INSUFFICIENT = "insufficient"
    ERROR = "error"


@dataclass
class GmailMessageMetadata:
    """Metadata from Gmail API response."""
    message_id: str
    thread_id: str
    subject: str
    from_email: str
    from_name: str
    to_emails: List[str]
    date: str
    snippet: str
    body_preview: str
    labels: List[str]
    size_estimate: int
    is_spam: bool
    is_trashed: bool


@dataclass
class GmailExtractionResult:
    """Result of extracting an inquiry from a single email message."""
    status: GmailExtractionStatus
    inquiry: Optional[Inquiry] = None
    error_message: Optional[str] = None
    warnings: List[str] = None

    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


@dataclass
class EmailIntakeResult:
    """Result of Gmail email intake operation."""
    success: bool
    retrieved_count: int = 0
    processed_count: int = 0
    skipped_count: int = 0
    duplicates_count: int = 0
    errors: List[dict] = None
    warnings: List[dict] = None
    proposed_inquiries: List[Inquiry] = None
    email_details: List[dict] = None

    def __post_init__(self):
        if self.errors is None:
            self.errors = []
        if self.warnings is None:
            self.warnings = []
        if self.proposed_inquiries is None:
            self.proposed_inquiries = []
        if self.email_details is None:
            self.email_details = []

    def to_csv_import_result(self):
        """Convert to CSVImportResult for compatibility with existing UI."""
        return CSVImportResult(
            accepted=self.processed_count,
            rejected=len(self.errors),
            duplicates=self.duplicates_count,
            errors=self.errors,
            warnings=self.warnings
        )


class GmailIntakeAdapter(BaseAdapter):
    """Gmail intake adapter for extracting inquiries from emails using Gmail API.

    Phase 3B Implementation: Official Gmail API with read-only OAuth2

    Features:
    - Gmail API with scope: https://www.googleapis.com/auth/gmail.readonly
    - Bounded retrieval: last 30 days, max 100 messages
    - Duplicate detection using text fingerprint, email, and IDs
    - Filtering: skip spam/trash, flag newsletters/promotions
    - Review workflow: user confirmation before saving
    - Token storage: Windows Credential Manager via keyring
    - Non-executing HTML parsing with BeautifulSoup
    - Read-only guarantee: no email sending, deleting, archiving
    """

    def __init__(self, settings=None):
        super().__init__(settings)
        self.settings = settings or get_settings()
        self._gmail_service = None
        self._credentials = None

    # -----------------------------------------------------------------------
    # Authentication and Token Storage
    # -----------------------------------------------------------------------

    def _load_credentials(self):
        """Load OAuth2 credentials from keyring or environment.

        Implements secure token storage:
        1. Windows Credential Manager via Python keyring (preferred)
        2. Falls back to environment variables only when keyring is unavailable

        Raises GoogleAuthError if credentials not configured.
        """
        try:
            from google.oauth2.credentials import Credentials
            from google.auth.exceptions import GoogleAuthError, TokenRefreshError
        except ImportError:
            return self._load_credentials_mock()

        # Attempt 1: Windows Credential Manager via keyring (preferred)
        try:
            client_id = keyring.get_password("LEADLIFT_GMAIL", "client_id")
            client_secret = keyring.get_password("LEADLIFT_GMAIL", "client_secret")
            refresh_token = keyring.get_password("LEADLIFT_GMAIL", "refresh_token")

            if all([client_id, client_secret, refresh_token]):
                return Credentials(
                    token=None,
                    refresh_token=refresh_token,
                    client_id=client_id,
                    client_secret=client_secret,
                    token_uri="https://oauth2.googleapis.com/token",
                    scopes=["https://www.googleapis.com/auth/gmail.readonly"],
                )
        except keyring_errors.KeyringError:
            pass

        # Attempt 2: Encrypted keyring storage
        try:
            client_id_encrypted = keyring.get_password("LEADLIFT_GMAIL_ENCRYPTED", "client_id")
            client_secret_encrypted = keyring.get_password("LEADLIFT_GMAIL_ENCRYPTED", "client_secret")
            refresh_token_encrypted = keyring.get_password("LEADLIFT_GMAIL_ENCRYPTED", "refresh_token")

            if all([client_id_encrypted, client_secret_encrypted, refresh_token_encrypted]):
                # Decrypt if encryption key is configured
                enc_key = self.settings.gmail_oauth_token_encryption_key
                if enc_key:
                    try:
                        import cryptography.fernet
                        fernet = cryptography.fernet.Fernet(
                            base64.urlsafe_b64encode(
                                hashlib.sha256(enc_key.encode()).digest()
                            )
                        )
                        client_id = fernet.decrypt(client_id_encrypted.encode()).decode()
                        client_secret = fernet.decrypt(client_secret_encrypted.encode()).decode()
                        refresh_token = fernet.decrypt(refresh_token_encrypted.encode()).decode()
                    except Exception:
                        # Decryption failed - fall through to environment variables
                        client_id, client_secret, refresh_token = None, None, None
                else:
                    # No decryption key - tokens stored unencrypted in keyring
                    client_id, client_secret, refresh_token = (
                        client_id_encrypted, client_secret_encrypted, refresh_token_encrypted
                    )

                if all([client_id, client_secret, refresh_token]):
                    return Credentials(
                        token=None,
                        refresh_token=refresh_token,
                        client_id=client_id,
                        client_secret=client_secret,
                        token_uri="https://oauth2.googleapis.com/token",
                        scopes=["https://www.googleapis.com/auth/gmail.readonly"],
                    )
        except (keyring_errors.KeyringError, Exception):
            pass

        # Attempt 3: Environment variables (explicitly configured, not a silent fallback)
        client_id = self.settings.gmail_oauth_client_id
        client_secret = self.settings.gmail_oauth_client_secret
        refresh_token = self.settings.gmail_oauth_refresh_token

        if all([client_id, client_secret, refresh_token]):
            return Credentials(
                token=None,
                refresh_token=refresh_token,
                client_id=client_id,
                client_secret=client_secret,
                token_uri="https://oauth2.googleapis.com/token",
                scopes=["https://www.googleapis.com/auth/gmail.readonly"],
            )

        raise GmailAuthError(
            "Gmail OAuth credentials not found. Please configure Gmail settings "
            "or run OAuth flow to obtain credentials and refresh tokens."
        )

    def _load_credentials_mock(self):
        """Fallback credential loading when google-auth is not installed."""
        raise GmailAuthError(
            "Gmail OAuth libraries not installed. Install with: pip install google-auth google-auth-oauthlib"
        )

    def _save_credentials_encrypted(self, credentials) -> None:
        """Save OAuth credentials to Windows Credential Manager with optional encryption."""
        try:
            settings = self.settings

            if settings.gmail_credentials_encryption_key:
                # Use keyring encrypted storage if encryption key provided
                import cryptography.fernet

                fernet = cryptography.fernet.Fernet(
                    base64.urlsafe_b64encode(
                        hashlib.sha256(settings.gmail_credentials_encryption_key.encode()).digest()
                    )
                )

                client_id_enc = fernet.encrypt(credentials.client_id.encode()).decode()
                client_secret_enc = fernet.encrypt(credentials.client_secret.encode()).decode()
                refresh_token_enc = fernet.encrypt(credentials.refresh_token.encode()).decode()

                keyring.set_password("LEADLIFT_GMAIL_ENCRYPTED", "client_id", client_id_enc)
                keyring.set_password("LEADLIFT_GMAIL_ENCRYPTED", "client_secret", client_secret_enc)
                keyring.set_password("LEADLIFT_GMAIL_ENCRYPTED", "refresh_token", refresh_token_enc)
            else:
                # Store directly in keyring (OS-level protection)
                keyring.set_password("LEADLIFT_GMAIL", "client_id", credentials.client_id)
                keyring.set_password("LEADLIFT_GMAIL", "client_secret", credentials.client_secret)
                keyring.set_password("LEADLIFT_GMAIL", "refresh_token", credentials.refresh_token)

        except Exception as e:
            raise GmailAuthError(f"Failed to save credentials: {str(e)}")

    def _get_service(self):
        """Get or create Gmail service with OAuth2 credentials.

        Validates stored credentials and handles expired/revoked tokens.
        """
        if not self._gmail_service:
            try:
                self._credentials = self._load_credentials()

                # Attempt to refresh if needed
                if not self._credentials.valid:
                    if self._credentials.expired and self._credentials.refresh_token:
                        from google.auth.transport.requests import Request
                        try:
                            self._credentials.refresh(Request())
                        except Exception:
                            raise GmailAuthError(
                                "Stored refresh token is invalid or expired. "
                                "Re-authentication required."
                            )

                self._gmail_service = self._build_gmail_service(self._credentials)

            except GmailAuthError:
                raise
            except Exception as e:
                raise GmailAuthError(f"Failed to authenticate with Gmail: {str(e)}")

        return self._gmail_service

    def _build_gmail_service(self, credentials):
        """Build a Gmail API service object from credentials."""
        try:
            # Import here to avoid hard dependency when not using Google
            from googleapiclient.discovery import build

            return build(
                "gmail", "v1",
                credentials=credentials,
                cache_discovery=False,
                static_discovery=False
            )
        except ImportError:
            raise GmailAuthError(
                "Google API client not installed. Install with: pip install google-api-python-client"
            )

    def get_oauth_url(self) -> str:
        """Generate OAuth2 authorization URL for manual setup.

        The user must visit this URL to grant read-only access,
        then complete the flow to obtain a refresh token.

        Returns:
            OAuth2 authorization URL string
        """
        settings = self.settings

        if not all([settings.gmail_oauth_client_id, settings.gmail_oauth_client_secret]):
            raise GmailAuthError(
                "Gmail OAuth client ID and secret required. "
                "Please configure gmail_oauth_client_id and gmail_oauth_client_secret."
            )

        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(
            {
                "installed": {
                    "client_id": settings.gmail_oauth_client_id,
                    "client_secret": settings.gmail_oauth_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost:8080/"]
                }
            },
            scopes=["https://www.googleapis.com/auth/gmail.readonly"]
        )

        auth_url, _ = flow.authorization_url(
            access_type="offline",
            include_granted_scopes="true",
            prompt="consent",  # Force consent to get refresh token
        )

        return auth_url

    def complete_oauth_flow(self, auth_code: str) -> bool:
        """Complete the OAuth2 flow with an authorization code.

        Exchanges authorization code for credentials and stores them securely.

        Args:
            auth_code: Authorization code from the OAuth consent redirect URL

        Returns:
            True if credentials saved successfully
        """
        settings = self.settings

        if not all([settings.gmail_oauth_client_id, settings.gmail_oauth_client_secret]):
            raise GmailAuthError(
                "Gmail OAuth client ID and secret required. "
                "Please configure gmail_oauth_client_id and gmail_oauth_client_secret."
            )

        from google_auth_oauthlib.flow import Flow

        flow = Flow.from_client_config(
            {
                "installed": {
                    "client_id": settings.gmail_oauth_client_id,
                    "client_secret": settings.gmail_oauth_client_secret,
                    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "redirect_uris": ["http://localhost:8080/"]
                }
            },
            scopes=["https://www.googleapis.com/auth/gmail.readonly"]
        )

        try:
            flow.fetch_token(code=auth_code)
            credentials = flow.credentials

            # Save credentials securely
            self._save_credentials_encrypted(credentials)

            self._credentials = credentials
            self._gmail_service = self._build_gmail_service(credentials)
            return True

        except Exception as e:
            raise GmailAuthError(f"OAuth flow failed: {str(e)}")

    def has_credentials(self) -> bool:
        """Check if valid Gmail credentials are configured."""
        try:
            self._load_credentials()
            return True
        except GmailAuthError:
            return False

    # -----------------------------------------------------------------------
    # Bounded Retrieval
    # -----------------------------------------------------------------------

    def _build_query(self) -> str:
        """Build the Gmail search query for bounded retrieval.

        Constructs a query that:
        - Limits to messages received within max age days
        - Excludes spam and trash (also filtered explicitly)
        - Restricts to inbox for safety

        Returns:
            Gmail search query string
        """
        max_age_days = max(1, min(self.settings.gmail_max_message_age_days, 365))
        start_date = datetime.utcnow() - timedelta(days=max_age_days)
        from_date = start_date.strftime("%Y/%m/%d")

        # Gmail search operators: after = received after date, in = label
        # We exclude spam and trash explicitly in the query
        query_parts = [
            f"after:{from_date}",
            "in:inbox",
            "-is:spam",
            "-is:trash",
        ]

        return " ".join(query_parts)

    def _retrieve_message_ids(self, service) -> List[dict]:
        """Retrieve message IDs from Gmail with pagination and bounded retrieval.

        Respects the max_messages limit and pagination.
        """
        max_messages = min(self.settings.gmail_max_retrieved_messages, 100)
        query = self._build_query()

        messages = []
        page_token = None

        while len(messages) < max_messages:
            try:
                request = service.users().messages().list(
                    userId='me',
                    q=query,
                    maxResults=100,  # Gmail API max per page
                    pageToken=page_token
                )
                response = request.execute()

                if 'messages' not in response:
                    break

                remaining = max_messages - len(messages)
                messages.extend(response['messages'][:remaining])

                page_token = response.get('nextPageToken')
                if not page_token:
                    break

            except Exception as e:
                raise GmailRetrievalError(f"Failed to list messages: {str(e)}")

        return messages

    def _retrieve_message(self, service, message_id: str) -> dict:
        """Retrieve full message details by ID."""
        try:
            response = service.users().messages().get(
                userId='me',
                id=message_id,
                format='full'
            ).execute()

            return response
        except Exception as e:
            raise GmailRetrievalError(f"Failed to retrieve message {message_id}: {str(e)}")

    # -----------------------------------------------------------------------
    # Message Parsing
    # -----------------------------------------------------------------------

    def _parse_message_metadata(self, message_response: dict) -> GmailMessageMetadata:
        """Extract metadata from Gmail API message response."""
        payload = message_response.get('payload', {})
        headers = payload.get('headers', [])

        # Parse headers (case-insensitive lookup)
        def get_header(name: str) -> str:
            for h in headers:
                if h['name'].lower() == name:
                    return h.get('value', '')
            return ''

        subject = get_header('subject')
        from_header = get_header('from')
        to_header = get_header('to')
        date_header = get_header('date')

        # Parse from email and name
        from_email, from_name = self._parse_email_and_name(from_header)
        to_emails = self._parse_email_addresses(to_header)

        # Get message body
        body = self._extract_message_body(payload)

        # Use Gmail snippet as body preview
        snippet = message_response.get('snippet', '')

        # Extract labels
        labels = message_response.get('labelIds', [])

        # Check for spam/trash labels
        is_spam = 'SPAM' in labels
        is_trashed = 'TRASH' in labels

        return GmailMessageMetadata(
            message_id=message_response.get('id', ''),
            thread_id=message_response.get('threadId', ''),
            subject=subject,
            from_email=from_email,
            from_name=from_name,
            to_emails=to_emails,
            date=date_header or '',
            snippet=snippet,
            body_preview=body[:500],
            labels=labels,
            size_estimate=message_response.get('sizeEstimate', 0),
            is_spam=is_spam,
            is_trashed=is_trashed
        )

    def _parse_email_and_name(self, from_header: str) -> Tuple[str, str]:
        """Parse email address and name from From header.

        Handles formats:
        - "Name Last <email@example.com>"
        - "email@example.com"
        - "<email@example.com>"
        """
        if not from_header:
            return "", ""

        # RFC 2047 encoded-word decoding for international names
        from email.header import decode_header, make_header
        try:
            decoded = str(make_header(decode_header(from_header)))
            from_header = decoded
        except Exception:
            pass

        # Pattern for "Name <email>" format
        match = re.search(r'([^<]+)\s*<([^>]+)>', from_header)
        if match:
            name = match.group(1).strip().strip('"')
            email = match.group(2).strip()
            return email.lower(), name

        # Pattern for just "email@domain.com" format
        email_match = re.search(r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})', from_header)
        if email_match:
            email = email_match.group(1)
            # Try to extract name from before email
            name_part = from_header[:from_header.find(email)].strip().strip('"')
            if name_part:
                return email.lower(), name_part
            return email.lower(), ""

        return "", from_header.strip()

    def _parse_email_addresses(self, header: str) -> List[str]:
        """Parse email addresses from a comma-separated header."""
        if not header:
            return []

        # Handle RFC 2047 decoding
        from email.header import decode_header, make_header
        try:
            decoded = str(make_header(decode_header(header)))
            header = decoded
        except Exception:
            pass

        # Extract email addresses
        pattern = r'([a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,})'
        emails = re.findall(pattern, header)

        # Clean and deduplicate (case-insensitive)
        seen = set()
        result = []
        for email in emails:
            email = email.strip().lower()
            if email not in seen:
                seen.add(email)
                result.append(email)

        return result

    def _extract_message_body(self, payload: dict) -> str:
        """Extract and decode message body from Gmail API payload.

        Handles:
        - Multipart messages (prefers text/plain over text/html)
        - HTML bodies converted to text using non-executing parser
        - Various character encodings
        - Quoted-printable and base64 encoded content
        """
        body = self._extract_body_from_parts(payload)
        return body

    def _extract_body_from_parts(self, payload: dict) -> str:
        """Extract body from message payload, handling multipart and encodings."""
        parts = payload.get('parts', [])
        mime_type = payload.get('mimeType', '')

        if not parts:
            body = payload.get('body', {})
            data = body.get('data', '')
            if data:
                return self._decode_message_body(data, body.get('attachmentId', None))
            return ""

        # Process parts
        plain_text = ""
        html_text = ""

        for part in parts:
            part_mime = part.get('mimeType', '')
            part_body = part.get('body', {})

            if part_mime == 'text/plain':
                data = part_body.get('data', '')
                if data:
                    text = self._decode_message_body(data, part_body.get('attachmentId'))
                    if text.strip():
                        plain_text = text
            elif part_mime == 'text/html':
                data = part_body.get('data', '')
                if data:
                    html_text = self._decode_message_body(data, part_body.get('attachmentId'))
            elif 'parts' in part:
                # Nested multipart
                nested = self._extract_body_from_parts_recursive(part)
                if not plain_text and nested.get('plain_text'):
                    plain_text = nested['plain_text']
                if not html_text and nested.get('html_text'):
                    html_text = nested['html_text']

        # Prefer plain text; fall back to HTML-to-text conversion
        settings = self.settings
        if plain_text.strip():
            return plain_text

        if html_text and settings.gmail_use_html_fallback:
            return self._html_to_text(html_text)

        return plain_text + html_text

    def _extract_body_from_parts_recursive(self, part: dict) -> dict:
        """Recursively extract body text from nested multipart parts."""
        parts = part.get('parts', [])
        plain_text = ""
        html_text = ""

        for subpart in parts:
            mime_type = subpart.get('mimeType', '')
            body = subpart.get('body', {})

            if mime_type == 'text/plain':
                data = body.get('data', '')
                if data:
                    text = self._decode_message_body(data, body.get('attachmentId'))
                    if text.strip() and not plain_text:
                        plain_text = text
            elif mime_type == 'text/html':
                data = body.get('data', '')
                if data:
                    text = self._decode_message_body(data, body.get('attachmentId'))
                    if text and not html_text:
                        html_text = text
            elif 'parts' in subpart:
                nested = self._extract_body_from_parts_recursive(subpart)
                if not plain_text and nested.get('plain_text'):
                    plain_text = nested['plain_text']
                if not html_text and nested.get('html_text'):
                    html_text = nested['html_text']

        return {'plain_text': plain_text, 'html_text': html_text}

    def _decode_message_body(self, data: str, attachment_id: Optional[str] = None) -> str:
        """Decode base64url-encoded message body with charset handling.

        Gmail API returns body data as base64url encoded strings.
        Handles quoted-printable and other encodings.
        """
        if not data:
            return ""

        try:
            # Decode base64url (URL-safe variant)
            # Gmail uses base64url without padding, so add padding
            padding = 4 - len(data) % 4
            if padding != 4:
                data += '=' * padding

            decoded_bytes = base64.urlsafe_b64decode(data)

            # Try common charsets
            for charset in ['utf-8', 'iso-8859-1', 'windows-1252', 'ascii']:
                try:
                    return decoded_bytes.decode(charset, errors='strict')
                except (UnicodeDecodeError, LookupError):
                    continue

            # Last resort: UTF-8 with error replacement
            return decoded_bytes.decode('utf-8', errors='replace')

        except Exception:
            return ""

    def _html_to_text(self, html: str) -> str:
        """Convert HTML to text using non-executing BeautifulSoup parser.

        This method does NOT execute:
        - JavaScript (inline, external, event handlers)
        - iframe loading
        - Form submissions
        - Any active content

        Uses 'html.parser' (built-in, non-executing).
        """
        if not html:
            return ""

        try:
            soup = BeautifulSoup(html, 'html.parser')

            # Remove script, style, iframe, noscript, and other active elements
            for tag in soup(['script', 'style', 'iframe', 'noscript',
                            'object', 'embed', 'applet', 'svg', 'img']):
                tag.decompose()

            # Remove all event handler attributes (onclick, onload, etc.)
            for tag in soup.find_all(True):
                attrs_to_remove = [attr for attr in tag.attrs if attr.startswith('on')]
                for attr in attrs_to_remove:
                    del tag.attrs[attr]

            # Remove href attributes that could be JavaScript URLs
            for tag in soup.find_all('a'):
                href = tag.get('href', '')
                if href and re.match(r'^javascript:', href.strip(), re.IGNORECASE):
                    tag.decompose()

            # Get clean text
            text = soup.get_text(separator='\n', strip=True)

            # Clean up excessive whitespace
            lines = [line.strip() for line in text.split('\n') if line.strip()]
            cleaned = '\n'.join(lines)

            return cleaned

        except Exception:
            return ""

    # -----------------------------------------------------------------------
    # Message Filtering
    # -----------------------------------------------------------------------

    def _should_skip_message(self, metadata: GmailMessageMetadata, full_text: str) -> Tuple[bool, Optional[str]]:
        """Determine if a message should be skipped based on filtering criteria.

        Returns:
            Tuple of (should_skip, reason)
        """
        # Skip spam or trashed messages (defense in depth - query also excludes)
        if metadata.is_spam:
            return True, "Spam message"
        if metadata.is_trashed:
            return True, "Trashed message"

        # Skip empty messages
        if not full_text.strip():
            return True, "Empty message body"

        subject_lower = (metadata.subject or "").lower()
        from_lower = (metadata.from_email or "").lower()

        # Newsletter / promotional patterns
        newsletter_patterns = [
            'weekly', 'digest', 'newsletter', 'update', 'bulletin',
            'notification', 'alert', 'announcement',
        ]
        email_patterns = [
            'noreply@', 'no-reply@', 'donotreply@', 'no_reply@',
            'notifications@', 'alerts@', 'digest@', 'newsletter@',
        ]

        is_newsletter = (
            any(pattern in subject_lower for pattern in newsletter_patterns) or
            any(pattern in from_lower for pattern in email_patterns)
        )

        if is_newsletter:
            # Don't auto-skip - flag for review
            return False, "Newsletter or promotional email - flagged for review"

        # Promotional email patterns
        promo_patterns = [
            'sale', 'discount', 'offer', 'buy now', 'limited time',
            'free shipping', 'special offer', 'promotion', 'clearance',
            'deal of the day', 'just for you',
        ]

        if any(pattern in subject_lower for pattern in promo_patterns):
            return True, "Promotional email"

        # Receipt and system notification patterns
        receipt_patterns = [
            'receipt', 'invoice', 'order confirmation', 'receipt for',
            'payment received', 'transaction', 'subscription',
        ]

        if any(pattern in subject_lower for pattern in receipt_patterns):
            return True, "Receipt or transactional email"

        return False, None

    # -----------------------------------------------------------------------
    # Inquiry Extraction
    # -----------------------------------------------------------------------

    def _extract_inquiry_from_message(
        self, message_response: dict, metadata: GmailMessageMetadata
    ) -> GmailExtractionResult:
        """Extract an inquiry from a Gmail message.

        Uses BrowserAdapter's extraction logic for text parsing and classification.

        Args:
            message_response: Full Gmail API message response dict
            metadata: Parsed message metadata

        Returns:
            GmailExtractionResult with status, inquiry, and warnings
        """
        # Build full text for extraction
        full_text = self._build_extraction_text(metadata)

        # Check for spam/newsletter patterns in content
        if self._looks_like_spam(full_text, metadata):
            return GmailExtractionResult(
                status=GmailExtractionStatus.NO_INQUIRY,
                error_message="Content classified as spam or non-inquiry"
            )

        # Use browser adapter extraction logic for consistency
        from leadlift.adapters.browser import BrowserAdapter, ExtractionStatus

        async def _run_extraction():
            async with BrowserAdapter() as adapter:
                # Build HTML with proper structure so name extraction works
                # BrowserAdapter's _parse_inquiry_content expects "From: Name <email>" format
                html_content = self._build_html_for_extraction(metadata, full_text)
                return await adapter.extract_from_html(
                    html_content,
                    base_url=f"https://mail.google.com/mail/u/0/#inbox/{metadata.message_id}"
                )

        try:
            import asyncio
            result = asyncio.run(_run_extraction())

            if result.status == ExtractionStatus.SUCCESS and result.inquiry:
                inquiry = self._map_to_email_inquiry(result.inquiry, metadata, full_text)

                # Check if inquiry has any meaningful content beyond spam patterns
                if not inquiry.original_text or not inquiry.original_text.strip():
                    return GmailExtractionResult(
                        status=GmailExtractionStatus.INSUFFICIENT,
                        error_message="Extracted inquiry has no meaningful content"
                    )

                return GmailExtractionResult(
                    status=GmailExtractionStatus.SUCCESS,
                    inquiry=inquiry,
                )

            elif result.status == ExtractionStatus.NO_INQUIRY:
                return GmailExtractionResult(
                    status=GmailExtractionStatus.NO_INQUIRY,
                    error_message=result.error_message
                )

            elif result.status == ExtractionStatus.INSUFFICIENT:
                return GmailExtractionResult(
                    status=GmailExtractionStatus.INSUFFICIENT,
                    error_message=result.error_message
                )

            else:
                return GmailExtractionResult(
                    status=GmailExtractionStatus.ERROR,
                    error_message=f"Extraction error: {result.error_message}"
                )

        except Exception as e:
            return GmailExtractionResult(
                status=GmailExtractionStatus.ERROR,
                error_message=f"Extraction failed: {str(e)}"
            )

    def _build_extraction_text(self, metadata: GmailMessageMetadata) -> str:
        """Build text content suitable for inquiry extraction."""
        lines = []

        if metadata.from_name:
            lines.append(f"From: {metadata.from_name}")
        if metadata.from_email:
            if metadata.from_name:
                lines.append(f"Email: {metadata.from_email}")
            else:
                lines.append(f"From: {metadata.from_email}")

        if metadata.subject:
            lines.append(f"Subject: {metadata.subject}")

        # Add body content
        body_text = metadata.body_preview

        # Also try to get full body if available in the raw response
        lines.append("")
        lines.append(body_text)

        return "\n".join(lines)

    def _build_html_for_extraction(self, metadata: GmailMessageMetadata, full_text: str) -> str:
        """Build HTML-wrapped content so BrowserAdapter can parse name from From header."""
        lines = []
        if metadata.from_name and metadata.from_email:
            lines.append(f"From: {metadata.from_name} &lt;{metadata.from_email}&gt;")
        elif metadata.from_email:
            lines.append(f"From: {metadata.from_email}")
        if metadata.subject:
            lines.append(f"Subject: {metadata.subject}")
        lines.append("")
        lines.append(full_text)
        return "\n".join(lines)

    def _looks_like_spam(self, text: str, metadata: GmailMessageMetadata) -> bool:
        """Check if the message content looks like spam or a non-inquiry."""
        text_lower = text.lower()

        # Strong spam indicators (return True = spam)
        if re.search(
            r"(?:weekly|digest|newsletter)[:\s]|check out|limited time offer|"
            r"guaranteed results|click here|buy now|50% off|discount",
            text_lower
        ):
            return True

        # System notification patterns
        if re.search(
            r"notification|alert|automated message|do not reply|donotreply",
            text_lower
        ):
            return True

        return False

    def _map_to_email_inquiry(
        self, inquiry: Inquiry, metadata: GmailMessageMetadata, full_text: str
    ) -> Inquiry:
        """Map an extracted inquiry to an email-sourced inquiry.

        Updates the inquiry with Gmail-specific fields and metadata:
        - Sets source to EMAIL
        - Sets source message ID (Gmail message ID)
        - Sets source conversation ID (Gmail thread ID)
        - Preserves original email identifiers separately
        - Normalizes content without representing inferred info as explicit
        """
        inquiry.source = InquirySource.EMAIL
        inquiry.source_platform = "gmail"
        inquiry.source_message_id = metadata.message_id
        inquiry.source_conversation_id = metadata.thread_id
        inquiry.source_account = metadata.from_email

        # Set customer email from From header (explicit in email)
        if metadata.from_email:
            inquiry.customer_email = metadata.from_email
        if metadata.from_name:
            inquiry.customer_name = metadata.from_name

        # Preserve received timestamp from email date header
        if metadata.date:
            inquiry.received_at = self._parse_email_date(metadata.date)

        # Preserve original text as the email body (the actual message content)
        inquiry.original_text = full_text.strip()[:10000]

        # Calculate fingerprint for deduplication
        inquiry.text_fingerprint = self._calculate_fingerprint(inquiry)

        return inquiry

    def _parse_email_date(self, date_header: str) -> str:
        """Parse email date header to ISO 8601 format."""
        if not date_header:
            return datetime.utcnow().isoformat() + "Z"

        try:
            from email.utils import parsedate_to_datetime
            dt = parsedate_to_datetime(date_header)
            return dt.isoformat()
        except Exception:
            return datetime.utcnow().isoformat() + "Z"

    def _calculate_fingerprint(self, inquiry: Inquiry) -> str:
        """Calculate content fingerprint for deduplication.

        Uses SHA256 hash of normalized content to create stable fingerprint.
        Preserves message identifiers separately from content fingerprint.
        """
        text = f"{inquiry.customer_name or ''}:{inquiry.customer_email or ''}:"
        text += f"{inquiry.original_text.lower()[:500]}"
        normalized = re.sub(r"\s+", " ", text.lower()).strip()
        return hashlib.sha256(normalized.encode()).hexdigest()[:12]

    # -----------------------------------------------------------------------
    # Duplicate Detection
    # -----------------------------------------------------------------------

    def _check_for_duplicates(self, inquiry: Inquiry) -> List[Inquiry]:
        """Check for existing inquiries that match the new inquiry.

        Uses the repository's find_duplicates method with:
        - customer_email
        - text_fingerprint
        - source_conversation_id (thread ID)
        - source_message_id (message ID)

        Returns list of matching inquiries (empty if no duplicates).
        """
        repository = InquiryRepository()
        return repository.find_duplicates(
            customer_email=inquiry.customer_email,
            text_fingerprint=inquiry.text_fingerprint,
            conversation_id=inquiry.source_conversation_id,
            message_id=inquiry.source_message_id,
        )

    # -----------------------------------------------------------------------
    # Main Processing Entry Point
    # -----------------------------------------------------------------------

    async def process_gmail_intake(self) -> EmailIntakeResult:
        """Process Gmail messages and return extracted inquiries for review.

        This is the main entry point that:
        1. Authenticates with Gmail API
        2. Retrieves a bounded set of messages
        3. Parses message metadata and MIME content
        4. Extracts inquiry data
        5. Performs duplicate detection
        6. Returns proposed inquiries for review WITHOUT saving

        Returns:
            EmailIntakeResult with proposed inquiries and processing summary

        Raises:
            GmailAuthError: If authentication fails
            GmailRetrievalError: If API retrieval fails
        """
        settings = self.settings

        # Validate bounded retrieval settings
        max_messages = min(settings.gmail_max_retrieved_messages, 100)
        max_age_days = max(1, min(settings.gmail_max_message_age_days, 365))

        # Get Gmail service (validates credentials)
        try:
            service = self._get_service()
        except GmailAuthError:
            raise

        # Retrieve message IDs with pagination
        message_ids = self._retrieve_message_ids(service)

        # Process each message
        proposed_inquiries: List[Inquiry] = []
        email_details: List[dict] = []
        processed_count = 0
        skipped_count = 0
        duplicates_count = 0
        errors: List[dict] = []

        for message_data in message_ids:
            message_id = message_data.get('id', '')
            thread_id = message_data.get('threadId', '')

            try:
                # Retrieve full message
                message_response = self._retrieve_message(service, message_id)

                # Parse metadata
                metadata = self._parse_message_metadata(message_response)

                # Extract body for text building
                metadata.body_preview = self._extract_message_body(message_response.get('payload', {}))

                # Build full text for extraction and filtering
                full_text = self._build_extraction_text(metadata)

                # Apply filtering
                should_skip, skip_reason = self._should_skip_message(metadata, full_text)

                if should_skip:
                    skipped_count += 1
                    email_details.append({
                        "message_id": message_id,
                        "thread_id": thread_id,
                        "subject": metadata.subject,
                        "from_email": metadata.from_email,
                        "status": "skipped",
                        "reason": skip_reason
                    })
                    continue

                # Flag for review if ambiguous
                is_flagged = skip_reason is not None

                # Extract inquiry from message
                result = self._extract_inquiry_from_message(message_response, metadata)

                if result.status == GmailExtractionStatus.SUCCESS and result.inquiry:
                    inquiry = result.inquiry

                    # Check for duplicates
                    duplicates = self._check_for_duplicates(inquiry)
                    if duplicates:
                        duplicates_count += 1
                        email_details.append({
                            "message_id": message_id,
                            "thread_id": thread_id,
                            "subject": metadata.subject,
                            "from_email": metadata.from_email,
                            "status": "duplicate",
                            "duplicate_of": duplicates[0].inquiry_id
                        })
                        continue

                    proposed_inquiries.append(inquiry)
                    processed_count += 1

                    email_details.append({
                        "message_id": message_id,
                        "thread_id": thread_id,
                        "subject": metadata.subject,
                        "from_email": metadata.from_email,
                        "status": "review",
                        "inquiry_id": inquiry.inquiry_id,
                        "flagged_for_review": is_flagged
                    })

                else:
                    skipped_count += 1
                    email_details.append({
                        "message_id": message_id,
                        "thread_id": thread_id,
                        "subject": metadata.subject,
                        "from_email": metadata.from_email,
                        "status": "skipped",
                        "reason": result.error_message or "No inquiry detected"
                    })

            except GmailRetrievalError as e:
                skipped_count += 1
                errors.append({
                    "field": "message_retrieval",
                    "message": f"Failed to retrieve message {message_id}: {str(e)}",
                    "message_id": message_id,
                    "severity": "error"
                })
                email_details.append({
                    "message_id": message_id,
                    "thread_id": thread_id,
                    "status": "error",
                    "reason": str(e)
                })

            except Exception as e:
                skipped_count += 1
                errors.append({
                    "field": "message_processing",
                    "message": f"Error processing message {message_id}: {str(e)}",
                    "message_id": message_id,
                    "severity": "error"
                })
                email_details.append({
                    "message_id": message_id,
                    "thread_id": thread_id,
                    "status": "error",
                    "reason": str(e)
                })

        return EmailIntakeResult(
            success=True,
            retrieved_count=len(message_ids),
            processed_count=processed_count,
            skipped_count=skipped_count,
            duplicates_count=duplicates_count,
            errors=errors,
            proposed_inquiries=proposed_inquiries,
            email_details=email_details
        )

    def process(self, csv_content: str = "", preview: bool = False) -> EmailIntakeResult:
        """Synchronous wrapper for process_gmail_intake (BaseAdapter protocol).

        Note: The Gmail adapter's main processing is async. Use process_gmail_intake
        for async usage, or process() for synchronous callers.
        """
        try:
            return asyncio.run(self.process_gmail_intake())
        except GmailAuthError as e:
            return EmailIntakeResult(
                success=False,
                errors=[{
                    "field": "gmail_auth",
                    "message": f"Gmail authentication failed: {str(e)}",
                    "severity": "error"
                }]
            )
        except Exception as e:
            # Check if error is about missing configuration
            error_msg_lower = str(e).lower()
            if "oauth" in error_msg_lower and ("not found" in error_msg_lower or "not configured" in error_msg_lower or "credentials" in error_msg_lower):
                return EmailIntakeResult(
                    success=False,
                    errors=[{
                        "field": "gmail_config",
                        "message": "Gmail OAuth configuration incomplete. Please configure client_id, client_secret, and refresh_token.",
                        "severity": "error"
                    }]
                )
            return EmailIntakeResult(
                success=False,
                errors=[{
                    "field": "gmail_auth",
                    "message": f"Gmail authentication failed: {str(e)}",
                    "severity": "error"
                }]
            )

    def to_csv_import_result(self):
        """Convert to CSVImportResult for compatibility with existing UI."""
        return CSVImportResult(
            accepted=self.processed_count,
            rejected=len(self.errors),
            duplicates=self.duplicates_count,
            errors=self.errors,
            warnings=self.warnings
        )

    # -----------------------------------------------------------------------
    # Save confirmed inquiry
    # -----------------------------------------------------------------------

    def save_confirmed_inquiry(self, inquiry: Inquiry) -> Inquiry:
        """Persist a confirmed inquiry to the database.

        This must ONLY be called after explicit user confirmation.

        Args:
            inquiry: Confirmed Inquiry to save

        Returns:
            The saved Inquiry (or existing duplicate)
        """
        repository = InquiryRepository()

        # Check for duplicates before saving
        duplicates = self._check_for_duplicates(inquiry)
        if duplicates:
            return duplicates[0]

        repository.save(inquiry)
        return inquiry


# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------

class GmailAuthError(Exception):
    """Raised when Gmail authentication fails."""
    pass


class GmailRetrievalError(Exception):
    """Raised when Gmail message retrieval fails."""
    pass


class GmailAPIError(Exception):
    """Raised for general Gmail API errors."""
    pass


# ---------------------------------------------------------------------------
# Convenience functions
# ---------------------------------------------------------------------------

def create_gmail_adapter(settings=None) -> GmailIntakeAdapter:
    """Create a GmailIntakeAdapter instance."""
    return GmailIntakeAdapter(settings=settings)


def save_confirmed_email_inquiry(inquiry: Inquiry) -> Inquiry:
    """Save a confirmed email inquiry (convenience function)."""
    adapter = GmailIntakeAdapter()
    return adapter.save_confirmed_inquiry(inquiry)


# Re-export for compatibility
ExtractionStatus = GmailExtractionStatus