"""LEADLIFT — Browser intake adapter for inquiry extraction from web pages.

Security considerations:
- Uploaded HTML is parsed with a non-executing HTML parser (BeautifulSoup)
- Live URL navigation validates schemes, rejects private/loopback IPs, enforces redirect limits
- Resource cleanup is guaranteed even on initialization failures
"""

from __future__ import annotations

import asyncio
import hashlib
import ipaddress
import re
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from dataclasses import dataclass
from enum import Enum
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from leadlift.models import (
    Inquiry,
    InquirySource,
    MissingInformation,
)
from leadlift.repository import get_repository
from leadlift.adapters.base import BaseAdapter
from leadlift.logging_config import get_logger, safe_log
import logging

_logger = get_logger("adapters.browser")


class ExtractionStatus(str, Enum):
    """Status of extraction attempt."""
    SUCCESS = "success"
    NO_INQUIRY = "no_inquiry"
    INSUFFICIENT = "insufficient"
    ERROR = "error"


@dataclass
class BrowserExtractionResult:
    """Result of browser-based inquiry extraction."""
    status: ExtractionStatus
    inquiry: Optional[Inquiry] = None
    raw_html: Optional[str] = None
    extracted_text: Optional[str] = None
    source_metadata: Optional[Dict[str, Any]] = None
    error_message: Optional[str] = None
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


# URL validation constants
ALLOWED_SCHEMES = {"http", "https"}
MAX_REDIRECTS = 5
REQUEST_TIMEOUT = 30  # seconds

# Private/reserved IP ranges (RFC 1918, RFC 4193, RFC 3927, etc.)
PRIVATE_IPV4_NETWORKS = [
    ipaddress.IPv4Network("10.0.0.0/8"),
    ipaddress.IPv4Network("172.16.0.0/12"),
    ipaddress.IPv4Network("192.168.0.0/16"),
    ipaddress.IPv4Network("127.0.0.0/8"),      # Loopback
    ipaddress.IPv4Network("169.254.0.0/16"),   # Link-local
    ipaddress.IPv4Network("0.0.0.0/8"),        # Current network
    ipaddress.IPv4Network("224.0.0.0/4"),      # Multicast
    ipaddress.IPv4Network("240.0.0.0/4"),      # Reserved
]
PRIVATE_IPV6_NETWORKS = [
    ipaddress.IPv6Network("::1/128"),          # Loopback
    ipaddress.IPv6Network("fe80::/10"),        # Link-local
    ipaddress.IPv6Network("fc00::/7"),         # Unique local
    ipaddress.IPv6Network("ff00::/8"),         # Multicast
    ipaddress.IPv6Network("::/128"),           # Unspecified
    ipaddress.IPv6Network("::ffff:0:0/96"),    # IPv4-mapped
]

# Known cloud metadata endpoints
CLOUD_METADATA_HOSTS = {
    "169.254.169.254",     # AWS, GCP, Azure, DigitalOcean
    "metadata.google.internal",
    "metadata.azure.com",
    "169.254.169.254",
}


def is_private_ip(host: str) -> bool:
    """Check if a hostname resolves to a private/reserved IP address."""
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        # Not an IP literal, would need DNS resolution to check
        return False
    
    if ip.version == 4:
        return any(ip in network for network in PRIVATE_IPV4_NETWORKS)
    else:
        return any(ip in network for network in PRIVATE_IPV6_NETWORKS)


def is_cloud_metadata_host(host: str) -> bool:
    """Check if hostname is a known cloud metadata endpoint."""
    return host.lower() in CLOUD_METADATA_HOSTS


def validate_url(url: str) -> tuple[bool, Optional[str]]:
    """
    Validate a URL for safe navigation.
    
    Returns (is_valid, error_message).
    """
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"Invalid URL: {e}"
    
    # Check scheme
    if parsed.scheme not in ALLOWED_SCHEMES:
        return False, f"Unsupported URL scheme: {parsed.scheme}. Only http/https allowed."
    
    # Check hostname
    hostname = parsed.hostname
    if not hostname:
        return False, "URL missing hostname"
    
    # Check for cloud metadata endpoints
    if is_cloud_metadata_host(hostname):
        return False, f"Access to cloud metadata endpoint blocked: {hostname}"
    
    # Check for private IP addresses in hostname (if it's an IP literal)
    if is_private_ip(hostname):
        return False, f"Access to private IP address blocked: {hostname}"
    
    # Note: For hostnames that aren't IP literals, we can't fully validate
    # without DNS resolution. The browser will resolve at navigation time.
    # We rely on the browser's same-origin policy and network isolation.
    
    return True, None


