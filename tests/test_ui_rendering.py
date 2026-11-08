"""UI rendering safety tests for customer-controlled content.

Tests verify that customer-controlled content is rendered as plain text
using st.text() rather than st.write() (Markdown) in the 4 messaging
adapter UI functions.

Streamlit's AppTest cannot navigate sidebar radio widgets, so we use
source-code verification combined with import/smoke tests.
"""

from __future__ import annotations

import inspect
import re
from pathlib import Path

import pytest

from tests.test_support import reset_test_database


class TestUICustomerContentRendering:
    """Verify customer-controlled content uses st.text() not st.write()."""

    def _get_function_source(self, func_name):
        import leadlift.ui as ui_module
        func = getattr(ui_module, func_name)
        return inspect.getsource(func)

    def test_whatsapp_customer_fields_use_st_text(self):
        src = self._get_function_source("render_whatsapp_intake")
        # Customer-controlled fields must use st.text
        assert 'st.text("Message ID:' in src or 'st.text("Message ID: ' in src
        assert 'st.text("Sender Phone:' in src or 'st.text("Sender Phone: ' in src
        assert 'st.text("Sender Name:' in src or 'st.text("Sender Name: ' in src
        assert 'st.text("Conversation ID:' in src or 'st.text("Conversation ID: ' in src
        assert 'st.text("Customer Name:' in src or 'st.text("Customer Name: ' in src
        assert 'st.text("Customer Phone:' in src or 'st.text("Customer Phone: ' in src
        assert 'st.text(inquiry.original_text' in src

    def test_instagram_customer_fields_use_st_text(self):
        src = self._get_function_source("render_instagram_intake")
        assert 'st.text("Message ID:' in src or 'st.text("Message ID: ' in src
        assert 'st.text("Sender ID:' in src or 'st.text("Sender ID: ' in src
        assert 'st.text("Sender Name:' in src or 'st.text("Sender Name: ' in src
        assert 'st.text("Conversation ID:' in src or 'st.text("Conversation ID: ' in src
        assert 'st.text("Customer Name:' in src or 'st.text("Customer Name: ' in src
        assert 'st.text(inquiry.original_text' in src

    def test_facebook_customer_fields_use_st_text(self):
        src = self._get_function_source("render_facebook_intake")
        assert 'st.text("Message ID:' in src or 'st.text("Message ID: ' in src
        assert 'st.text("Sender ID:' in src or 'st.text("Sender ID: ' in src
        assert 'st.text("Sender Name:' in src or 'st.text("Sender Name: ' in src
        assert 'st.text("Conversation ID:' in src or 'st.text("Conversation ID: ' in src
        assert 'st.text("Customer Name:' in src or 'st.text("Customer Name: ' in src
        assert 'st.text(inquiry.original_text' in src

    def test_telegram_customer_fields_use_st_text(self):
        src = self._get_function_source("render_telegram_intake")
        assert 'st.text("Message ID:' in src or 'st.text("Message ID: ' in src
        assert 'st.text("Update ID:' in src or 'st.text("Update ID: ' in src
        assert 'st.text("Sender ID:' in src or 'st.text("Sender ID: ' in src
        assert 'st.text("Sender Name:' in src or 'st.text("Sender Name: ' in src
        assert 'st.text("Chat ID:' in src or 'st.text("Chat ID: ' in src
        assert 'st.text("Chat Type:' in src or 'st.text("Chat Type: ' in src
        assert 'st.text("Customer Name:' in src or 'st.text("Customer Name: ' in src
        assert 'st.text(inquiry.original_text' in src

    def test_no_customer_content_in_st_write(self):
        """Verify no customer-controlled field is passed through st.write() in the 4 adapters."""
        for func_name in ["render_whatsapp_intake", "render_instagram_intake", "render_facebook_intake", "render_telegram_intake"]:
            src = self._get_function_source(func_name)
            # Find st.write calls that include customer-controlled variables
            for line in src.split("\n"):
                if "st.write(" in line:
                    # These st.write calls should only be for application-controlled content
                    # (config status, error messages, source enum, event type)
                    assert "inquiry.customer_name" not in line, f"{func_name}: customer_name in st.write"
                    assert "inquiry.customer_phone" not in line, f"{func_name}: customer_phone in st.write"
                    assert "inquiry.customer_email" not in line, f"{func_name}: customer_email in st.write"
                    assert "result.sender_name" not in line, f"{func_name}: sender_name in st.write"
                    assert "result.sender_id" not in line, f"{func_name}: sender_id in st.write"
                    assert "result.sender_phone" not in line, f"{func_name}: sender_phone in st.write"
                    assert "result.message_id" not in line, f"{func_name}: message_id in st.write"
                    assert "result.conversation_id" not in line, f"{func_name}: conversation_id in st.write"
                    assert "result.chat_id" not in line, f"{func_name}: chat_id in st.write"
                    assert "result.update_id" not in line, f"{func_name}: update_id in st.write"
                    assert "inquiry.original_text" not in line, f"{func_name}: original_text in st.write"

    def test_ui_module_imports(self):
        import leadlift.ui
        assert hasattr(leadlift.ui, "render_whatsapp_intake")
        assert hasattr(leadlift.ui, "render_instagram_intake")
        assert hasattr(leadlift.ui, "render_facebook_intake")
        assert hasattr(leadlift.ui, "render_telegram_intake")
        assert hasattr(leadlift.ui, "render_website_form_intake")


class TestUIRenderingSmoke:
    """Smoke tests using AppTest where possible."""

    def test_app_loads(self, isolated_database):
        from streamlit.testing.v1 import AppTest
        reset_test_database(isolated_database, Path(isolated_database).parent)
        at = AppTest.from_file("C:/Users/OGUNYEYE SOLOMON/Projects/Test/leadlift_recovery_candidate/leadlift/ui.py")
        at.run(timeout=10)
        assert not at.exception
        reset_test_database(isolated_database, Path(isolated_database).parent)


