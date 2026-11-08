"""LEADLIFT — Streamlit application interface."""

from __future__ import annotations

import json
import streamlit as st

from leadlift.models import InquiryStatus, InquirySource
from leadlift.config import get_settings
from leadlift.dashboard import Dashboard
from leadlift.repository import get_repository
from leadlift.ai.processor import AIProcessor
from leadlift.ai.validation import ValidatedExtraction
try:
    from leadlift.adapters.browser import BrowserAdapter, extract_from_file, save_confirmed_inquiry, ExtractionStatus
except ImportError:
    BrowserAdapter = None
    extract_from_file = None
    save_confirmed_inquiry = None
    ExtractionStatus = None
from leadlift.adapters.whatsapp import WhatsAppIntakeAdapter, WhatsAppIntakeResult
from leadlift.adapters.instagram import InstagramIntakeAdapter, InstagramIntakeResult
from leadlift.adapters.facebook import FacebookIntakeAdapter, FacebookIntakeResult
from leadlift.adapters.telegram import TelegramIntakeAdapter, TelegramIntakeResult
from leadlift.adapters.website_form import WebsiteFormIntakeAdapter, WebsiteFormIntakeResult


import re


def _sanitize_csv_value(value: object) -> str:
    """Neutralize spreadsheet formula injection at the CSV export boundary."""
    if value is None:
        return ""
    text = str(value)
    stripped = text.lstrip()
    dangerous = False
    if stripped:
        first = stripped[0]
        if first in ("=", "+", "@"):
            dangerous = True
        elif first == "-":
            dangerous = bool(re.search(r"^-\d*[\+\-*/^%(]", stripped)) or bool(
                re.search(r"^-\s*[A-Za-z_]", stripped)
            )
        if text != stripped and stripped[0] in ("=", "+", "-", "@"):
            dangerous = True
    return ("'" + text) if dangerous else text


def _apply_visual_foundation():
    st.markdown("""
    <style>
    :root {
        --primary: #1E3A5F;
        --accent: #E8743B;
        --success: #28A745;
        --warning: #FFC107;
        --danger: #DC3545;
        --text: #212529;
        --text-muted: #6C757D;
        --bg: #F8F9FA;
        --border: #DEE2E6;
    }
    h1 { color: var(--primary); font-weight: 600; font-size: 1.75rem; }
    h2 { color: var(--primary); font-weight: 600; font-size: 1.25rem; }
    h3 { color: var(--text); font-weight: 600; font-size: 1rem; }
    .block-container { padding: 1.5rem 2rem; max-width: 1200px; }
    .stButton button { border-radius: 4px; font-weight: 500; }
    [data-testid="stMetric"] { background-color: white; border: 1px solid var(--border); border-radius: 4px; padding: 0.75rem; }
    section[data-testid="stSidebar"] { background-color: var(--bg); }
    </style>
    """, unsafe_allow_html=True)


def render_header():
    st.set_page_config(page_title="LEADLIFT", page_icon="", layout="wide")
    st.title("LEADLIFT")
    st.markdown("AI-assisted lead management and follow-up automation for small service businesses")
    st.markdown("---")


def render_dashboard():
    st.header("Dashboard")
    dashboard = Dashboard(get_repository())
    summary = dashboard.summary()

    col1, col2, col3, col4 = st.columns(4)
    with col1:
        st.metric("Total Inquiries", summary["total_inquiries"])
    with col2:
        st.metric("New Inquiries", summary["new_inquiries"])
    with col3:
        st.metric("Outstanding Follow-ups", summary["outstanding_followups"])
    with col4:
        st.metric("Overdue Follow-ups", summary["overdue_followups"])

    st.subheader("Inquiries by Status")
    st.bar_chart(summary["inquiries_by_status"])

    st.subheader("Inquiries by Source")
    st.bar_chart(summary["inquiries_by_source"])

    st.subheader("AI Processing States")
    st.json(summary["ai_processing_state"])

    st.subheader("Won/Lost")
    st.write(f"Won: {summary['won']}  |  Lost: {summary['lost']}")