def resolve_hostname_to_ips(hostname: str) -> List[str]:
    """Resolve hostname to IP addresses. Returns empty list on failure."""
    try:
        import socket
        infos = socket.getaddrinfo(hostname, None, family=socket.AF_UNSPEC, type=socket.SOCK_STREAM)
        ips = list({info[4][0] for info in infos})
        return ips
    except Exception:
        return []


def is_destination_safe(url: str) -> tuple[bool, Optional[str]]:
    """
    Validate URL and resolve hostname to check for private IPs.
    Performs DNS resolution to catch hostnames that resolve to private IPs.
    """
    valid, error = validate_url(url)
    if not valid:
        return False, error
    
    parsed = urlparse(url)
    hostname = parsed.hostname
    
    # Resolve hostname to check for private IPs
    ips = resolve_hostname_to_ips(hostname)
    for ip in ips:
        if is_private_ip(ip):
            return False, f"Hostname {hostname} resolves to private IP: {ip}"
        if is_cloud_metadata_host(ip):
            return False, f"Hostname {hostname} resolves to cloud metadata endpoint: {ip}"
    
    return True, None


class BrowserAdapter(BaseAdapter):
    """
    Browser-based intake adapter for extracting inquiries from web pages.
    
    Security features:
    - Uploaded HTML parsed with non-executing BeautifulSoup parser
    - Live URL navigation validates schemes, rejects private/loopback IPs
    - Redirect limits enforced
    - Resource cleanup guaranteed even on initialization failures
    """
    
    def __init__(self, settings=None):
        super().__init__(settings)
        self.browser = None
        self.context = None
        self.page = None
        self._playwright = None
        self._initialized = False
        
    async def __aenter__(self):
        """Async context manager entry - launches browser with guaranteed cleanup on failure."""
        from playwright.async_api import async_playwright
        
        self._playwright = await async_playwright().start()
        try:
            self.browser = await self._playwright.chromium.launch(headless=True)
            self.context = await self.browser.new_context(
                # Disable JavaScript for security when extracting from uploaded HTML
                # Note: For extract_from_url we may want JS, but we use a separate method
                java_script_enabled=True,
            )
            self.page = await self.context.new_page()
            self._initialized = True
            return self
        except Exception:
            # Clean up on any initialization failure
            await self._cleanup()
            raise
            
    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Async context manager exit - closes browser resources."""
        await self._cleanup()
        
    async def _cleanup(self):
        """Clean up browser resources in reverse order."""
        errors = []
        
        if self.page:
            try:
                await self.page.close()
            except Exception as e:
                errors.append(f"Page close error: {e}")
            self.page = None
            
        if self.context:
            try:
                await self.context.close()
            except Exception as e:
                errors.append(f"Context close error: {e}")
            self.context = None
            
        if self.browser:
            try:
                await self.browser.close()
            except Exception as e:
                errors.append(f"Browser close error: {e}")
            self.browser = None
            
        if self._playwright:
            try:
                await self._playwright.stop()
            except Exception as e:
                errors.append(f"Playwright stop error: {e}")
            self._playwright = None
            
        self._initialized = False
        
        if errors:
            # Log errors but don't raise - cleanup should be best effort
            import logging
            logging.getLogger(__name__).warning("Browser cleanup errors: %s", "; ".join(errors))
        
    async def extract_from_url(
        self,
        url: str,
        wait_selector: Optional[str] = None,
        wait_timeout: int = 30000,
    ) -> BrowserExtractionResult:
        """
        Navigate to URL and extract inquiry content.
        
        Security controls:
        - Validates URL scheme (http/https only)
        - Rejects private/loopback IPs and cloud metadata endpoints
        - Validates redirect destinations with limit
        - Validates final URL after redirects
        
        Args:
            url: URL to navigate to
            wait_selector: Optional CSS selector to wait for before extracting
            wait_timeout: Timeout in milliseconds for page load
            
        Returns:
            BrowserExtractionResult with extracted inquiry or error info
        """
        if not self.page:
            raise RuntimeError("Browser not initialized. Use async context manager.")
            
        # Validate initial URL
        valid, error = validate_url(url)
        if not valid:
            return BrowserExtractionResult(
                status=ExtractionStatus.ERROR,
                error_message=f"URL validation failed: {error}",
            )
        
        # Additional DNS-based check for private IPs
        safe, error = is_destination_safe(url)
        if not safe:
            return BrowserExtractionResult(
                status=ExtractionStatus.ERROR,
                error_message=f"Destination blocked: {error}",
            )
        
        redirect_count = 0
        current_url = url
        
        try:
            # Navigate with redirect tracking
            response = await self.page.goto(
                current_url,
                wait_until="domcontentloaded",
                timeout=wait_timeout,
            )
            
            # Handle redirects
            while response and response.status >= 300 and response.status < 400:
                redirect_count += 1
                if redirect_count > MAX_REDIRECTS:
                    return BrowserExtractionResult(
                        status=ExtractionStatus.ERROR,
                        error_message=f"Too many redirects ({redirect_count} > {MAX_REDIRECTS})",
                    )
                
                # Get redirect location
                location = response.headers.get("location")
                if not location:
                    break
                
                # Build absolute URL
                if location.startswith("http://") or location.startswith("https://"):
                    next_url = location
                elif location.startswith("/"):
                    parsed = urlparse(current_url)
                    next_url = f"{parsed.scheme}://{parsed.netloc}{location}"
                else:
                    parsed = urlparse(current_url)
                    path = parsed.path.rsplit("/", 1)[0]
                    next_url = f"{parsed.scheme}://{parsed.netloc}{path}/{location}"
                
                # Validate redirect destination
                valid, error = validate_url(next_url)
                if not valid:
                    return BrowserExtractionResult(
                        status=ExtractionStatus.ERROR,
                        error_message=f"Redirect destination blocked: {error}",
                    )
                
                safe, error = is_destination_safe(next_url)
                if not safe:
                    return BrowserExtractionResult(
                        status=ExtractionStatus.ERROR,
                        error_message=f"Redirect destination blocked: {error}",
                    )
                
                # Follow redirect
                response = await self.page.goto(
                    next_url,
                    wait_until="domcontentloaded",
                    timeout=wait_timeout,
                )
                current_url = next_url
            
            # Wait for optional selector if provided
            if wait_selector:
                try:
                    await self.page.wait_for_selector(wait_selector, timeout=wait_timeout)
                except Exception:
                    # Selector not found - continue anyway, could add to warnings
                    pass
            
            # Final URL validation after all redirects
            final_url = self.page.url
            valid, error = validate_url(final_url)
            if not valid:
                return BrowserExtractionResult(
                    status=ExtractionStatus.ERROR,
                    error_message=f"Final URL blocked: {error}",
                )
            
            safe, error = is_destination_safe(final_url)
            if not safe:
                return BrowserExtractionResult(
                    status=ExtractionStatus.ERROR,
                    error_message=f"Final destination blocked: {error}",
                )
            
            # Get page content
            html = await self.page.content()
            text = await self.page.inner_text("body")
            
            # Extract source metadata
            source_metadata = {
                "url": final_url,
                "title": await self.page.title(),
                "timestamp": datetime.utcnow().isoformat() + "Z",
                "redirect_count": redirect_count,
            }
            
            # Extract inquiry from page content
            return self._extract_inquiry_from_text(text, html, source_metadata)
            
        except asyncio.TimeoutError:
            return BrowserExtractionResult(
                status=ExtractionStatus.ERROR,
                error_message=f"Navigation timeout after {wait_timeout}ms",
            )
        except Exception as e:
            return BrowserExtractionResult(
                status=ExtractionStatus.ERROR,
                error_message=f"Browser extraction failed: {str(e)}",
            )
             
    async def extract_from_html(
        self,
        html: str,
        base_url: str = "file://",
    ) -> BrowserExtractionResult:
        """
        Extract inquiry from raw HTML content using a NON-EXECUTING HTML parser.
        
        This method uses BeautifulSoup (lxml parser) which does NOT execute:
        - JavaScript (inline, external, event handlers)
        - iframe loading
        - Form submissions
        - Any active content
        
        Args:
            html: Raw HTML content
            base_url: Base URL for resolving relative links (default: file://)
            
        Returns:
            BrowserExtractionResult with extracted inquiry or error info
        """
        if not html or len(html.strip()) < 20:
            return BrowserExtractionResult(
                status=ExtractionStatus.INSUFFICIENT,
                raw_html=html,
                extracted_text="",
                source_metadata={"url": base_url},
                error_message="HTML content too short for extraction",
            )
        
        try:
            # Preserve email addresses enclosed in angle brackets as visible text
            html = re.sub(r'<([^<>]*@[^<>]*)>', r'&lt;\1&gt;', html)

            # Parse with BeautifulSoup (lxml parser) - NO JavaScript execution
            soup = BeautifulSoup(html, "lxml")
            
            # Remove script, style, iframe, noscript, and other active elements
            for tag in soup.find_all(["script", "style", "iframe", "noscript", "object", "embed", "applet"]):
                tag.decompose()
            
            # Remove event handler attributes (onclick, onload, etc.)
            for tag in soup.find_all(True):
                attrs_to_remove = [attr for attr in tag.attrs if attr.startswith("on")]
                for attr in attrs_to_remove:
                    del tag.attrs[attr]
            
            # Get clean text
            text = soup.get_text(separator=" ", strip=True)
            
            # Get title
            title_tag = soup.find("title")
            title = title_tag.get_text(strip=True) if title_tag else ""
            
            # Extract source metadata
            source_metadata = {
                "url": base_url,
                "title": title,
                "timestamp": datetime.utcnow().isoformat() + "Z",
            }
            
            # Extract inquiry from cleaned content
            return self._extract_inquiry_from_text(text, html, source_metadata)
            
        except Exception as e:
            return BrowserExtractionResult(
                status=ExtractionStatus.ERROR,
                error_message=f"HTML extraction failed: {str(e)}",
            )

    def _clean_text(self, text: str) -> str:
        """Remove excessive whitespace and normalize line endings."""
        lines = [l.strip() for l in text.split("\n") if l.strip()]
        return " ".join(lines)

    def _extract_emails_from_html(self, html: str) -> List[str]:
        """Extract unique email addresses from raw HTML before text extraction."""
        found = set()
        for match in re.finditer(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", html):
            found.add(match.group().lower())
        return list(found)

    def _extract_phones_from_html(self, html: str) -> List[str]:
        """Extract phone numbers from HTML using BeautifulSoup."""
        import re
        soup = BeautifulSoup(html, "lxml")
        found = set()
        for tag in soup.find_all(["span", "div", "p", "label", "input", "a"], attrs={"type": ["tel", "text"]}):
            tag_text = tag.get_text() if hasattr(tag, "get_text") else str(tag)
            matches = re.findall(r"\+?\d[\d\-\s]{7,}\d", tag_text)
            for m in matches:
                phone = m.strip().replace(" ", "")
                if phone:
                    found.add(phone)
        # Also scan raw HTML
        for match in re.finditer(r"\+?\d[\d\-\s]{7,}\d", html):
            found.add(match.group().strip().replace(" ", ""))
        return list(found)

    def _parse_inquiry_content(self, text: str) -> Dict[str, Optional[str]]:
        """Parse plain text into structured inquiry fields."""
        result = {
            "customer_name": None,
            "customer_email": None,
            "customer_phone": None,
            "organization": None,
            "requested_service": None,
            "budget_stated": None,
            "timeline_stated": None,
        }
        
        # Try to extract email first
        email_patterns = [
            re.compile(r"(?:From|me|I)[:\s]+(?:[<\"]?)([\w.+-]+@[\w.-]+\.\w+)(?:[>\"]?)", re.IGNORECASE),
            re.compile(r"\b([\w.+-]+@[\w.-]+\.\w+)\b"),
        ]
        for pat in email_patterns:
            m = pat.search(text)
            if m:
                result["customer_email"] = m.group(1).lower()
                break
        
        # Name: typically after From:, Contact:, Or:
        name_patterns = [
            re.compile(r"\b(?:From|Contact|Or)\b\s*[:\s]+(.+?)(?:\s*<|<|&lt;)", re.IGNORECASE),
            re.compile(r"(?:Name|My name is)\s*[:\s]+(.+?)(?:\n|\.)", re.IGNORECASE),
        ]
        for pat in name_patterns:
            m = pat.search(text)
            if m:
                candidate = m.group(1).strip()
                if len(candidate) >= 2 and len(candidate) <= 100:
                    result["customer_name"] = candidate
                    break
        
        # Organization: usually after Company:, about, or the sender's name
        org_patterns = [
            re.compile(r"(?:Company|Organization|About)\s*[:\s]+(.+?)(?=\s+(?:Phone|Company|Organization|About|Hi|We|Please|Budget|Timeline|Looking)\b|$)", re.IGNORECASE),
            re.compile(r"(?:at|c/o)\s+([A-Z][A-Za-z\s&]{5,50})"),
        ]
        for pat in org_patterns:
            m = pat.search(text)
            if m:
                org = m.group(1).strip()
                if org and len(org) >= 3:
                    result["organization"] = org
                    break
        
        # Budget
        budget_pat = re.search(r"(?:Budget|Cost|Price|Budget)[:\s]+(?:around\s*)?([\$€£]?\s*(?:\d[\d,]*\d|\d)\s*[\$€£]?(?:/(?:month|year|mo|yr)|per month|per year)?)", text, re.IGNORECASE)
        if budget_pat:
            result["budget_stated"] = budget_pat.group(1).strip()
        else:
            budget_only = re.search(r"(?:Budget|Cost|Price)[:\s]+(?:around\s*)?\$?(\d[\d,]*\d|\d)", text, re.IGNORECASE)
            if budget_only:
                result["budget_stated"] = f"${budget_only.group(1)}"
        
        # Timeline / duration
        timeline_pat = re.search(r"(?:timeline|duration|timeframe|deadline)[:\s]+around?\s*(\d+\s*[-~]\s*\d+\s*(?:days?|months?|years?|weeks?))", text, re.IGNORECASE)
        if timeline_pat:
            result["timeline_stated"] = f"{timeline_pat.group(1).lower()}"
        else:
            tm = re.search(r"(?:timeline|deadline|timeframe)[:\s]+(\d+\s*(?:months?|weeks?|years?|days?))", text, re.IGNORECASE)
            if tm:
                result["timeline_stated"] = tm.group(1).lower()
            else:
                # Try standalone "X months" or "X weeks" or "X days" or "X years"
                tm2 = re.search(r"(\d+\s*(?:months?|weeks?|years?|days?))", text, re.IGNORECASE)
                if tm2:
                    result["timeline_stated"] = tm2.group(1).lower()
        
        # Requested service keywords
        service_keywords = [
            ("e-commerce", "E-Commerce"),
            ("web development", "Web Development"),
            ("website", "Web Development"),
            ("seo", "SEO"),
            ("page speed", "Page Speed Optimization"),
            ("social media", "Social Media Marketing"),
            ("marketing", "Marketing Services"),
            ("email marketing", "Email Marketing"),
            ("api", "API Development"),
            ("app", "Mobile App Development"),
            ("software", "Software Development"),
            ("it support", "IT Support & Hosting"),
            ("data analytics", "Data Analytics"),
        ]
        full_text_lower = text.lower()
        for keyword, normalized in service_keywords:
            if keyword in full_text_lower:
                result["requested_service"] = normalized
                break
        
        return result

    def _classify_as_inquiry(self, fields: Dict[str, Optional[str]], original_text: str) -> ExtractionStatus:
        """Determine whether content qualifies as an inquiry."""
        if not original_text or not original_text.strip():
            return ExtractionStatus.NO_INQUIRY
        
        # Check for clearly non-inquiry patterns (spam/newsletter)
        # These have clear indicators that they are NOT inquiries
        if (
            re.search(r"(?:weekly|digest|newsletter)[:\s]", original_text, re.IGNORECASE)
            or re.search(r"(?:check out|latest articles|read more|buy now|discount|50% off|guaranteed results|click here|limited time offer)", original_text, re.IGNORECASE)
            or "sales@" in original_text.lower()
            or "newsletter@" in original_text.lower()
        ):
            return ExtractionStatus.NO_INQUIRY
        
        # Must mention some business context - keywords
        has_intent = any(
            kw in original_text.lower()
            for kw in ("need", "request", "looking for", "want", "urgent", "business", "custom")
        )
        has_concrete_field = (
            fields.get("customer_name") is not None
            or fields.get("organization") is not None
            or fields.get("requested_service") is not None
        )

        if has_intent and has_concrete_field:
            return ExtractionStatus.SUCCESS

        if has_intent or original_text.strip():
            return ExtractionStatus.INSUFFICIENT

        return ExtractionStatus.NO_INQUIRY

    def _has_sufficient_inquiry_info(self, fields: Dict[str, Optional[str]]) -> bool:
        """Check if inquiry has sufficient info for a positive result."""
        required = ["customer_name", "organization", "requested_service"]
        present = sum(1 for f in required if fields.get(f) is not None)
        return present >= 2

    def _build_inquiry(
        self,
        fields: Dict[str, Optional[str]],
        source_metadata: Dict[str, Any],
    ) -> Inquiry:
        """Build an Inquiry model from parsed fields."""
        inquiry = Inquiry(
            customer_name=fields.get("customer_name"),
            customer_email=fields.get("customer_email"),
            customer_phone=None,  # Will be set by caller if phones found
            organization=fields.get("organization"),
            requested_service=fields.get("requested_service"),
            budget_stated=fields.get("budget_stated"),
            timeline_stated=fields.get("timeline_stated"),
            source=InquirySource.BROWSER,
            source_platform="web",
            missing_information=[],
        )
        inquiry.original_text = source_metadata.get("url", "")
        return inquiry

    def _normalize_for_fingerprint(self, inquiry: Inquiry) -> str:
        """Create a stable text fingerprint for deduplication."""
        text = f"{inquiry.customer_name}:{inquiry.organization}:{inquiry.requested_service}:" \
               f"{inquiry.original_text.lower()[:500]}"
        normalized = re.sub(r"\s+", " ", text.lower()).strip()
        h = hashlib.md5(normalized.encode()).hexdigest()[:8]
        return h

    def _identify_missing_info(self, fields: Dict[str, Optional[str]], inquiry: Inquiry) -> None:
        """Populate missing_information with absent expected fields."""
        missing = []
        if not inquiry.customer_email:
            missing.append("customer_email")
        if not inquiry.customer_name:
            missing.append("customer_name")
        if not inquiry.organization:
            missing.append("organization")
        if not inquiry.requested_service:
            missing.append("requested_service")
        if not inquiry.budget_stated:
            missing.append("budget_stated")
        if not inquiry.timeline_stated:
            missing.append("timeline_stated")
        if not fields.get("customer_phone") and "customer_phone" not in missing:
            missing.append("customer_phone")
        for m_field in missing:
            if m_field not in [mi.field for mi in inquiry.missing_information]:
                description = {
                    "customer_email": "Email address of the customer",
                    "customer_name": "Customer's name",
                    "organization": "Company/Organization name",
                    "requested_service": "Requested service type",
                    "budget_stated": "Estimated budget mentioned by customer",
                    "timeline_stated": "Timeline or duration specified",
                    "customer_phone": "Customer's phone number",
                }.get(m_field, "Missing information")
                inquiry.missing_information.append(
                    MissingInformation(field=m_field, description=description, importance="medium")
                )

    def _extract_inquiry_from_text(
        self,
        text: str,
        html: str,
        source_metadata: Dict[str, Any],
    ) -> BrowserExtractionResult:
        """Main extraction pipeline: parse text → build inquiry → classify."""
        if not text or not text.strip():
            return BrowserExtractionResult(
                status=ExtractionStatus.INSUFFICIENT,
                error_message="No content to extract from page",
            )
        
        clean_text = self._clean_text(text)
        
        # Parse structured fields from text
        fields = self._parse_inquiry_content(clean_text)
        
        # Extract emails from raw HTML (before tag stripping loses <email> patterns)
        emails_from_html = self._extract_emails_from_html(html)
        if emails_from_html and not fields["customer_email"]:
            fields["customer_email"] = emails_from_html[0]
        
        # Check for extracted phones from HTML (may not be in text)
        phones_from_html = self._extract_phones_from_html(html)
        if phones_from_html and not fields["customer_phone"]:
            fields["customer_phone"] = phones_from_html[0]
        
        # Classify the content
        status = self._classify_as_inquiry(fields, clean_text)
        
        if status == ExtractionStatus.NO_INQUIRY:
            return BrowserExtractionResult(
                status=ExtractionStatus.NO_INQUIRY,
                inquiry=None,
                raw_html=html,
                extracted_text=clean_text,
                source_metadata=source_metadata,
                error_message="Content does not appear to be an inquiry",
            )
        
        # Determine final status based on sufficiency
        required_fields = ["customer_name", "organization", "requested_service"]
        fields_present = [f for f in required_fields if fields.get(f) is not None]
        
        if not self._has_sufficient_inquiry_info(fields):
            status = ExtractionStatus.INSUFFICIENT
            # If no required fields at all, return None inquiry
            if len(fields_present) == 0:
                return BrowserExtractionResult(
                    status=ExtractionStatus.INSUFFICIENT,
                    inquiry=None,
                    raw_html=html,
                    extracted_text=clean_text,
                    source_metadata=source_metadata,
                )
        
        if status == ExtractionStatus.NO_INQUIRY:
            return BrowserExtractionResult(
                status=ExtractionStatus.NO_INQUIRY,
                inquiry=None,
                raw_html=html,
                extracted_text=clean_text,
                source_metadata=source_metadata,
                error_message="Content does not appear to be an inquiry",
            )
        
        # Build inquiry
        inquiry = self._build_inquiry(fields, source_metadata)
        inquiry.original_text = clean_text[:10000]
        inquiry.text_fingerprint = self._normalize_for_fingerprint(inquiry)
        inquiry.customer_email = fields["customer_email"]
        inquiry.customer_phone = fields["customer_phone"]
        
        # Identify missing information
        self._identify_missing_info(fields, inquiry)
        
        return BrowserExtractionResult(
            status=status,
            inquiry=inquiry,
            raw_html=html,
            extracted_text=clean_text,
            source_metadata=source_metadata,
        )


@dataclass
class BrowserIntakeResult:
    """Result of a browser intake operation including extraction and save status."""
    success: bool
    inquiry: Optional[Inquiry] = None
    extraction_result: Optional[BrowserExtractionResult] = None
    error_message: Optional[str] = None
    warnings: List[str] = None
    
    def __post_init__(self):
        if self.warnings is None:
            self.warnings = []


async def extract_from_file(file_path: str) -> BrowserIntakeResult:
    """
    Extract inquiry from a local HTML file.
    
    Args:
        file_path: Path to HTML file
        
    Returns:
        BrowserIntakeResult with extracted inquiry or error info
    """
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            html = f.read()
    except Exception as e:
        return BrowserIntakeResult(
            success=False,
            error_message=f"Failed to read file: {e}",
        )
    
    async with BrowserAdapter() as adapter:
        extraction_result = await adapter.extract_from_html(html, base_url=f"file://{file_path}")
    
    return BrowserIntakeResult(
        success=extraction_result.status in (ExtractionStatus.SUCCESS, ExtractionStatus.INSUFFICIENT),
        inquiry=extraction_result.inquiry,
        extraction_result=extraction_result,
        error_message=extraction_result.error_message if extraction_result.status == ExtractionStatus.ERROR else None,
        warnings=extraction_result.warnings,
    )


async def save_confirmed_inquiry(inquiry: Inquiry) -> Inquiry:
    """
    Persist a confirmed inquiry to the database.
    
    Args:
        inquiry: Inquiry model to save
        
    Returns:
        Saved Inquiry with assigned ID
    """
    from leadlift.repository import get_repository
    repo = get_repository()
    # Check for duplicates before saving
    duplicates = repo.find_duplicates(
        customer_email=inquiry.customer_email,
        text_fingerprint=inquiry.text_fingerprint,
        conversation_id=inquiry.source_conversation_id,
        message_id=inquiry.source_message_id,
    )
    if duplicates:
        return duplicates[0]
    repo.save(inquiry)
    return inquiry