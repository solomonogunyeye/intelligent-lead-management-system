"""LEADLIFT — Dashboard metrics and reporting."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from leadlift.models import FollowUpTask, Inquiry, InquiryStatus
from leadlift.repository import InquiryRepository


class Dashboard:
    """Calculate operational metrics from real persisted data."""

    def __init__(self, repository: Optional[InquiryRepository] = None):
        self.repository = repository or InquiryRepository()

    def summary(self) -> Dict[str, Any]:
        """Return a comprehensive summary for the dashboard."""
        inquiries = self.repository.get_all(limit=10000)

        # Total counts
        total = len(inquiries)
        new_count = sum(1 for i in inquiries if i.status == InquiryStatus.NEW)

        # By status
        by_status = {}
        for status in InquiryStatus:
            by_status[status.value] = sum(1 for i in inquiries if i.status == status)

        # By source
        by_source = {}
        for i in inquiries:
            by_source[i.source.value] = by_source.get(i.source.value, 0) + 1

        # Follow-ups
        tasks = self.repository.get_all_tasks()
        outstanding = [t for t in tasks if not t.completed]
        overdue = [t for t in outstanding if t.is_overdue()]

        # Won/Lost
        won = sum(1 for i in inquiries if i.status == InquiryStatus.WON)
        lost = sum(1 for i in inquiries if i.status == InquiryStatus.LOST)

        # AI processing state
        by_ai_state = {}
        for i in inquiries:
            by_ai_state[i.ai_state.value] = by_ai_state.get(i.ai_state.value, 0) + 1

        # Missing information count
        missing_info_count = sum(len(i.missing_information) for i in inquiries)

        # Response time (for contacted inquiries with timestamps)
        response_times = self._calculate_response_times(inquiries)

        return {
            "total_inquiries": total,
            "new_inquiries": new_count,
            "inquiries_by_status": by_status,
            "inquiries_by_source": by_source,
            "outstanding_followups": len(outstanding),
            "overdue_followups": len(overdue),
            "won": won,
            "lost": lost,
            "missing_information_fields": missing_info_count,
            "ai_processing_state": by_ai_state,
            "avg_response_time_hours": response_times.get("avg_hours"),
            "median_response_time_hours": response_times.get("median_hours"),
        }

    def _calculate_response_times(self, inquiries: List[Inquiry]) -> Dict[str, Optional[float]]:
        """Calculate response times for inquiries that have been contacted."""
        times = []
        for i in inquiries:
            if i.status in (InquiryStatus.CONTACTED, InquiryStatus.QUALIFIED,
                           InquiryStatus.PROPOSAL_SENT, InquiryStatus.WON, InquiryStatus.LOST):
                # Find the first "Contacted" status change
                history = self.repository.get_status_history(i.inquiry_id)
                contacted_at = None
                for entry in history:
                    if entry.new_status == InquiryStatus.CONTACTED:
                        contacted_at = entry.changed_at
                        break
                if contacted_at:
                    try:
                        received = datetime.fromisoformat(i.received_at.replace("Z", "+00:00"))
                        contacted = datetime.fromisoformat(contacted_at.replace("Z", "+00:00"))
                        hours = (contacted - received).total_seconds() / 3600
                        if hours >= 0:
                            times.append(hours)
                    except (ValueError, TypeError):
                        continue

        if not times:
            return {"avg_hours": None, "median_hours": None}

        times.sort()
        avg = sum(times) / len(times)
        median = times[len(times) // 2] if times else None
        return {"avg_hours": round(avg, 1), "median_hours": round(median, 1) if median else None}

    def trends(self, days: int = 30) -> Dict[str, List[Dict[str, Any]]]:
        """Return daily inquiry counts for the last N days."""
        from leadlift.db import get_db

        with get_db() as conn:
            rows = conn.execute(
                """SELECT date(received_at) as day, COUNT(*) as count
                   FROM inquiries
                   WHERE received_at >= date('now', ?)
                   GROUP BY date(received_at)
                   ORDER BY day""",
                (f"-{days} days",),
            ).fetchall()

        return {"daily": [{"day": r["day"], "count": r["count"]} for r in rows]}

    def missing_info_report(self) -> List[Dict[str, Any]]:
        """Report on missing information across inquiries."""
        inquiries = self.repository.get_all()
        report = []
        for i in inquiries:
            if i.missing_information:
                for mi in i.missing_information:
                    report.append({
                        "inquiry_id": i.inquiry_id,
                        "customer": i.customer_name or "Unknown",
                        "field": mi.field,
                        "description": mi.description,
                        "importance": mi.importance,
                    })
        return report

    def error_report(self) -> List[Dict[str, Any]]:
        """Report processing errors."""
        inquiries = self.repository.get_all()
        report = []
        for i in inquiries:
            if i.ai_state == "failed":
                history = self.repository.get_processing_history(i.inquiry_id)
                for h in history:
                    if h.state in ("failed", "timeout", "error"):
                        report.append({
                            "inquiry_id": i.inquiry_id,
                            "step": h.step,
                            "provider": h.provider,
                            "error": h.error_message,
                            "details": h.details,
                            "processed_at": h.processed_at,
                        })
        return report

    def priority_distribution(self) -> Dict[str, int]:
        """Count inquiries by priority bucket."""
        inquiries = self.repository.get_all()
        buckets = {"High (0.7+)": 0, "Medium (0.4-0.7)": 0, "Low (<0.4)": 0}
        for i in inquiries:
            if i.priority_score >= 0.7:
                buckets["High (0.7+)"] += 1
            elif i.priority_score >= 0.4:
                buckets["Medium (0.4-0.7)"] += 1
            else:
                buckets["Low (<0.4)"] += 1
        return buckets

    def export_summary(self) -> Dict[str, Any]:
        """Full export for reporting."""
        return {
            "generated_at": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
            "summary": self.summary(),
            "trends": self.trends(30),
            "missing_info": self.missing_info_report(),
            "errors": self.error_report(),
            "priority_distribution": self.priority_distribution(),
        }