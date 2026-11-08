"""LEADLIFT — Repository layer for data access."""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any, List, Optional, Sequence

from leadlift.db import get_connection, get_db
from leadlift.models import (
    BusinessConfig,
    FollowUpTask,
    Inquiry,
    InquiryStatus,
    ProcessingHistoryEntry,
    ResponseDraft,
    StatusHistoryEntry,
)


class InquiryRepository:
    """Data access for inquiries and related records."""

    # --- Inquiries ---

    def save(self, inquiry: Inquiry, conn=None) -> None:
        """Insert a new inquiry record.

        Uses plain INSERT (not INSERT OR REPLACE) to prevent silent
        deletion/recreation of existing inquiries. Raises IntegrityError
        if an inquiry with the same inquiry_id already exists.
        """
        data = inquiry.to_db_dict()
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        values = list(data.values())
        sql = f"INSERT INTO inquiries ({cols}) VALUES ({placeholders})"

        if conn is None:
            with get_db() as conn:
                conn.execute(sql, values)
                conn.commit()
        else:
            conn.execute(sql, values)

    def update(self, inquiry: Inquiry, conn=None) -> None:
        """Update an existing inquiry record.

        Uses UPDATE ... SET ... WHERE inquiry_id = ?.
        Only updates fields present in the inquiry's to_db_dict().
        """
        data = inquiry.to_db_dict()
        # Remove inquiry_id from SET clause (it's the key)
        inquiry_id = data.pop("inquiry_id", None)
        if inquiry_id is None:
            raise ValueError("inquiry_id is required for update")

        set_clause = ", ".join(f"{k} = ?" for k in data.keys())
        values = list(data.values()) + [inquiry_id]
        sql = f"UPDATE inquiries SET {set_clause} WHERE inquiry_id = ?"

        if conn is None:
            with get_db() as conn:
                cursor = conn.execute(sql, values)
                conn.commit()
                if cursor.rowcount == 0:
                    raise ValueError(f"Inquiry not found: {inquiry_id}")
        else:
            cursor = conn.execute(sql, values)
            if cursor.rowcount == 0:
                raise ValueError(f"Inquiry not found: {inquiry_id}")

    def save_if_not_duplicate(self, inquiry: Inquiry) -> tuple:
        """Atomically insert an inquiry if no duplicate exists.

        Uses INSERT ... ON CONFLICT DO NOTHING with the partial unique index
        on (source, source_message_id). Returns (inquiry, inserted) where
        inserted is True if a new row was created, False if it was a duplicate.

        If a duplicate is detected, returns the existing inquiry.
        """
        data = inquiry.to_db_dict()
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        values = list(data.values())
        sql = f"INSERT INTO inquiries ({cols}) VALUES ({placeholders}) ON CONFLICT(source, source_message_id) WHERE source_message_id IS NOT NULL AND source_message_id != '' DO NOTHING"

        with get_db() as conn:
            cursor = conn.execute(sql, values)
            conn.commit()
            if cursor.rowcount > 0:
                return inquiry, True
            # Duplicate - fetch existing
            existing = conn.execute(
                "SELECT * FROM inquiries WHERE source = ? AND source_message_id = ?",
                (inquiry.source.value, inquiry.source_message_id),
            ).fetchone()
            if existing:
                return Inquiry.from_db_row(existing), False
            return inquiry, False

    def get(self, inquiry_id: str) -> Optional[Inquiry]:
        """Retrieve an inquiry by ID."""
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM inquiries WHERE inquiry_id = ?",
                (inquiry_id,),
            ).fetchone()
            return Inquiry.from_db_row(row) if row else None

    def get_all(
        self,
        status: Optional[InquiryStatus] = None,
        limit: int = 1000,
    ) -> List[Inquiry]:
        """Retrieve all inquiries, optionally filtered by status."""
        with get_db() as conn:
            if status:
                rows = conn.execute(
                    "SELECT * FROM inquiries WHERE status = ? ORDER BY received_at DESC LIMIT ?",
                    (status.value, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM inquiries ORDER BY received_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [Inquiry.from_db_row(r) for r in rows]

    def search(
        self,
        query: str,
        limit: int = 100,
    ) -> List[Inquiry]:
        """Search inquiries by customer name, email, phone, organization, service, source, or text."""
        if not query or not query.strip():
            return self.get_all(limit=limit)

        with get_db() as conn:
            pattern = f"%{query.strip()}%"
            rows = conn.execute(
                """SELECT * FROM inquiries
                   WHERE customer_name LIKE ?
                      OR customer_email LIKE ?
                      OR customer_phone LIKE ?
                      OR organization LIKE ?
                      OR requested_service LIKE ?
                      OR source LIKE ?
                      OR original_text LIKE ?
                   ORDER BY received_at DESC
                   LIMIT ?""",
                (pattern, pattern, pattern, pattern, pattern, pattern, pattern, limit),
            ).fetchall()
            return [Inquiry.from_db_row(r) for r in rows]

    def search_and_filter(
        self,
        query: str = "",
        status: Optional[InquiryStatus] = None,
        source: Optional[str] = None,
        priority_min: Optional[float] = None,
        date_from: Optional[str] = None,
        date_to: Optional[str] = None,
        limit: int = 1000,
    ) -> List[Inquiry]:
        """Search and filter inquiries with combinable criteria."""
        conditions = []
        params = []

        if query and query.strip():
            pattern = f"%{query.strip()}%"
            conditions.append(
                "(customer_name LIKE ? OR customer_email LIKE ? OR customer_phone LIKE ? "
                "OR organization LIKE ? OR requested_service LIKE ? OR source LIKE ? OR original_text LIKE ?)"
            )
            params.extend([pattern] * 7)

        if status is not None:
            conditions.append("status = ?")
            params.append(status.value)

        if source:
            conditions.append("source = ?")
            params.append(source)

        if priority_min is not None:
            conditions.append("priority_score >= ?")
            params.append(priority_min)

        if date_from:
            conditions.append("received_at >= ?")
            params.append(date_from)

        if date_to:
            conditions.append("received_at <= ?")
            params.append(date_to)

        where = " AND ".join(conditions) if conditions else "1=1"
        sql = f"SELECT * FROM inquiries WHERE {where} ORDER BY received_at DESC LIMIT ?"
        params.append(limit)

        with get_db() as conn:
            rows = conn.execute(sql, params).fetchall()
            return [Inquiry.from_db_row(r) for r in rows]

    def update_status(self, inquiry_id: str, new_status: InquiryStatus, reason: Optional[str] = None) -> None:
        """Update an inquiry status and record the history entry."""
        valid_statuses = {s.value for s in InquiryStatus}
        if new_status.value not in valid_statuses:
            raise ValueError(f"Invalid status: {new_status}")

        with get_db() as conn:
            row = conn.execute(
                "SELECT status FROM inquiries WHERE inquiry_id = ?",
                (inquiry_id,),
            ).fetchone()
            previous_status = InquiryStatus(row["status"]) if row else None
            current = now_str()

            conn.execute(
                "UPDATE inquiries SET status = ?, updated_at = ? WHERE inquiry_id = ?",
                (new_status.value, current, inquiry_id),
            )
            conn.execute(
                "INSERT INTO status_history (inquiry_id, previous_status, new_status, changed_at, reason) "
                "VALUES (?, ?, ?, ?, ?)",
                (inquiry_id, previous_status.value if previous_status else None,
                 new_status.value, current, reason),
            )
            conn.commit()

    def update_ai_state(self, inquiry_id: str, state: str, error_message: Optional[str] = None) -> None:
        """Update the AI processing state of an inquiry."""
        current = now_str()
        with get_db() as conn:
            conn.execute(
                "UPDATE inquiries SET ai_state = ?, last_processed_at = ? WHERE inquiry_id = ?",
                (state, current, inquiry_id),
            )
            conn.commit()

    def update_priority(self, inquiry_id: str, score: float, explanation: str) -> None:
        """Update priority score and explanation."""
        with get_db() as conn:
            conn.execute(
                "UPDATE inquiries SET priority_score = ?, priority_explanation = ?, updated_at = ? WHERE inquiry_id = ?",
                (score, explanation, now_str(), inquiry_id),
            )
            conn.commit()

    def update_classification(self, inquiry_id: str, classification: str, source: str) -> None:
        """Update classification and classification source. Source is required."""
        with get_db() as conn:
            conn.execute(
                "UPDATE inquiries SET classification = ?, classification_source = ?, updated_at = ? WHERE inquiry_id = ?",
                (classification, source, now_str(), inquiry_id),
            )
            conn.commit()

    def increment_processed(self, inquiry_id: str) -> None:
        """Update last_processed_at timestamp."""
        with get_db() as conn:
            conn.execute(
                "UPDATE inquiries SET last_processed_at = ? WHERE inquiry_id = ?",
                (now_str(), inquiry_id),
            )
            conn.commit()

    def delete(self, inquiry_id: str) -> bool:
        """Delete an inquiry and its related records."""
        with get_db() as conn:
            conn.execute("DELETE FROM status_history WHERE inquiry_id = ?", (inquiry_id,))
            conn.execute("DELETE FROM processing_history WHERE inquiry_id = ?", (inquiry_id,))
            conn.execute("DELETE FROM follow_up_tasks WHERE inquiry_id = ?", (inquiry_id,))
            result = conn.execute("DELETE FROM inquiries WHERE inquiry_id = ?", (inquiry_id,))
            conn.commit()
            return result.rowcount > 0

    # --- Status History ---

    def get_status_history(self, inquiry_id: str) -> List[StatusHistoryEntry]:
        """Retrieve status change history for an inquiry."""
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM status_history WHERE inquiry_id = ? ORDER BY changed_at",
                (inquiry_id,),
            ).fetchall()
            entries = []
            for row in rows:
                data = dict(row)
                data["previous_status"] = InquiryStatus(data["previous_status"]) if data["previous_status"] else None
                data["new_status"] = InquiryStatus(data["new_status"])
                entries.append(StatusHistoryEntry(**data))
            return entries

    # --- Processing History ---

    def add_processing_entry(self, entry: ProcessingHistoryEntry, conn=None) -> int:
        """Record a processing history entry."""
        data = entry.to_db_dict()
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        values = list(data.values())
        sql = f"INSERT INTO processing_history ({cols}) VALUES ({placeholders})"

        if conn is None:
            with get_db() as conn:
                cur = conn.execute(sql, values)
                conn.commit()
                return cur.lastrowid
        else:
            cur = conn.execute(sql, values)
            return cur.lastrowid

    def get_processing_history(self, inquiry_id: str) -> List[ProcessingHistoryEntry]:
        """Retrieve processing history for an inquiry."""
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM processing_history WHERE inquiry_id = ? ORDER BY processed_at",
                (inquiry_id,),
            ).fetchall()
            entries = []
            for row in rows:
                data = dict(row)
                if data.get("details_json"):
                    data["details"] = json.loads(data["details_json"])
                entries.append(ProcessingHistoryEntry(**data))
            return entries

    # --- Follow-up Tasks ---

    def save_task(self, task: FollowUpTask, conn=None) -> None:
        """Insert or replace a follow-up task."""
        data = task.to_db_dict()
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        values = list(data.values())
        sql = f"INSERT OR REPLACE INTO follow_up_tasks ({cols}) VALUES ({placeholders})"

        if conn is None:
            with get_db() as conn:
                conn.execute(sql, values)
                conn.commit()
        else:
            conn.execute(sql, values)

    def get_task(self, task_id: str) -> Optional[FollowUpTask]:
        """Retrieve a task by ID."""
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM follow_up_tasks WHERE task_id = ?",
                (task_id,),
            ).fetchone()
            return FollowUpTask.from_db_row(row) if row else None

    def get_tasks_for_inquiry(self, inquiry_id: str) -> List[FollowUpTask]:
        """Retrieve all tasks for an inquiry."""
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM follow_up_tasks WHERE inquiry_id = ? ORDER BY due_at",
                (inquiry_id,),
            ).fetchall()
            return [FollowUpTask.from_db_row(r) for r in rows]

    def get_all_tasks(self, include_completed: bool = False, limit: int = 500) -> List[FollowUpTask]:
        """Retrieve all tasks, optionally including completed."""
        with get_db() as conn:
            if include_completed:
                rows = conn.execute(
                    "SELECT * FROM follow_up_tasks ORDER BY due_at LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM follow_up_tasks WHERE completed = 0 ORDER BY due_at LIMIT ?",
                    (limit,),
                ).fetchall()
            return [FollowUpTask.from_db_row(r) for r in rows]

    def complete_task(self, task_id: str, completed_at: Optional[str] = None) -> None:
        """Mark a task as completed."""
        ts = completed_at or now_str()
        with get_db() as conn:
            conn.execute(
                "UPDATE follow_up_tasks SET completed = 1, completed_at = ?, updated_at = ? WHERE task_id = ?",
                (ts, ts, task_id),
            )
            conn.commit()

    def delete_task(self, task_id: str) -> bool:
        """Delete a follow-up task."""
        with get_db() as conn:
            result = conn.execute("DELETE FROM follow_up_tasks WHERE task_id = ?", (task_id,))
            conn.commit()
            return result.rowcount > 0

    # --- Business Config ---

    def get_business_config(self) -> BusinessConfig:
        """Retrieve the business configuration."""
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM business_config WHERE id = 1",
            ).fetchone()
            if row:
                return BusinessConfig.from_db_row(row)
            return BusinessConfig()

    def update_business_config(self, config: BusinessConfig) -> None:
        """Update the business configuration."""
        data = config.to_db_dict()
        with get_db() as conn:
            cols = ", ".join(f"{k} = ?" for k in data)
            values = list(data.values()) + [1]
            conn.execute(
                f"UPDATE business_config SET {cols} WHERE id = ?",
                values,
            )
            conn.commit()

    # --- Duplicate detection ---

    def find_duplicates(
        self,
        customer_email: Optional[str],
        text_fingerprint: Optional[str],
        conversation_id: Optional[str],
        message_id: Optional[str],
    ) -> List[Inquiry]:
        """
        Find potential duplicate inquiries based on available matching criteria.

        Duplicate detection is advisory: results are reviewed, not automatically merged.
        """
        if not customer_email and not text_fingerprint and not conversation_id and not message_id:
            return []

        with get_db() as conn:
            conditions = []
            values: list = []

            if customer_email:
                conditions.append("customer_email = ?")
                values.append(customer_email.lower())

            if text_fingerprint:
                conditions.append("text_fingerprint = ?")
                values.append(text_fingerprint)

            if conversation_id:
                conditions.append("source_conversation_id = ?")
                values.append(conversation_id)

            if message_id:
                conditions.append("source_message_id = ?")
                values.append(message_id)

            sql = f"""SELECT * FROM inquiries
                        WHERE {' OR '.join(conditions)}
                        ORDER BY received_at DESC"""

            rows = conn.execute(sql, values).fetchall()
            return [Inquiry.from_db_row(r) for r in rows]

    # --- Count queries for dashboard ---

    def count_by_status(self) -> dict:
        """Count inquiries grouped by status."""
        with get_db() as conn:
            rows = conn.execute(
                "SELECT status, COUNT(*) as count FROM inquiries GROUP BY status"
            ).fetchall()
            return {row["status"]: row["count"] for row in rows}

    def count_by_source(self) -> dict:
        """Count inquiries grouped by source."""
        with get_db() as conn:
            rows = conn.execute(
                "SELECT source, COUNT(*) as count FROM inquiries GROUP BY source"
            ).fetchall()
            return {row["source"]: row["count"] for row in rows}

    def total_count(self) -> int:
        """Total number of inquiries."""
        with get_db() as conn:
            return conn.execute("SELECT COUNT(*) as count FROM inquiries").fetchone()["count"]

    def count_new(self) -> int:
        """Count inquiries with status 'New'."""
        with get_db() as conn:
            return conn.execute(
                "SELECT COUNT(*) as count FROM inquiries WHERE status = ?",
                (InquiryStatus.NEW.value,),
            ).fetchone()["count"]

    def count_by_ai_state(self) -> dict:
        """Count inquiries grouped by AI processing state."""
        with get_db() as conn:
            rows = conn.execute(
                "SELECT ai_state, COUNT(*) as count FROM inquiries GROUP BY ai_state"
            ).fetchall()
            return {row["ai_state"]: row["count"] for row in rows}

    # --- Response Drafts ---

    def save_draft(self, draft: ResponseDraft, conn=None) -> None:
        """Insert or replace a response draft."""
        data = draft.to_db_dict()
        cols = ", ".join(data.keys())
        placeholders = ", ".join("?" for _ in data)
        values = list(data.values())
        sql = f"INSERT OR REPLACE INTO response_drafts ({cols}) VALUES ({placeholders})"

        if conn is None:
            with get_db() as conn:
                conn.execute(sql, values)
                conn.commit()
        else:
            conn.execute(sql, values)

    def get_draft(self, draft_id: str) -> Optional[ResponseDraft]:
        """Retrieve a draft by ID."""
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM response_drafts WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()
            return ResponseDraft.from_db_row(row) if row else None

    def get_drafts_for_inquiry(self, inquiry_id: str) -> List[ResponseDraft]:
        """Retrieve all drafts for an inquiry."""
        with get_db() as conn:
            rows = conn.execute(
                "SELECT * FROM response_drafts WHERE inquiry_id = ? ORDER BY created_at DESC",
                (inquiry_id,),
            ).fetchall()
            return [ResponseDraft.from_db_row(r) for r in rows]

    def get_latest_draft(self, inquiry_id: str) -> Optional[ResponseDraft]:
        """Retrieve the latest draft for an inquiry."""
        with get_db() as conn:
            row = conn.execute(
                "SELECT * FROM response_drafts WHERE inquiry_id = ? ORDER BY created_at DESC LIMIT 1",
                (inquiry_id,),
            ).fetchone()
            return ResponseDraft.from_db_row(row) if row else None

    def update_draft_status(self, draft_id: str, status: str) -> None:
        """Update draft status."""
        with get_db() as conn:
            conn.execute(
                "UPDATE response_drafts SET status = ?, updated_at = ? WHERE draft_id = ?",
                (status, now_str(), draft_id),
            )
            conn.commit()

    def update_draft_text(self, draft_id: str, new_text: str) -> bool:
        """Update draft text content."""
        with get_db() as conn:
            result = conn.execute(
                "UPDATE response_drafts SET draft_text = ?, updated_at = ? WHERE draft_id = ?",
                (new_text, now_str(), draft_id),
            )
            conn.commit()
            return result.rowcount > 0

    def delete_draft(self, draft_id: str) -> bool:
        """Delete a response draft."""
        with get_db() as conn:
            result = conn.execute("DELETE FROM response_drafts WHERE draft_id = ?", (draft_id,))
            conn.commit()
            return result.rowcount > 0


def now_str() -> str:
    """Current UTC timestamp in ISO format."""
    return datetime.utcnow().isoformat() + "Z"


# Module-level singleton
_repository: Optional[InquiryRepository] = None


def get_repository() -> InquiryRepository:
    """Get the repository singleton."""
    global _repository
    if _repository is None:
        _repository = InquiryRepository()
    return _repository