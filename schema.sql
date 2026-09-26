-- Schema for the AVC Data Warehouse
-- Table to store feature vectors and metadata
CREATE TABLE IF NOT EXISTS avc_features (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gateway_timestamp REAL NOT NULL,  -- Unix timestamp when data was received
    feature_0 REAL NOT NULL,
    feature_1 REAL NOT NULL,
    feature_2 REAL NOT NULL,
    feature_3 REAL NOT NULL,
    feature_4 REAL NOT NULL,
    feature_5 REAL NOT NULL,
    feature_6 REAL NOT NULL,
    feature_7 REAL NOT NULL,
    feature_8 REAL NOT NULL,
    feature_9 REAL NOT NULL,
    feature_10 REAL NOT NULL,
    feature_11 REAL NOT NULL,
    feature_12 REAL NOT NULL,
    label TEXT,                       -- Optional label (word spoken)
    source TEXT DEFAULT 'udp'         -- Source of data (udp, serial, file)
);
-- Index on timestamp for faster queries
CREATE INDEX IF NOT EXISTS idx_gateway_timestamp ON avc_features(gateway_timestamp);
