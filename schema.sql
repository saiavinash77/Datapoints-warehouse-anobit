-- AVC Data Collection Platform schema
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS participants (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    code TEXT UNIQUE NOT NULL,
    age INTEGER,
    gender TEXT,
    language TEXT,
    dialect TEXT,
    notes TEXT,
    consent_given INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    stage INTEGER NOT NULL DEFAULT 1,
    device_mode TEXT NOT NULL DEFAULT 'handheld',
    operator TEXT,
    mic_gain REAL,
    sample_rates_json TEXT,
    started_at REAL NOT NULL,
    ended_at REAL,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT UNIQUE NOT NULL,
    category TEXT NOT NULL DEFAULT 'word',
    language TEXT
);

CREATE TABLE IF NOT EXISTS takes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    prompt_id INTEGER NOT NULL REFERENCES prompts(id),
    take_number INTEGER NOT NULL,
    started_at REAL NOT NULL,
    duration_s REAL NOT NULL DEFAULT 0,
    verdict TEXT,
    verdict_reason TEXT,
    marked_bad INTEGER NOT NULL DEFAULT 0,
    split TEXT
);

CREATE TABLE IF NOT EXISTS windows (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    take_id INTEGER NOT NULL REFERENCES takes(id),
    seq INTEGER NOT NULL,
    t_start_ms REAL NOT NULL,
    mic_blob BLOB,
    piezo_blob BLOB,
    pressure_blob BLOB,
    airflow_blob BLOB,
    f0 REAL, f1 REAL, f2 REAL, f3 REAL, f4 REAL, f5 REAL, f6 REAL,
    f7 REAL, f8 REAL, f9 REAL, f10 REAL, f11 REAL, f12 REAL,
    quality_score REAL,
    quality_verdict TEXT,
    packet_type INTEGER,
    window_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_windows_take ON windows(take_id);

CREATE TABLE IF NOT EXISTS embeddings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    take_id INTEGER NOT NULL REFERENCES takes(id),
    window_id INTEGER REFERENCES windows(id),
    extractor_name TEXT NOT NULL,
    extractor_version INTEGER NOT NULL,
    dim INTEGER NOT NULL,
    vector BLOB NOT NULL,
    label TEXT NOT NULL,
    participant_id INTEGER NOT NULL,
    stage INTEGER NOT NULL,
    quality_score REAL,
    is_bad INTEGER NOT NULL DEFAULT 0,
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_embeddings_take ON embeddings(take_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_label ON embeddings(label);
