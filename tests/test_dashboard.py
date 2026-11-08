"""LEADLIFT — Dashboard reporting tests (Phase 4D.1).

Uses temporary databases and synthetic records.
"""

from __future__ import annotations

import pytest
from pathlib import Path

from leadlift.models import Inquiry, InquirySource, InquiryStatus, MissingInformation
from leadlift.repository import InquiryRepository
from leadlift.dashboard import Dashboard
from tests.test_support import reset_test_database


@pytest.fixture
def temp_db(isolated_database):
    """Provide a fresh database and repository for each test."""
    reset_test_database(isolated_database, Path(isolated_database).parent)
    yield isolated_database, InquiryRepository()


def _make_inquiry(repo, text="Test inquiry", status=InquiryStatus.NEW, source=InquirySource.MANUAL, missing=None):
    inq = Inquiry(
        source=source,
        source_platform=source.value,
        original_text=text,
        received_at="2026-01-01T10:00:00Z",
        first_observed_at="2026-01-01T10:00:00Z",
        status=status,
    )
    if missing:
        inq.missing_information = missing
    repo.save(inq)
    return inq


class TestDashboardReporting:
    def test_missing_info_empty_db(self, temp_db):
        db_path, repo = temp_db
        dash = Dashboard(repo)
        assert dash.missing_info_report() == []

    def test_missing_info_populated(self, temp_db):
        db_path, repo = temp_db
        mi = [MissingInformation(field="budget", description="Budget not specified", importance="high")]
        inq = _make_inquiry(repo, missing=mi)
        dash = Dashboard(repo)
        report = dash.missing_info_report()
        assert len(report) == 1
        assert report[0]["inquiry_id"] == inq.inquiry_id
        assert report[0]["field"] == "budget"
        assert report[0]["importance"] == "high"

    def test_missing_info_multiple(self, temp_db):
        db_path, repo = temp_db
        mi1 = [MissingInformation(field="budget", description="Budget missing", importance="high")]
        mi2 = [MissingInformation(field="timeline", description="Timeline missing", importance="medium")]
        _make_inquiry(repo, missing=mi1)
        _make_inquiry(repo, missing=mi2)
        dash = Dashboard(repo)
        report = dash.missing_info_report()
        assert len(report) == 2

    def test_error_report_empty_db(self, temp_db):
        db_path, repo = temp_db
        dash = Dashboard(repo)
        assert dash.error_report() == []

    def test_error_report_with_failed_inquiry(self, temp_db):
        db_path, repo = temp_db
        inq = _make_inquiry(repo)
        # Add a failed processing entry
        repo.add_processing_entry(
            __import__('leadlift.models', fromlist=['ProcessingHistoryEntry']).ProcessingHistoryEntry(
                inquiry_id=inq.inquiry_id,
                step="extraction",
                provider="ollama",
                state="failed",
                error_message="Model timeout",
            )
        )
        repo.update_ai_state(inq.inquiry_id, "failed")
        dash = Dashboard(repo)
        report = dash.error_report()
        assert len(report) == 1
        assert report[0]["inquiry_id"] == inq.inquiry_id
        assert report[0]["error"] == "Model timeout"

    def test_error_report_no_failed(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo)
        dash = Dashboard(repo)
        assert dash.error_report() == []

    def test_export_summary_empty_db(self, temp_db):
        db_path, repo = temp_db
        dash = Dashboard(repo)
        export = dash.export_summary()
        assert "generated_at" in export
        assert "summary" in export
        assert "trends" in export
        assert "missing_info" in export
        assert "errors" in export
        assert "priority_distribution" in export
        assert export["summary"]["total_inquiries"] == 0

    def test_export_summary_populated(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, text="Inquiry 1")
        _make_inquiry(repo, text="Inquiry 2", status=InquiryStatus.WON)
        dash = Dashboard(repo)
        export = dash.export_summary()
        assert export["summary"]["total_inquiries"] == 2
        assert export["summary"]["won"] == 1
        assert len(export["missing_info"]) == 0
        assert len(export["errors"]) == 0

    def test_export_summary_includes_missing_info(self, temp_db):
        db_path, repo = temp_db
        mi = [MissingInformation(field="budget", description="Budget missing", importance="high")]
        _make_inquiry(repo, missing=mi)
        dash = Dashboard(repo)
        export = dash.export_summary()
        assert len(export["missing_info"]) == 1

    def test_export_summary_includes_errors(self, temp_db):
        db_path, repo = temp_db
        inq = _make_inquiry(repo)
        repo.add_processing_entry(
            __import__('leadlift.models', fromlist=['ProcessingHistoryEntry']).ProcessingHistoryEntry(
                inquiry_id=inq.inquiry_id,
                step="extraction",
                provider="ollama",
                state="failed",
                error_message="Timeout",
            )
        )
        repo.update_ai_state(inq.inquiry_id, "failed")
        dash = Dashboard(repo)
        export = dash.export_summary()
        assert len(export["errors"]) == 1


