-- LEADLIFT — SQLite schema
-- Tables: inquiries, status_history, processing_history, business_config, follow_up_tasks

PRAGMA foreign_keys = ON;

-- ---------------------------------------------------------------------------
-- Business configuration table
-- Stores one row of global configuration (service offerings, thresholds, tone).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS business_config (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    services_json TEXT NOT NULL DEFAULT '[]',
    stale_hours INTEGER NOT NULL DEFAULT 48,
    communication_tone TEXT NOT NULL DEFAULT 'Professional',
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Inquiries table
-- Core record for each customer inquiry. Distinguishes explicit,
-- inferred, and unknown information.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS inquiries (
    inquiry_id TEXT PRIMARY KEY,
    source TEXT NOT NULL DEFAULT 'manual',
    source_platform TEXT,
    source_account TEXT,
    source_conversation_id TEXT,
    source_message_id TEXT,
    text_fingerprint TEXT,  -- SHA256 hash of original_text for content-based dedup
    customer_name TEXT,
    customer_email TEXT,
    customer_phone TEXT,
    organization TEXT,
    requested_service TEXT,
    budget_stated TEXT,
    budget_inferred TEXT,
    timeline_stated TEXT,
    timeline_inferred TEXT,
    classification TEXT,
    classification_source TEXT DEFAULT 'rule',
    missing_information_json TEXT NOT NULL DEFAULT '[]',
    recommended_next_action TEXT,
    priority_score REAL NOT NULL DEFAULT 0.0,
    priority_explanation TEXT,
    status TEXT NOT NULL DEFAULT 'New',
    ai_state TEXT NOT NULL DEFAULT 'pending',
    original_text TEXT NOT NULL,
    received_at TEXT NOT NULL,
    first_observed_at TEXT NOT NULL,
    last_processed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now'))
);

-- ---------------------------------------------------------------------------
-- Status history
-- Records every status change for audit purposes.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inquiry_id TEXT NOT NULL,
    previous_status TEXT,
    new_status TEXT NOT NULL,
    changed_at TEXT NOT NULL DEFAULT (datetime('now')),
    reason TEXT,
    FOREIGN KEY (inquiry_id) REFERENCES inquiries(inquiry_id)
);

-- ---------------------------------------------------------------------------
-- Processing history
-- Records every AI/processing attempt with outcome and error details.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS processing_history (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inquiry_id TEXT NOT NULL,
    step TEXT NOT NULL,
    provider TEXT,
    state TEXT NOT NULL,
    error_message TEXT,
    details_json TEXT CHECK (length(details_json) <= 65535),
    processed_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (inquiry_id) REFERENCES inquiries(inquiry_id)
);

-- ---------------------------------------------------------------------------
-- Follow-up tasks
-- Persistent tasks tied to inquiries. Overdue state is computed at query time
-- from due_at and current time (never stored as a stale flag).
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS follow_up_tasks (
    task_id TEXT PRIMARY KEY,
    inquiry_id TEXT NOT NULL,
    description TEXT NOT NULL,
    due_at TEXT NOT NULL,
    assigned_to TEXT,
    notes TEXT,
    completed INTEGER NOT NULL DEFAULT 0,
    completed_at TEXT,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (inquiry_id) REFERENCES inquiries(inquiry_id)
);

-- ---------------------------------------------------------------------------
-- Response drafts
-- Stores generated response drafts for inquiries.
-- ---------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS response_drafts (
    draft_id TEXT PRIMARY KEY,
    inquiry_id TEXT NOT NULL,
    draft_text TEXT NOT NULL,
    generated_by TEXT NOT NULL,
    model TEXT,
    status TEXT NOT NULL DEFAULT 'draft' CHECK (status IN ('draft', 'edited', 'sent', 'archived')),
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    FOREIGN KEY (inquiry_id) REFERENCES inquiries(inquiry_id)
);

CREATE INDEX IF NOT EXISTS idx_response_drafts_inquiry ON response_drafts(inquiry_id);

-- ---------------------------------------------------------------------------
-- Indexes for common lookups
-- ---------------------------------------------------------------------------
CREATE INDEX IF NOT EXISTS idx_inquiries_status ON inquiries(status);
CREATE INDEX IF NOT EXISTS idx_inquiries_priority ON inquiries(priority_score DESC);
CREATE INDEX IF NOT EXISTS idx_inquiries_source ON inquiries(source);
CREATE INDEX IF NOT EXISTS idx_inquiries_received_at ON inquiries(received_at);
CREATE INDEX IF NOT EXISTS idx_inquiries_text_fingerprint ON inquiries(text_fingerprint);
CREATE INDEX IF NOT EXISTS idx_status_history_inquiry ON status_history(inquiry_id);
CREATE INDEX IF NOT EXISTS idx_processing_history_inquiry ON processing_history(inquiry_id);
CREATE INDEX IF NOT EXISTS idx_followup_tasks_inquiry ON follow_up_tasks(inquiry_id);
CREATE INDEX IF NOT EXISTS idx_followup_tasks_due ON follow_up_tasks(due_at);
CREATE INDEX IF NOT EXISTS idx_followup_tasks_completed ON follow_up_tasks(completed);

-- ---------------------------------------------------------------------------
-- Initialize business config with defaults if empty
-- ---------------------------------------------------------------------------
INSERT INTO business_config (id, services_json, stale_hours, communication_tone)
SELECT 1, '[]', 48, 'Professional'
WHERE NOT EXISTS (SELECT 1 FROM business_config WHERE id = 1);