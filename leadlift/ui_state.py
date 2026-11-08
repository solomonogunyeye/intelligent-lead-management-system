"""LEADLIFT — UI session state management.

Centralised session-state helpers for the Streamlit application.
Uses native Streamlit session_state — no external state-management framework.
"""

from __future__ import annotations

import streamlit as st


# ---------------------------------------------------------------------------
# Session-state keys
# ---------------------------------------------------------------------------

KEY_CURRENT_PAGE = "leadlift_current_page"
KEY_SELECTED_INQUIRY = "leadlift_selected_inquiry"
KEY_SEARCH_QUERY = "leadlift_search_query"
KEY_STATUS_FILTER = "leadlift_status_filter"
KEY_SOURCE_FILTER = "leadlift_source_filter"
KEY_PRIORITY_MIN = "leadlift_priority_min"
KEY_DATE_FROM = "leadlift_date_from"
KEY_INTAKE_CHANNEL = "leadlift_intake_channel"
KEY_AI_PROCESSING = "leadlift_ai_processing"


def init_session_state() -> None:
    """Initialise all session-state keys with defaults."""
    defaults = {
        KEY_CURRENT_PAGE: "Dashboard",
        KEY_SELECTED_INQUIRY: None,
        KEY_SEARCH_QUERY: "",
        KEY_STATUS_FILTER: "All",
        KEY_SOURCE_FILTER: "All",
        KEY_PRIORITY_MIN: 0.0,
        KEY_DATE_FROM: None,
        KEY_INTAKE_CHANNEL: "Manual",
        KEY_AI_PROCESSING: False,
    }
    for key, value in defaults.items():
        if key not in st.session_state:
            st.session_state[key] = value


def get_current_page() -> str:
    return st.session_state.get(KEY_CURRENT_PAGE, "Dashboard")


def set_current_page(page: str) -> None:
    st.session_state[KEY_CURRENT_PAGE] = page


def get_selected_inquiry() -> str | None:
    return st.session_state.get(KEY_SELECTED_INQUIRY)


def set_selected_inquiry(inquiry_id: str | None) -> None:
    st.session_state[KEY_SELECTED_INQUIRY] = inquiry_id


def get_search_query() -> str:
    return st.session_state.get(KEY_SEARCH_QUERY, "")


def set_search_query(query: str) -> None:
    st.session_state[KEY_SEARCH_QUERY] = query


def get_status_filter() -> str:
    return st.session_state.get(KEY_STATUS_FILTER, "All")


def set_status_filter(status: str) -> None:
    st.session_state[KEY_STATUS_FILTER] = status


def get_source_filter() -> str:
    return st.session_state.get(KEY_SOURCE_FILTER, "All")


def set_source_filter(source: str) -> None:
    st.session_state[KEY_SOURCE_FILTER] = source


def get_priority_min() -> float:
    return st.session_state.get(KEY_PRIORITY_MIN, 0.0)


def set_priority_min(value: float) -> None:
    st.session_state[KEY_PRIORITY_MIN] = value


def get_date_from():
    return st.session_state.get(KEY_DATE_FROM)


def set_date_from(value) -> None:
    st.session_state[KEY_DATE_FROM] = value


def get_intake_channel() -> str:
    return st.session_state.get(KEY_INTAKE_CHANNEL, "Manual")


def set_intake_channel(channel: str) -> None:
    st.session_state[KEY_INTAKE_CHANNEL] = channel


def is_ai_processing() -> bool:
    return st.session_state.get(KEY_AI_PROCESSING, False)


def set_ai_processing(value: bool) -> None:
    st.session_state[KEY_AI_PROCESSING] = value