def render_inquiries():
    st.header("Inquiries")
    repository = get_repository()

    # Search and filter controls
    st.subheader("Search & Filter")
    col1, col2, col3 = st.columns(3)
    with col1:
        search_query = st.text_input("Search", placeholder="Search by name, email, phone, service, or message...")
    with col2:
        status_filter = st.selectbox("Status", ["All"] + [s.value for s in InquiryStatus], index=0)
    with col3:
        source_filter = st.selectbox("Source", ["All"] + sorted(set(i.source.value for i in repository.get_all())), index=0)

    col4, col5 = st.columns(2)
    with col4:
        priority_min = st.slider("Min Priority", 0.0, 1.0, 0.0, 0.1)
    with col5:
        date_from = st.date_input("From Date", value=None)

    # Apply search and filters
    status = None if status_filter == "All" else InquiryStatus(status_filter)
    source = None if source_filter == "All" else source_filter
    date_from_str = date_from.isoformat() if date_from else None

    inquiries = repository.search_and_filter(
        query=search_query,
        status=status,
        source=source,
        priority_min=priority_min if priority_min > 0 else None,
        date_from=date_from_str,
    )

    # Show count
    st.write(f"**{len(inquiries)} inquiries found**")

    # Show inquiry table
    st.subheader("All Inquiries")
    st.dataframe([{
        "inquiry_id": i.inquiry_id,
        "customer": i.customer_name or "Unknown",
        "service": i.requested_service or "Unknown",
        "source": i.source.value,
        "status": i.status.value,
        "priority": i.priority_score,
        "ai_state": i.ai_state.value,
    } for i in inquiries], width="stretch")

    st.markdown("---")

    # Export controls
    st.subheader("Export")
    col1, col2 = st.columns(2)
    with col1:
        if st.button("Export CSV"):
            import csv, io
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["inquiry_id", "customer_name", "customer_email", "customer_phone", "organization", "requested_service", "source", "status", "priority_score", "received_at", "original_text"])
            for i in inquiries:
                writer.writerow([
                    _sanitize_csv_value(i.inquiry_id), _sanitize_csv_value(i.customer_name or ""), _sanitize_csv_value(i.customer_email or ""), _sanitize_csv_value(i.customer_phone or ""),
                    _sanitize_csv_value(i.organization or ""), _sanitize_csv_value(i.requested_service or ""), _sanitize_csv_value(i.source.value), _sanitize_csv_value(i.status.value),
                    i.priority_score, i.received_at, _sanitize_csv_value(i.original_text),
                ])
            st.download_button("Download CSV", output.getvalue(), "inquiries.csv", "text/csv")
    with col2:
        if st.button("Export JSON"):
            import json
            data = [{
                "inquiry_id": i.inquiry_id,
                "customer_name": i.customer_name,
                "customer_email": i.customer_email,
                "customer_phone": i.customer_phone,
                "organization": i.organization,
                "requested_service": i.requested_service,
                "source": i.source.value,
                "status": i.status.value,
                "priority_score": i.priority_score,
                "received_at": i.received_at,
                "original_text": i.original_text,
            } for i in inquiries]
            st.download_button("Download JSON", json.dumps(data, indent=2, default=str), "inquiries.json", "application/json")

    st.markdown("---")
    st.subheader("Inquiry Details")

    # Select inquiry for detailed view
    inquiry_options = {f"{i.inquiry_id} - {i.customer_name or 'Unknown'}": i.inquiry_id for i in inquiries}
    if inquiry_options:
        selected_label = st.selectbox("Select inquiry", list(inquiry_options.keys()))
        selected_id = inquiry_options[selected_label]
        inquiry = repository.get(selected_id)

        if inquiry:
            render_inquiry_detail(inquiry, repository)


