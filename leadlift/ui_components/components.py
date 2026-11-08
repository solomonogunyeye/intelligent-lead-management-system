"""LEADLIFT — Reusable UI components.

Thin wrappers around Streamlit widgets with consistent styling.
No business logic — presentation only.
"""

from __future__ import annotations

import streamlit as st


def page_header(title: str, subtitle: str = "") -> None:
    """Render a consistent page header."""
    st.header(title)
    if subtitle:
        st.markdown(subtitle)
    st.markdown("---")


def empty_state(message: str) -> None:
    """Render an empty-state message."""
    st.info(message)


def error_banner(message: str) -> None:
    """Render an error banner."""
    st.error(message)


def success_banner(message: str) -> None:
    """Render a success banner."""
    st.success(message)


def warning_banner(message: str) -> None:
    """Render a warning banner."""
    st.warning(message)


def status_badge(status: str) -> str:
    """Return a colored status badge string."""
    colors = {
        "New": "🔵",
        "Contacted": "🟡",
        "Qualified": "🟢",
        "Proposal Sent": "🟣",
        "Won": "✅",
        "Lost": "❌",
    }
    return f"{colors.get(status, '⚪')} {status}"


def priority_indicator(score: float) -> str:
    """Return a priority indicator string."""
    if score >= 0.7:
        return f"🔴 High ({score:.2f})"
    elif score >= 0.4:
        return f"🟡 Medium ({score:.2f})"
    else:
        return f"🟢 Low ({score:.2f})"


def inquiry_summary_card(
    inquiry_id: str,
    customer_name: str | None,
    requested_service: str | None,
    source: str,
    status: str,
    priority: float,
) -> None:
    """Render a compact inquiry summary card."""
    st.markdown(f"**{customer_name or 'Unknown'}** — {requested_service or 'No service'}")
    st.caption(f"Source: {source} | Status: {status} | Priority: {priority:.2f}")
