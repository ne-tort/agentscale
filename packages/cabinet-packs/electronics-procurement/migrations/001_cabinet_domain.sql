-- Cabinet-local index for electronics-procurement (not platform schema).
CREATE TABLE IF NOT EXISTS equipment_cards (
  id TEXT PRIMARY KEY,
  project_id TEXT,
  part_number TEXT,
  title TEXT,
  payload_json TEXT NOT NULL DEFAULT '{}',
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS run_index (
  run_id TEXT PRIMARY KEY,
  project_id TEXT NOT NULL,
  phase TEXT,
  updated_at TEXT NOT NULL
);