def render_inquiry_detail(inquiry, repository):
    """Render detailed view of a single inquiry with AI processing."""
    col1, col2 = st.columns([1, 1])

    with col1:
        st.write("**Basic Information**")
        st.write(f"**ID:** {inquiry.inquiry_id}")
        st.text("Customer: " + str(inquiry.customer_name or 'Not provided'))
        st.text("Email: " + str(inquiry.customer_email or 'Not provided'))
        st.text("Phone: " + str(inquiry.customer_phone or 'Not provided'))
        st.text("Organization: " + str(inquiry.organization or 'Not provided'))
        st.text("Requested Service: " + str(inquiry.requested_service or 'Not provided'))
        st.text("Budget (stated): " + str(inquiry.budget_stated or 'Not provided'))
        if inquiry.budget_inferred:
            st.write(f"**Budget (inferred):** {inquiry.budget_inferred}")
        st.text("Timeline (stated): " + str(inquiry.timeline_stated or 'Not provided'))
        if inquiry.timeline_inferred:
            st.write(f"**Timeline (inferred):** {inquiry.timeline_inferred}")
        st.write(f"**Source:** {inquiry.source.value}")
        st.text(inquiry.original_text)

        st.write("**Workflow**")
        st.write(f"**Status:** {inquiry.status.value}")
        st.write(f"**Priority:** {inquiry.priority_score:.2f}")
        if inquiry.priority_explanation:
            with st.expander("Priority Explanation"):
                st.text(inquiry.priority_explanation)

    with col2:
        st.write("**AI Processing**")
        ai_state = inquiry.ai_state.value
        state_colors = {
            "pending": "🟡",
            "processed": "🟢",
            "failed": "🔴",
            "skipped": "⚪",
        }
        st.write(f"**State:** {state_colors.get(ai_state, '⚪')} {ai_state.title()}")

        if inquiry.classification:
            st.write(f"**Classification:** {inquiry.classification} ({inquiry.classification_source})")

        if inquiry.missing_information:
            st.write("**Missing Information:**")
            for mi in inquiry.missing_information:
                st.write(f"  - {mi.description} ({mi.importance})")

        if inquiry.recommended_next_action:
            st.write(f"**Recommended Action:** {inquiry.recommended_next_action}")

        # AI Processing controls
        st.markdown("---")
        processor = AIProcessor()

        if ai_state == "pending" or ai_state == "failed":
            if st.button("🔄 Process with AI", type="primary", width='stretch'):
                with st.spinner("Processing inquiry with AI..."):
                    try:
                        result = processor.process_inquiry(inquiry.inquiry_id)
                        st.success("Processing completed!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Processing failed: {e}")
        elif ai_state == "processed":
            st.write("✅ Already processed")
            if st.button("🔁 Re-process", use_container_width=True):
                with st.spinner("Re-processing..."):
                    try:
                        result = processor.retry_failed(inquiry.inquiry_id)
                        st.success("Re-processing completed!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Re-processing failed: {e}")

        # Show response drafts
        drafts = repository.get_drafts_for_inquiry(inquiry.inquiry_id)
        if drafts:
            st.markdown("---")
            st.write("**Response Drafts**")
            for draft in drafts:
                with st.expander(f"Draft {draft.draft_id[:8]}... ({draft.status}) - {draft.created_at[:16]}"):
                    st.text_area("Draft", draft.draft_text, height=200, key=f"draft_{draft.draft_id}", disabled=True)
                    col_a, col_b = st.columns(2)
                    with col_a:
                        if draft.status == "draft" and st.button("✏️ Edit", key=f"edit_{draft.draft_id}"):
                            st.session_state[f"editing_{draft.draft_id}"] = True
                    with col_b:
                        if st.button("🗑️ Delete", key=f"delete_{draft.draft_id}"):
                            repository.delete_draft(draft.draft_id)
                            st.rerun()

                    if st.session_state.get(f"editing_{draft.draft_id}", False):
                        edited = st.text_area("Edit draft", draft.draft_text, height=200, key=f"edit_text_{draft.draft_id}")
                        if st.button("💾 Save", key=f"save_{draft.draft_id}"):
                            try:
                                if repository.update_draft_text(draft.draft_id, edited):
                                    st.success("Draft saved successfully!")
                                else:
                                    st.error("Failed to save draft - draft not found")
                            except Exception as e:
                                st.error(f"Failed to save draft: {e}")
                            st.session_state[f"editing_{draft.draft_id}"] = False
                            st.rerun()

    # Original message
    st.markdown("---")
    st.write("**Original Message**")
    st.text_area("Original text", inquiry.original_text, height=150, disabled=True)

    # Processing history
    history = repository.get_processing_history(inquiry.inquiry_id)
    if history:
        st.markdown("---")
        st.write("**Processing History**")
        for entry in history:
            status_icon = {"success": "✅", "failed": "❌", "started": "🔄", "timeout": "⏱️"}.get(entry.state, "❓")
            with st.expander(f"{status_icon} {entry.step} - {entry.provider} - {entry.processed_at[:19]}"):
                st.write(f"**State:** {entry.state}")
                if entry.error_message:
                    st.error(entry.error_message)
                if entry.details:
                    st.json(entry.details)


