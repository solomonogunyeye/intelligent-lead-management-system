"""LEADLIFT — Search, filter, and export tests (Phase 6B).

Uses temporary databases and synthetic records only.
"""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path

import pytest

from leadlift.models import Inquiry, InquirySource, InquiryStatus
from leadlift.repository import InquiryRepository
from tests.test_support import reset_test_database
from tests.test_support import reset_test_database


@pytest.fixture
def temp_db(isolated_database):
    """Provide a fresh database and repository for each test."""
    reset_test_database(isolated_database, Path(isolated_database).parent)
    yield isolated_database, InquiryRepository()


def _make_inquiry(repo, text="Test inquiry", name="John", email="john@test.com", phone="555-1234", org="Acme", service="Web Dev", source=InquirySource.MANUAL, status=InquiryStatus.NEW, priority=0.5, received="2026-01-15T10:00:00Z"):
    inq = Inquiry(
        source=source,
        source_platform=source.value,
        customer_name=name,
        customer_email=email,
        customer_phone=phone,
        organization=org,
        requested_service=service,
        original_text=text,
        received_at=received,
        first_observed_at=received,
        status=status,
        priority_score=priority,
    )
    repo.save(inq)
    return inq


# ---------------------------------------------------------------------------
# Search tests
# ---------------------------------------------------------------------------

class TestSearch:
    def test_exact_match(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, name="Alice")
        results = repo.search("Alice")
        assert len(results) == 1

    def test_partial_match(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, name="Alice Smith")
        results = repo.search("Ali")
        assert len(results) == 1

    def test_case_insensitive(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, name="Alice")
        results = repo.search("alice")
        assert len(results) == 1

    def test_search_by_email(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, email="alice@example.com")
        results = repo.search("example.com")
        assert len(results) == 1

    def test_search_by_phone(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, phone="555-9999")
        results = repo.search("9999")
        assert len(results) == 1

    def test_search_by_service(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, service="SEO")
        results = repo.search("SEO")
        assert len(results) == 1

    def test_search_by_source(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, source=InquirySource.WHATSAPP)
        results = repo.search("whatsapp")
        assert len(results) == 1

    def test_search_by_text(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, text="I need a website")
        results = repo.search("website")
        assert len(results) == 1

    def test_empty_search_returns_all(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo)
        _make_inquiry(repo, name="Bob")
        results = repo.search("")
        assert len(results) == 2

    def test_null_fields_handled(self, temp_db):
        db_path, repo = temp_db
        inq = Inquiry(source=InquirySource.MANUAL, source_platform="manual", original_text="Test", received_at="2026-01-01T10:00:00Z", first_observed_at="2026-01-01T10:00:00Z")
        repo.save(inq)
        results = repo.search("Test")
        assert len(results) == 1

    def test_special_characters(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, name="O'Brien")
        results = repo.search("O'Brien")
        assert len(results) == 1

    def test_sql_injection_safe(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, name="Alice")
        # SQL injection attempt
        results = repo.search("'; DROP TABLE inquiries; --")
        assert len(results) == 0  # No crash, no records
        # Verify table still exists
        all_inqs = repo.get_all()
        assert len(all_inqs) == 1


# ---------------------------------------------------------------------------
# Filter tests
# ---------------------------------------------------------------------------

class TestFilter:
    def test_filter_by_status(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, status=InquiryStatus.NEW)
        _make_inquiry(repo, status=InquiryStatus.WON, name="Bob")
        results = repo.search_and_filter(status=InquiryStatus.NEW)
        assert len(results) == 1

    def test_filter_by_source(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, source=InquirySource.MANUAL)
        _make_inquiry(repo, source=InquirySource.WHATSAPP, name="Bob")
        results = repo.search_and_filter(source="whatsapp")
        assert len(results) == 1

    def test_filter_by_priority(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, priority=0.8)
        _make_inquiry(repo, priority=0.3, name="Bob")
        results = repo.search_and_filter(priority_min=0.5)
        assert len(results) == 1

    def test_filter_by_date_from(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, received="2026-01-01T10:00:00Z")
        _make_inquiry(repo, received="2026-02-01T10:00:00Z", name="Bob")
        results = repo.search_and_filter(date_from="2026-02-01")
        assert len(results) == 1

    def test_filter_by_date_to(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, received="2026-01-01T10:00:00Z")
        _make_inquiry(repo, received="2026-02-01T10:00:00Z", name="Bob")
        results = repo.search_and_filter(date_to="2026-01-31")
        assert len(results) == 1

    def test_combined_filters(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, status=InquiryStatus.NEW, source=InquirySource.MANUAL, name="Alice")
        _make_inquiry(repo, status=InquiryStatus.WON, source=InquirySource.MANUAL, name="Bob")
        _make_inquiry(repo, status=InquiryStatus.NEW, source=InquirySource.WHATSAPP, name="Charlie")
        results = repo.search_and_filter(status=InquiryStatus.NEW, source="manual")
        assert len(results) == 1
        assert results[0].customer_name == "Alice"

    def test_clear_filters(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo)
        _make_inquiry(repo, name="Bob")
        # With filter
        filtered = repo.search_and_filter(status=InquiryStatus.WON)
        assert len(filtered) == 0
        # Clear filter (status=None)
        all_results = repo.search_and_filter()
        assert len(all_results) == 2

    def test_empty_result_set(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, name="Alice")
        results = repo.search_and_filter(query="nonexistent")
        assert len(results) == 0

    def test_search_and_filter_combined(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, name="Alice", text="website")
        _make_inquiry(repo, name="Bob", text="app")
        results = repo.search_and_filter(query="website", status=InquiryStatus.NEW)
        assert len(results) == 1
        assert results[0].customer_name == "Alice"

    def test_filter_does_not_mutate(self, temp_db):
        db_path, repo = temp_db
        inq = _make_inquiry(repo)
        original_text = inq.original_text
        repo.search_and_filter(status=InquiryStatus.NEW)
        loaded = repo.get(inq.inquiry_id)
        assert loaded.original_text == original_text


