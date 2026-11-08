"""LEADLIFT — Deterministic priority calculation with human-readable explanations."""

from __future__ import annotations

from typing import Any, List

from leadlift.models import BusinessConfig, Inquiry, InquiryStatus


class PriorityCalculator:
    """Calculate inquiry priority using configurable business rules.

    The priority is a weighted score (0.0 to 1.0) derived from deterministic
    factors. Each factor contributes to a human-readable explanation.
    """

    def __init__(self, config: BusinessConfig):
        self.config = config

    def calculate(self, inquiry: Inquiry) -> tuple[float, str]:
        """Calculate priority score and explanation for an inquiry.

        Returns:
            (score, explanation) where score is 0.0 to 1.0 and explanation
            is a human-readable description of the decision.
        """
        factors: list[tuple[str, float, float, str]] = []  # (name, weight, score, description)

        # Factor 1: Service match (weight: 0.30)
        service_match = self._service_match_score(inquiry)
        factors.append(("Service Match", 0.30, service_match[0], service_match[1]))

        # Factor 2: Near-term deadline (weight: 0.20)
        deadline_score = self._deadline_score(inquiry)
        factors.append(("Deadline", 0.20, deadline_score[0], deadline_score[1]))

        # Factor 3: Staleness (weight: 0.15)
        staleness_score = self._staleness_score(inquiry)
        factors.append(("Staleness", 0.15, staleness_score[0], staleness_score[1]))

        # Factor 4: Existing customer (weight: 0.10)
        existing_customer = self._existing_customer_score(inquiry)
        factors.append(("Existing Customer", 0.10, existing_customer[0], existing_customer[1]))

        # Factor 5: Missing information (weight: 0.10)
        missing_info_score = self._missing_info_score(inquiry)
        factors.append(("Missing Info", 0.10, missing_info_score[0], missing_info_score[1]))

        # Factor 6: Spam classification (weight: 0.15)
        spam_score = self._spam_score(inquiry)
        factors.append(("Spam", 0.15, spam_score[0], spam_score[1]))

        # Calculate weighted score
        total_weight = sum(w for _, w, _, _ in factors)
        weighted_sum = sum(score * w for _, w, score, _ in factors)
        raw_score = weighted_sum / total_weight if total_weight > 0 else 0.0
        final_score = max(0.0, min(1.0, raw_score))

        # Build explanation
        explanation_parts = [f"Priority score: {final_score:.2f}"]
        for name, weight, _, desc in factors:
            explanation_parts.append(f"- {name}: {desc} (weight: {weight:.2f})")

        explanation = "\n".join(explanation_parts)

        return final_score, explanation

    def _service_match_score(self, inquiry: Inquiry) -> tuple[float, str]:
        """Score based on whether the requested service matches business offerings."""
        if not inquiry.requested_service:
            return 0.5, "No service specified"

        service_lower = inquiry.requested_service.lower()
        for offering in self.config.services:
            if offering.lower() in service_lower or service_lower in offering.lower():
                return 1.0, f"Matches configured service: {offering}"

        return 0.3, f"Service '{inquiry.requested_service}' not in configured offerings"

    def _deadline_score(self, inquiry: Inquiry) -> tuple[float, str]:
        """Score based on whether the customer stated a near-term deadline.
        
        Only uses explicitly stated timeline, not inferred, for scoring.
        """
        timeline = inquiry.timeline_stated
        if not timeline:
            return 0.5, "No timeline stated by customer"

        timeline_lower = timeline.lower()
        near_terms = ["urgent", "asap", "immediately", "today", "tomorrow",
                       "this week", "next week", "within", "by"]
        for term in near_terms:
            if term in timeline_lower:
                return 1.0, f"Near-term deadline: {timeline}"

        return 0.4, f"Timeline stated but not clearly urgent: {timeline}"

    def _staleness_score(self, inquiry: Inquiry) -> tuple[float, str]:
        """Score based on how long the inquiry has been unanswered."""
        from datetime import datetime, timezone

        try:
            received = datetime.fromisoformat(inquiry.received_at.replace("Z", "+00:00"))
            now = datetime.now(timezone.utc)
            hours = (now - received).total_seconds() / 3600
        except (ValueError, TypeError):
            return 0.5, "Cannot parse received timestamp"

        threshold = self.config.stale_hours
        if hours >= threshold * 2:
            return 1.0, f"Very stale ({hours:.0f} hours, threshold: {threshold}h)"
        elif hours >= threshold:
            return 0.8, f"Stale ({hours:.0f} hours, threshold: {threshold}h)"
        elif hours >= threshold / 2:
            return 0.6, f"Approaching staleness ({hours:.0f} hours)"
        else:
            return 0.3, f"Recent ({hours:.0f} hours)"

    def _existing_customer_score(self, inquiry: Inquiry) -> tuple[float, str]:
        """Score based on whether the customer is an existing customer."""
        if inquiry.customer_email:
            return 0.6, "Customer email provided (potential existing customer)"
        if inquiry.customer_name:
            return 0.4, "Customer name provided"
        return 0.2, "No customer contact information"

    def _missing_info_score(self, inquiry: Inquiry) -> tuple[float, str]:
        """Score based on missing information (more missing = higher priority).
        
        However, for spam/low-quality inquiries, missing info should not boost priority.
        """
        # Check if inquiry is spam or low-quality
        is_spam_or_low_quality = False
        if inquiry.classification:
            cls = inquiry.classification.lower()
            if "spam" in cls or "low-quality" in cls:
                is_spam_or_low_quality = True
        
        if not inquiry.missing_information:
            return 0.3, "No missing information identified"
        
        count = len(inquiry.missing_information)
        
        # For spam/low-quality, missing info doesn't increase priority
        if is_spam_or_low_quality:
            return 0.2, f"Missing info ignored for {inquiry.classification} ({count} fields)"
        
        if count >= 3:
            return 1.0, f"Many missing fields ({count})"
        elif count >= 2:
            return 0.8, f"Some missing fields ({count})"
        elif count >= 1:
            return 0.6, f"One missing field ({count})"
        return 0.3, "No missing information"

    def _spam_score(self, inquiry: Inquiry) -> tuple[float, str]:
        """Score based on spam classification (higher = less spam)."""
        if inquiry.classification:
            if "spam" in inquiry.classification.lower():
                return 0.0, "Classified as spam"
            elif "low-quality" in inquiry.classification.lower():
                return 0.1, "Classified as low-quality"
        return 0.5, "Not classified as spam"


def calculate_priority(inquiry: Inquiry, config: BusinessConfig) -> tuple[float, str]:
    """Convenience function to calculate priority."""
    calculator = PriorityCalculator(config)
    return calculator.calculate(inquiry)