def render_import():
    st.header("Import Inquiries")
    st.markdown("Upload a CSV file with inquiries to import.")

    uploaded_file = st.file_uploader("CSV file", type=["csv"])

    if uploaded_file is not None:
        csv_content = uploaded_file.getvalue().decode("utf-8")

        from leadlift.adapters.csv import CSVAdapter
        adapter = CSVAdapter()
        result = adapter.process(csv_content)

        st.subheader("Summary")
        st.write(f"Accepted: {result.accepted}")
        st.write(f"Rejected: {result.rejected}")
        st.write(f"Duplicates: {result.duplicates}")

        if result.errors:
            st.subheader("Errors")
            st.json(result.errors)

        if result.warnings:
            st.subheader("Warnings")
            st.json(result.warnings)


def render_process():
    """Dedicated AI processing page."""
    st.header("AI Processing Center")
    st.markdown("Process inquiries with AI to extract information, classify, and generate response drafts.")

    repository = get_repository()
    processor = AIProcessor()

    # Show AI availability
    ai_available = processor.is_available()
    model_info = processor.get_model_info()

    if ai_available:
        st.success(f"🟢 AI Available - Model: {model_info.get('name', 'Unknown')}")
    else:
        st.error("🔴 AI Unavailable - Check Ollama connection")

    st.markdown("---")

    # Get unprocessed inquiries
    all_inquiries = repository.get_all()
    unprocessed = [i for i in all_inquiries if i.ai_state.value in ("pending", "failed")]

    if not unprocessed:
        st.info("All inquiries have been processed.")
        return

    st.write(f"**Unprocessed inquiries:** {len(unprocessed)}")

    for inquiry in unprocessed:
        with st.container():
            col1, col2, col3 = st.columns([3, 1, 1])
            with col1:
                st.write(f"**{inquiry.inquiry_id}** - {inquiry.customer_name or 'Unknown'} - {inquiry.requested_service or 'No service'}")
                st.caption(f"Status: {inquiry.status.value} | AI: {inquiry.ai_state.value} | {inquiry.original_text[:100]}...")
            with col2:
                st.write(f"Priority: {inquiry.priority_score:.2f}")
            with col3:
                if st.button("Process", key=f"proc_{inquiry.inquiry_id}", type="primary"):
                    with st.spinner(f"Processing {inquiry.inquiry_id}..."):
                        try:
                            result = processor.process_inquiry(inquiry.inquiry_id)
                            st.success(f"Done: {result['classification']} ({result['confidence']:.0%})")
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed: {e}")
            st.divider()