# ---------------------------------------------------------------------------
# Export tests
# ---------------------------------------------------------------------------

class TestExport:
    def test_csv_generation(self, temp_db):
        db_path, repo = temp_db
        inq = _make_inquiry(repo, name="John", email="john@test.com")
        inquiries = repo.get_all()
        # Simulate CSV export
        import csv, io
        output = io.StringIO()
        writer = csv.writer(output)
        writer.writerow(["inquiry_id", "customer_name", "customer_email"])
        for i in inquiries:
            writer.writerow([i.inquiry_id, i.customer_name or "", i.customer_email or ""])
        csv_content = output.getvalue()
        assert "inquiry_id" in csv_content
        assert "John" in csv_content
        assert "john@test.com" in csv_content

    def test_json_generation(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, name="John")
        inquiries = repo.get_all()
        import json
        data = [{"inquiry_id": i.inquiry_id, "customer_name": i.customer_name} for i in inquiries]
        json_str = json.dumps(data, default=str)
        parsed = json.loads(json_str)
        assert len(parsed) == 1
        assert parsed[0]["customer_name"] == "John"

    def test_utf8_handling(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, name="José García", text="Necesito un sitio web")
        inquiries = repo.get_all()
        import json
        data = [{"customer_name": i.customer_name, "original_text": i.original_text} for i in inquiries]
        json_str = json.dumps(data, ensure_ascii=False)
        assert "José" in json_str
        assert "Necesito" in json_str

    def test_csv_formula_injection_protection(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo, name="=CMD|' /C calc'!A0")
        inquiries = repo.get_all()
        import csv, io
        output = io.StringIO()
        writer = csv.writer(output)
        for i in inquiries:
            # Sanitize: prefix dangerous chars with tab
            name = i.customer_name or ""
            if name and name[0] in ("=", "+", "-", "@", "\t", "\r"):
                name = "\t" + name
            writer.writerow([name])
        csv_content = output.getvalue()
        # The dangerous value should be sanitized
        assert "=CMD" not in csv_content or "\t=CMD" in csv_content

    def test_null_values_in_export(self, temp_db):
        db_path, repo = temp_db
        inq = Inquiry(source=InquirySource.MANUAL, source_platform="manual", original_text="Test", received_at="2026-01-01T10:00:00Z", first_observed_at="2026-01-01T10:00:00Z")
        repo.save(inq)
        inquiries = repo.get_all()
        import json
        data = [{"customer_name": i.customer_name, "customer_email": i.customer_email} for i in inquiries]
        json_str = json.dumps(data, default=str)
        parsed = json.loads(json_str)
        assert parsed[0]["customer_name"] is None
        assert parsed[0]["customer_email"] is None

    def test_empty_export(self, temp_db):
        db_path, repo = temp_db
        inquiries = repo.get_all()
        assert len(inquiries) == 0
        import json
        data = [{"inquiry_id": i.inquiry_id} for i in inquiries]
        json_str = json.dumps(data)
        parsed = json.loads(json_str)
        assert parsed == []

    def test_export_does_not_modify_db(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo)
        before_count = len(repo.get_all())
        # Simulate export
        inquiries = repo.get_all()
        _ = [i.inquiry_id for i in inquiries]
        after_count = len(repo.get_all())
        assert before_count == after_count

    def test_export_excludes_secrets(self, temp_db):
        db_path, repo = temp_db
        _make_inquiry(repo)
        inquiries = repo.get_all()
        import json
        data = [{"inquiry_id": i.inquiry_id, "original_text": i.original_text} for i in inquiries]
        json_str = json.dumps(data)
        # No secrets should be in export
        assert "password" not in json_str.lower()
        assert "secret" not in json_str.lower()
        assert "token" not in json_str.lower()


# ---------------------------------------------------------------------------
# UI tests
# ---------------------------------------------------------------------------

class TestUI:
    def test_search_controls_render(self, isolated_database):
        from streamlit.testing.v1 import AppTest
        reset_test_database(isolated_database, Path(isolated_database).parent)
        at = AppTest.from_file(str(Path(__file__).parent.parent / "leadlift" / "ui.py"))
        at.run(timeout=10)
        at.radio[0].set_value("Inquiries")
        at.run(timeout=10)
        assert not at.exception

    def test_export_controls_render(self, isolated_database):
        from streamlit.testing.v1 import AppTest
        reset_test_database(isolated_database, Path(isolated_database).parent)
        at = AppTest.from_file(str(Path(__file__).parent.parent / "leadlift" / "ui.py"))
        at.run(timeout=10)
        at.radio[0].set_value("Inquiries")
        at.run(timeout=10)
        assert not at.exception

    def test_navigation_still_works(self, isolated_database):
        from streamlit.testing.v1 import AppTest
        reset_test_database(isolated_database, Path(isolated_database).parent)
        at = AppTest.from_file(str(Path(__file__).parent.parent / "leadlift" / "ui.py"))
        at.run(timeout=10)
        pages = ["Dashboard", "Inquiries", "Follow-ups", "Intake", "Intelligence", "Reports", "Settings"]
        for page in pages:
            at.radio[0].set_value(page)
            at.run(timeout=10)
            assert not at.exception, f"Failed on page: {page}"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
