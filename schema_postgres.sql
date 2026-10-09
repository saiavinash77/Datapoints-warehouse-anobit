-- Supabase / PostgreSQL schema for the AVC Data Collection Platform
CREATE EXTENSION IF NOT EXISTS vector;

CREATE TABLE IF NOT EXISTS participants (
    id SERIAL PRIMARY KEY,
    code TEXT UNIQUE NOT NULL,
    age INTEGER,
    gender TEXT,
    language TEXT,
    dialect TEXT,
    notes TEXT,
    consent_given INTEGER NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL
);

CREATE TABLE IF NOT EXISTS sessions (
    id SERIAL PRIMARY KEY,
    participant_id INTEGER NOT NULL REFERENCES participants(id),
    stage INTEGER NOT NULL DEFAULT 1,
    device_mode TEXT NOT NULL DEFAULT 'handheld',
    site_id TEXT,
    operator TEXT,
    mic_gain DOUBLE PRECISION,
    sample_rates_json TEXT,
    started_at DOUBLE PRECISION NOT NULL,
    ended_at DOUBLE PRECISION,
    notes TEXT
);

CREATE TABLE IF NOT EXISTS prompts (
    id SERIAL PRIMARY KEY,
    text TEXT UNIQUE NOT NULL,
    category TEXT NOT NULL DEFAULT 'word',
    language TEXT
);

CREATE TABLE IF NOT EXISTS takes (
    id SERIAL PRIMARY KEY,
    session_id INTEGER NOT NULL REFERENCES sessions(id),
    prompt_id INTEGER NOT NULL REFERENCES prompts(id),
    take_number INTEGER NOT NULL,
    started_at DOUBLE PRECISION NOT NULL,
    duration_s DOUBLE PRECISION NOT NULL DEFAULT 0,
    verdict TEXT,
    verdict_reason TEXT,
    marked_bad INTEGER NOT NULL DEFAULT 0,
    split TEXT
);

CREATE TABLE IF NOT EXISTS windows (
    id SERIAL PRIMARY KEY,
    take_id INTEGER NOT NULL REFERENCES takes(id),
    seq INTEGER NOT NULL,
    t_start_ms DOUBLE PRECISION NOT NULL,
    mic_blob BYTEA,
    piezo_blob BYTEA,
    pressure_blob BYTEA,
    airflow_blob BYTEA,
    f0 DOUBLE PRECISION, f1 DOUBLE PRECISION, f2 DOUBLE PRECISION, f3 DOUBLE PRECISION,
    f4 DOUBLE PRECISION, f5 DOUBLE PRECISION, f6 DOUBLE PRECISION, f7 DOUBLE PRECISION,
    f8 DOUBLE PRECISION, f9 DOUBLE PRECISION, f10 DOUBLE PRECISION, f11 DOUBLE PRECISION,
    f12 DOUBLE PRECISION,
    quality_score DOUBLE PRECISION,
    quality_verdict TEXT,
    packet_type INTEGER,
    window_count INTEGER
);
CREATE INDEX IF NOT EXISTS idx_windows_take ON windows(take_id);

CREATE TABLE IF NOT EXISTS embeddings (
    id SERIAL PRIMARY KEY,
    take_id INTEGER NOT NULL REFERENCES takes(id),
    window_id INTEGER REFERENCES windows(id),
    extractor_name TEXT NOT NULL,
    extractor_version INTEGER NOT NULL,
    dim INTEGER NOT NULL,
    vector BYTEA NOT NULL,
    vec vector(13),
    label TEXT NOT NULL,
    participant_id INTEGER NOT NULL,
    stage INTEGER NOT NULL,
    quality_score DOUBLE PRECISION,
    is_bad INTEGER NOT NULL DEFAULT 0,
    created_at DOUBLE PRECISION NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_embeddings_take ON embeddings(take_id);
CREATE INDEX IF NOT EXISTS idx_embeddings_label ON embeddings(label);