def render_browser_intake():
    """Browser intake page for extracting inquiries from web pages/HTML files."""
    st.header("Browser Intake")
    st.markdown("Extract inquiries from web pages or local HTML files. Select a message to extract and review before saving.")

    # File upload
    uploaded_file = st.file_uploader("Upload HTML file (e.g., saved email page)", type=["html", "htm"])

    if uploaded_file is not None:
        html_content = uploaded_file.getvalue().decode("utf-8")

        # Extract inquiry
        with st.spinner("Extracting inquiry from HTML..."):
            try:
                import asyncio
                result = asyncio.run(extract_from_file("temp.html", settings=None))
                # Actually extract from the uploaded content directly
                async def _extract():
                    async with BrowserAdapter() as adapter:
                        return await adapter.extract_from_html(html_content, base_url="uploaded")
                result = asyncio.run(_extract())
                
                st.session_state["browser_extraction_result"] = result
            except Exception as e:
                st.error(f"Extraction failed: {e}")
                return

    # Display extraction result if available
    if "browser_extraction_result" in st.session_state:
        result = st.session_state["browser_extraction_result"]
        
        if result.status == ExtractionStatus.SUCCESS:
            st.success("✅ Extraction successful")
            inquiry = result.inquiry
            
            # Display extracted fields
            st.subheader("Extracted Inquiry")
            col1, col2 = st.columns(2)
            
            with col1:
                st.write("**Basic Information**")
                st.text("Customer Name: " + str(inquiry.customer_name or 'Not found'))
                st.text("Email: " + str(inquiry.customer_email or 'Not found'))
                st.text("Phone: " + str(inquiry.customer_phone or 'Not found'))
                st.text("Organization: " + str(inquiry.organization or 'Not found'))
                st.text("Requested Service: " + str(inquiry.requested_service or 'Not found'))
                st.text("Budget: " + str(inquiry.budget_stated or 'Not mentioned'))
                st.text("Timeline: " + str(inquiry.timeline_stated or 'Not mentioned'))
                st.text(inquiry.original_text)
            
            with col2:
                st.write("**Source Metadata**")
                st.text("Source: " + str(inquiry.source.value))
                if result.source_metadata:
                    st.text("URL: " + str(result.source_metadata.get('url', 'N/A')))
                    st.text("Title: " + str(result.source_metadata.get('title', 'N/A')))
            
            # Missing information
            if inquiry.missing_information:
                st.subheader("⚠️ Missing Information")
                for mi in inquiry.missing_information:
                    st.write(f"  - {mi.description} ({mi.importance})")
            
            # Duplicate check
            if result.duplicate_check:
                st.warning(f"⚠️ Potential duplicates found: {len(result.duplicate_check)} existing inquiry(ies)")
                for dup in result.duplicate_check:
                    st.write(f"  - {dup.inquiry_id}: {dup.customer_name or 'Unknown'} ({dup.customer_email or 'No email'})")
            
            # Original text preview
            with st.expander("📄 Original Extracted Text"):
                st.text(inquiry.original_text[:2000] + ("..." if len(inquiry.original_text) > 2000 else ""))
            
            # Confirmation buttons
            st.markdown("---")
            col1, col2 = st.columns(2)
            with col1:
                if st.button("✅ Confirm & Save Inquiry", type="primary", use_container_width=True):
                    with st.spinner("Saving inquiry..."):
                        try:
                            saved = asyncio.run(save_confirmed_inquiry(inquiry))
                            st.success(f"Inquiry saved! ID: {saved.inquiry_id}")
                            # Clear the extraction result
                            del st.session_state["browser_extraction_result"]
                            st.rerun()
                        except Exception as e:
                            st.error(f"Failed to save: {e}")
            with col2:
                if st.button("❌ Cancel", use_container_width=True):
                    del st.session_state["browser_extraction_result"]
                    st.rerun()
                    
        elif result.status == ExtractionStatus.NO_INQUIRY:
            st.warning("📭 This page does not appear to contain a service inquiry")
            if result.warnings:
                for w in result.warnings:
                    st.write(f"  - {w['message']}")
            if st.button("Try another page"):
                del st.session_state["browser_extraction_result"]
                st.rerun()
                
        elif result.status == ExtractionStatus.INSUFFICIENT:
            st.warning("📝 Insufficient content to extract an inquiry")
            if result.error_message:
                st.error(result.error_message)
            if st.button("Try another page"):
                del st.session_state["browser_extraction_result"]
                st.rerun()
                
        else:
            st.error(f"❌ Extraction failed: {result.error_message}")
            if st.button("Try again"):
                del st.session_state["browser_extraction_result"]
                st.rerun()

    # Sample fixtures section
    st.markdown("---")
    st.subheader("🧪 Test Fixtures")
    st.markdown("Test the browser adapter with sample HTML fixtures:")
    
    fixture_cols = st.columns(4)
    fixtures = [
        ("Complete Inquiry", "test_msg_complete.html"),
        ("Missing Phone", "test_msg_missing_phone.html"),
        ("Non-Inquiry", "test_msg_non_inquiry.html"),
        ("Spam", "test_msg_spam.html"),
    ]
    
    for i, (label, filename) in enumerate(fixtures):
        with fixture_cols[i]:
            if st.button(f"Test: {label}", key=f"fixture_{i}"):
                import os
                fixture_path = os.path.join("data", filename)
                with st.spinner(f"Testing {label}..."):
                    try:
                        result = asyncio.run(extract_from_file(fixture_path))
                        st.session_state["browser_extraction_result"] = result
                        st.rerun()
                    except Exception as e:
                        st.error(f"Failed: {e}")


def render_instagram_intake():
    """Instagram Professional inbound intake interface."""
    st.header("Instagram Intake")

def render_facebook_intake():
    """Facebook Page Messenger inbound intake interface."""
    st.header("Facebook Messenger Intake")