class TestDateTrends:
    def test_same_day_grouping(self, temp_db):
        db_path, repo = temp_db
        # Two inquiries on same day
        for i in range(2):
            inq = Inquiry(source=InquirySource.MANUAL, source_platform="manual", original_text=f"Inq {i}", received_at="2026-01-01T10:00:00Z", first_observed_at="2026-01-01T10:00:00Z")
            repo.save(inq)
        dash = Dashboard(repo)
        trends = dash.trends(365)
        # Should group both on 2026-01-01
        days = [d for d in trends["daily"] if d["day"] == "2026-01-01"]
        assert len(days) == 1
        assert days[0]["count"] == 2

    def test_different_days(self, temp_db):
        db_path, repo = temp_db
        dates = ["2026-01-01T10:00:00Z", "2026-01-02T10:00:00Z", "2026-01-03T10:00:00Z"]
        for d in dates:
            inq = Inquiry(source=InquirySource.MANUAL, source_platform="manual", original_text="Test", received_at=d, first_observed_at=d)
            repo.save(inq)
        dash = Dashboard(repo)
        trends = dash.trends(365)
        assert len(trends["daily"]) == 3

    def test_month_boundary(self, temp_db):
        db_path, repo = temp_db
        inq1 = Inquiry(source=InquirySource.MANUAL, source_platform="manual", original_text="Jan", received_at="2026-01-31T23:59:59Z", first_observed_at="2026-01-31T23:59:59Z")
        inq2 = Inquiry(source=InquirySource.MANUAL, source_platform="manual", original_text="Feb", received_at="2026-02-01T00:00:00Z", first_observed_at="2026-02-01T00:00:00Z")
        repo.save(inq1)
        repo.save(inq2)
        dash = Dashboard(repo)
        trends = dash.trends(365)
        days = [d["day"] for d in trends["daily"]]
        assert "2026-01-31" in days
        assert "2026-02-01" in days

    def test_year_boundary(self, temp_db):
        db_path, repo = temp_db
        inq1 = Inquiry(source=InquirySource.MANUAL, source_platform="manual", original_text="Dec", received_at="2025-12-31T23:59:59Z", first_observed_at="2025-12-31T23:59:59Z")
        inq2 = Inquiry(source=InquirySource.MANUAL, source_platform="manual", original_text="Jan", received_at="2026-01-01T00:00:00Z", first_observed_at="2026-01-01T00:00:00Z")
        repo.save(inq1)
        repo.save(inq2)
        dash = Dashboard(repo)
        trends = dash.trends(365)
        days = [d["day"] for d in trends["daily"]]
        assert "2025-12-31" in days
        assert "2026-01-01" in days

    def test_midnight_boundary(self, temp_db):
        db_path, repo = temp_db
        inq1 = Inquiry(source=InquirySource.MANUAL, source_platform="manual", original_text="Before midnight", received_at="2026-01-01T23:59:59Z", first_observed_at="2026-01-01T23:59:59Z")
        inq2 = Inquiry(source=InquirySource.MANUAL, source_platform="manual", original_text="After midnight", received_at="2026-01-02T00:00:00Z", first_observed_at="2026-01-02T00:00:00Z")
        repo.save(inq1)
        repo.save(inq2)
        dash = Dashboard(repo)
        trends = dash.trends(365)
        days = [d["day"] for d in trends["daily"]]
        assert "2026-01-01" in days
        assert "2026-01-02" in days
        # Each should have count 1
        for d in trends["daily"]:
            if d["day"] in ("2026-01-01", "2026-01-02"):
                assert d["count"] == 1

    def test_empty_db_trends(self, temp_db):
        db_path, repo = temp_db
        dash = Dashboard(repo)
        trends = dash.trends(30)
        assert trends["daily"] == []

    def test_empty_db_summary(self, temp_db):
        db_path, repo = temp_db
        dash = Dashboard(repo)
        s = dash.summary()
        assert s["total_inquiries"] == 0
        assert s["new_inquiries"] == 0
        assert s["won"] == 0
        assert s["lost"] == 0
        assert s["outstanding_followups"] == 0
        assert s["overdue_followups"] == 0
        assert s["missing_information_fields"] == 0
        assert s["avg_response_time_hours"] is None
        assert s["median_response_time_hours"] is None


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
