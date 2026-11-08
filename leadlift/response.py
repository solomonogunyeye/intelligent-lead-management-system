"""LEADLIFT — Response draft generation."""

from __future__ import annotations

from typing import Any

from leadlift.models import BusinessConfig, Inquiry, MissingInformation


class ResponseGenerator:
    """Generate editable response drafts for inquiries.

    Uses the inquiry data, business configuration, and missing information
    to create a professional response draft. Never fabricates prices,
    discounts, guarantees, or commitments.
    """

    def __init__(self, config: BusinessConfig):
        self.config = config

    def generate(self, inquiry: Inquiry) -> str:
        """Generate a response draft for the inquiry."""
        lines = []

        # Greeting
        if inquiry.customer_name:
            lines.append(f"Dear {inquiry.customer_name},")
        else:
            lines.append("Hello,")

        lines.append("")

        # Acknowledge the inquiry
        lines.append("Thank you for reaching out to us. We appreciate your interest.")

        # Reference their specific request if available
        if inquiry.requested_service:
            lines.append(f"We received your inquiry regarding {inquiry.requested_service}.")
        elif inquiry.original_text:
            snippet = inquiry.original_text[:200]
            if len(inquiry.original_text) > 200:
                snippet += "..."
            lines.append(f"Regarding your message: \"{snippet}\"")

        lines.append("")

        # Address stated requirements
        if inquiry.budget_stated:
            lines.append(f"You mentioned a budget of {inquiry.budget_stated}. "
                        "We'll take this into consideration when preparing our proposal.")
        if inquiry.timeline_stated:
            lines.append(f"Your stated timeline is {inquiry.timeline_stated}. "
                        "We'll do our best to accommodate this schedule.")

        lines.append("")

        # Missing information questions
        if inquiry.missing_information:
            lines.append("To provide you with the most accurate information, "
                        "we'd appreciate a few more details:")
            for mi in inquiry.missing_information:
                lines.append(f"- {mi.description}")
            lines.append("")

        # Next steps / recommended action
        if inquiry.recommended_next_action:
            lines.append(f"Our recommended next step: {inquiry.recommended_next_action}")
        else:
            lines.append("We would be happy to schedule a brief call to discuss your needs in detail "
                        "and provide a tailored proposal.")

        lines.append("")

        # Closing
        lines.append(f"Best regards,")
        lines.append(f"[Your Name]")
        lines.append(f"[Your Company]")
        lines.append("")
        lines.append("---")
        lines.append("This is a draft response. Please review and edit before sending.")

        return "\n".join(lines)

    def generate_short(self, inquiry: Inquiry) -> str:
        """Generate a shorter response draft."""
        if inquiry.customer_name:
            greeting = f"Hi {inquiry.customer_name},"
        else:
            greeting = "Hello,"

        body = "Thanks for your inquiry. "

        if inquiry.requested_service:
            body += f"Regarding {inquiry.requested_service}, "
        body += "we'd love to discuss your project. "

        if inquiry.missing_information:
            body += "Could you let us know " + " and ".join(
                mi.description for mi in inquiry.missing_information
            ) + "? "

        body += "Available for a call this week if that works for you."

        return f"{greeting}\n\n{body}\n\nBest,\n[Your Name]"