def render_followups():
    """Follow-up task workspace."""
    st.header("Follow-ups")
    repository = get_repository()
    tasks = repository.get_all_tasks()

    if not tasks:
        st.info("No follow-up tasks found.")
        return

    pending = [t for t in tasks if not t.completed]
    completed = [t for t in tasks if t.completed]

    from datetime import datetime, timezone
    now = datetime.now(timezone.utc).isoformat()
    overdue = [t for t in pending if t.due_at and t.due_at < now]

    if overdue:
        st.subheader("Overdue")
        for t in overdue:
            st.warning("Overdue: " + t.description + " - Due: " + str(t.due_at))
            if st.button("Complete", key="complete_" + t.task_id):
                repository.complete_task(t.task_id)
                st.rerun()

    if pending:
        st.subheader("Pending")
        for t in pending:
            if t not in overdue:
                st.write(t.description + " - Due: " + str(t.due_at))
                if st.button("Complete", key="complete_" + t.task_id):
                    repository.complete_task(t.task_id)
                    st.rerun()
    else:
        st.info("No pending follow-ups.")

    if completed:
        st.subheader("Completed")
        for t in completed:
            st.text("Done: " + t.description)
    else:
        st.info("No completed follow-ups.")


def render_intake():
    """Central intake centre for all channels."""
    st.header("Intake")
    st.markdown("Select an intake channel to process inquiries.")

    channel = st.selectbox("Intake Channel", ["Manual", "CSV Import", "Browser", "Instagram", "Facebook"])

    if channel == "Manual" or channel == "CSV Import":
        render_import()
    elif channel == "Browser":
        render_browser_intake()
    elif channel == "Instagram":
        render_instagram_intake()
    elif channel == "Facebook":
        render_facebook_intake()


def render_reports():
    """Reports and analytics."""
    st.header("Reports")
    repository = get_repository()
    from leadlift.dashboard import Dashboard
    dashboard = Dashboard(repository)

    summary = dashboard.summary()
    st.subheader("Summary")
    col1, col2, col3, col4 = st.columns(4)
    col1.metric("Total Inquiries", summary["total_inquiries"])
    col2.metric("New", summary["new_inquiries"])
    col3.metric("Won", summary["won"])
    col4.metric("Lost", summary["lost"])

    st.subheader("By Status")
    st.dataframe([{"Status": k, "Count": v} for k, v in summary["inquiries_by_status"].items() if v > 0], width="stretch")

    st.subheader("By Source")
    st.dataframe([{"Source": k, "Count": v} for k, v in summary["inquiries_by_source"].items() if v > 0], width="stretch")

    st.subheader("Priority Distribution")
    pd = dashboard.priority_distribution()
    st.dataframe([{"Priority": k, "Count": v} for k, v in pd.items()], width="stretch")

    st.subheader("Trends (30 days)")
    trends = dashboard.trends(30)
    if trends["daily"]:
        st.dataframe(trends["daily"], width="stretch")
    else:
        st.info("No trend data available.")

    st.subheader("Missing Information")
    missing = dashboard.missing_info_report()
    if missing:
        st.dataframe(missing, width="stretch")
    else:
        st.info("No missing information.")

    st.subheader("Export")
    inquiries = repository.get_all()
    if inquiries:
        import csv, io, json
        col1, col2 = st.columns(2)
        with col1:
            output = io.StringIO()
            writer = csv.writer(output)
            writer.writerow(["inquiry_id", "customer_name", "customer_email", "source", "status", "priority_score", "received_at"])
            for i in inquiries:
                writer.writerow([_sanitize_csv_value(i.inquiry_id), _sanitize_csv_value(i.customer_name or ""), _sanitize_csv_value(i.customer_email or ""), _sanitize_csv_value(i.source.value), _sanitize_csv_value(i.status.value), i.priority_score, i.received_at])
            st.download_button("Download CSV", output.getvalue(), "inquiries.csv", "text/csv")
        with col2:
            data = [{"inquiry_id": i.inquiry_id, "customer_name": i.customer_name, "source": i.source.value, "status": i.status.value, "priority_score": i.priority_score, "received_at": i.received_at} for i in inquiries]
            st.download_button("Download JSON", json.dumps(data, indent=2, default=str), "inquiries.json", "application/json")
    else:
        st.info("No inquiries to export.")


