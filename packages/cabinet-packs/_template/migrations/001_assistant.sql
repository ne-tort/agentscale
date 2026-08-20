-- Minimal schema for generic-assistant cabinets.
CREATE TABLE IF NOT EXISTS assistant_notes (
  id TEXT PRIMARY KEY,
  project_id TEXT,
  body TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL
);