class TestBrowserUIRendering:
    """Verify Browser Intake customer content uses st.text()."""

    def test_browser_customer_fields_use_st_text(self):
        import inspect
        import leadlift.ui as ui_module
        src = inspect.getsource(ui_module.render_browser_intake)
        assert 'st.text("Customer Name:' in src or 'st.text("Customer Name: ' in src
        assert 'st.text("Email:' in src or 'st.text("Email: ' in src
        assert 'st.text("Phone:' in src or 'st.text("Phone: ' in src
        assert 'st.text("Organization:' in src or 'st.text("Organization: ' in src
        assert 'st.text("Requested Service:' in src or 'st.text("Requested Service: ' in src
        assert 'st.text("Budget:' in src or 'st.text("Budget: ' in src
        assert 'st.text("Timeline:' in src or 'st.text("Timeline: ' in src
        assert 'st.text("URL:' in src or 'st.text("URL: ' in src
        assert 'st.text("Title:' in src or 'st.text("Title: ' in src

    def test_no_customer_content_in_st_write_browser(self):
        import inspect
        import leadlift.ui as ui_module
        src = inspect.getsource(ui_module.render_browser_intake)
        for line in src.split("\n"):
            if "st.write(" in line:
                assert "inquiry.customer_name" not in line
                assert "inquiry.customer_email" not in line
                assert "inquiry.customer_phone" not in line
                assert "inquiry.organization" not in line
                assert "inquiry.requested_service" not in line
                assert "inquiry.budget_stated" not in line
                assert "inquiry.timeline_stated" not in line
                assert "source_metadata.get" not in line
                assert "dup.customer_name" not in line
                assert "dup.customer_email" not in line

    def test_browser_page_loads(self, isolated_database):
        from streamlit.testing.v1 import AppTest
        reset_test_database(isolated_database, Path(isolated_database).parent)
        at = AppTest.from_file("C:/Users/OGUNYEYE SOLOMON/Projects/Test/leadlift_recovery_candidate/leadlift/ui.py")
        at.run(timeout=10)
        assert not at.exception
        reset_test_database(isolated_database, Path(isolated_database).parent)


class TestInquiryDetailRendering:
    """Verify render_inquiry_detail customer content uses st.text()."""

    def test_customer_fields_use_st_text(self):
        import inspect
        import leadlift.ui as ui_module
        src = inspect.getsource(ui_module.render_inquiry_detail)
        # Customer-controlled fields must use st.text
        assert 'st.text("Customer: ' in src
        assert 'st.text("Email: ' in src
        assert 'st.text("Phone: ' in src
        assert 'st.text("Organization: ' in src
        assert 'st.text("Requested Service: ' in src
        assert 'st.text("Budget (stated): ' in src
        assert 'st.text("Timeline (stated): ' in src

    def test_no_customer_content_in_st_write_detail(self):
        import inspect
        import leadlift.ui as ui_module
        src = inspect.getsource(ui_module.render_inquiry_detail)
        for line in src.split("\n"):
            if "st.write(" in line:
                assert "inquiry.customer_name" not in line
                assert "inquiry.customer_email" not in line
                assert "inquiry.customer_phone" not in line
                assert "inquiry.organization" not in line
                assert "inquiry.requested_service" not in line
                assert "inquiry.budget_stated" not in line
                assert "inquiry.timeline_stated" not in line
                assert "dup.customer_name" not in line
                assert "dup.customer_email" not in line

    def test_message_text_still_plain(self):
        import inspect
        import leadlift.ui as ui_module
        src = inspect.getsource(ui_module.render_inquiry_detail)
        assert 'st.text(inquiry.original_text' in src or 'st.text(' in src


class TestUINavigation:
    """Verify UI navigation renders all pages without errors."""

    def test_app_loads(self, isolated_database):
        from streamlit.testing.v1 import AppTest
        reset_test_database(isolated_database, Path(isolated_database).parent)
        at = AppTest.from_file("C:/Users/OGUNYEYE SOLOMON/Projects/Test/leadlift_recovery_candidate/leadlift/ui.py")
        at.run(timeout=10)
        assert not at.exception

    def test_all_pages_render(self, isolated_database):
        from streamlit.testing.v1 import AppTest
        reset_test_database(isolated_database, Path(isolated_database).parent)
        at = AppTest.from_file("C:/Users/OGUNYEYE SOLOMON/Projects/Test/leadlift_recovery_candidate/leadlift/ui.py")
        at.run(timeout=10)
        assert not at.exception
        pages = ["Dashboard", "Inquiries", "Follow-ups", "Intake", "Intelligence", "Reports", "Settings"]
        for page in pages:
            at.radio[0].set_value(page)
            at.run(timeout=10)
            assert not at.exception, f"Failed on page: {page}"

    def test_main_has_navigation(self):
        import inspect
        import leadlift.ui as ui_module
        src = inspect.getsource(ui_module.main)
        assert 'st.sidebar.radio' in src
        assert 'render_dashboard' in src
        assert 'render_inquiries' in src
        assert 'render_settings' in src

    def test_no_nav_in_intake_functions(self):
        import inspect
        import leadlift.ui as ui_module
        for func_name in ['render_facebook_intake', 'render_website_form_intake', 'render_whatsapp_intake',
                          'render_instagram_intake', 'render_telegram_intake', 'render_browser_intake']:
            src = inspect.getsource(getattr(ui_module, func_name))
            assert 'st.sidebar.radio' not in src, f'{func_name} still has nav'