def render_backup():
    """Database backup interface."""
    st.header("Database Backup")
    st.markdown("Create a verified local backup of the LEADLIFT database.")

    settings = get_settings()
    backup_dir = settings.backup_dir

    st.text("Backup Directory: " + str(backup_dir))
    st.text("Retention: " + str(settings.backup_retention) + " backups")

    if st.button("Create Backup", type="primary"):
        with st.spinner("Creating backup..."):
            from leadlift.db import create_backup, list_backups
            result = create_backup()
            if result["success"]:
                st.success("Backup created successfully!")
                st.text("Filename: " + result["filename"])
                st.text("Path: " + result["path"])
            else:
                st.error("Backup failed: " + result["error"])

    st.markdown("---")
    st.subheader("Available Backups")
    from leadlift.db import list_backups
    backups = list_backups()
    if backups:
        for b in backups:
            st.text("  - " + b["filename"] + " (" + str(b["size_bytes"]) + " bytes)")
    else:
        st.info("No backups found.")

    st.markdown("---")
    st.subheader("Database Health")
    if st.button("Check Database Health"):
        from leadlift.db import check_database_health
        health = check_database_health()
        if health["status"] == "healthy":
            st.success("Database is healthy")
        elif health["status"] == "unhealthy":
            st.error("Database has issues")
        else:
            st.warning("Unable to check database")
        st.text("Integrity: " + str(health["integrity"]))
        st.text("Foreign Keys: " + str(health["foreign_keys"]))
        st.text("Schema Version: " + str(health["schema_version"]))
        if health["error"]:
            st.error("Error: " + health["error"])

    st.markdown("---")
    st.subheader("Backup Security")
    st.markdown("Backups use SQLite's online backup API for safe concurrent access.")


def render_settings():
    """Settings and operations."""
    st.header("Settings")
    from leadlift.db import create_backup, list_backups, check_database_health
    settings = get_settings()
    st.text("Backup Directory: " + str(settings.backup_dir))
    st.text("Retention: " + str(settings.backup_retention) + " backups")

    if st.button("Create Backup", type="primary"):
        with st.spinner("Creating backup..."):
            result = create_backup()
            if result["success"]:
                st.success("Backup created successfully!")
                st.text("Filename: " + result["filename"])
                st.text("Path: " + result["path"])
            else:
                st.error("Backup failed: " + result["error"])

    st.markdown("---")
    st.subheader("Available Backups")
    backups = list_backups()
    if backups:
        for b in backups:
            st.text("  - " + b["filename"] + " (" + str(b["size_bytes"]) + " bytes)")
    else:
        st.info("No backups found.")

    st.markdown("---")
    st.subheader("Database Health")
    if st.button("Check Database Health"):
        health = check_database_health()
        if health["status"] == "healthy":
            st.success("Database is healthy")
        elif health["status"] == "unhealthy":
            st.error("Database has issues")
        else:
            st.warning("Unable to check database")
        st.text("Integrity: " + str(health["integrity"]))
        st.text("Foreign Keys: " + str(health["foreign_keys"]))
        st.text("Schema Version: " + str(health["schema_version"]))
        if health["error"]:
            st.error("Error: " + health["error"])

    st.markdown("---")
    st.subheader("Backup Security")
    st.markdown("Backups use SQLite's online backup API for safe concurrent access.")


def main():
    from leadlift.logging_config import setup_logging
    from leadlift.config import get_settings
    settings = get_settings()
    setup_logging(
        log_dir=getattr(settings, 'log_dir', None),
        log_level=getattr(settings, 'log_level', 'INFO'),
        max_bytes=getattr(settings, 'log_max_bytes', 5 * 1024 * 1024),
        backup_count=getattr(settings, 'log_backup_count', 5),
    )
    get_settings()
    render_header()

    from leadlift.ui_state import init_session_state
    init_session_state()

    nav = st.sidebar.radio("Navigate", ["Dashboard", "Inquiries", "Follow-ups", "Intake", "Intelligence", "Reports", "Settings"])

    if nav == "Dashboard":
        render_dashboard()
    elif nav == "Inquiries":
        render_inquiries()
    elif nav == "Follow-ups":
        render_followups()
    elif nav == "Intake":
        render_intake()
    elif nav == "Intelligence":
        render_process()
    elif nav == "Reports":
        render_reports()
    elif nav == "Settings":
        render_settings()


if __name__ == "__main__":
    main()
