#!/usr/bin/env python3
"""SQLite storage layer for the dataset (participants, sessions, prompts, takes, windows, embeddings)."""

import json
import logging
import sqlite3
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_PATH = Path(__file__).resolve().parent.parent / 'schema.sql'
DEFAULT_DB = Path(__file__).resolve().parent.parent / 'avc_collector.db'


class Store:
    def __init__(self, db_path=DEFAULT_DB):
        self.db_path = str(db_path)
        self._local = threading.local()
        self.init_schema()

    def conn(self) -> sqlite3.Connection:
        c = getattr(self._local, 'conn', None)
        if c is None:
            c = sqlite3.connect(self.db_path, timeout=10)
            c.row_factory = sqlite3.Row
            c.execute('PRAGMA foreign_keys = ON')
            self._local.conn = c
        return c

    def init_schema(self):
        schema = SCHEMA_PATH.read_text()
        c = self.conn()
        c.executescript(schema)
        c.commit()
        logger.info("Database ready: %s", self.db_path)

    def query(self, sql, params=()):
        return self.conn().execute(sql, params).fetchall()

    def execute(self, sql, params=()):
        cur = self.conn().execute(sql, params)
        self.conn().commit()
        return cur


# --- participants ---

def next_participant_code(store) -> str:
    row = store.query("SELECT code FROM participants ORDER BY id DESC LIMIT 1")
    if not row:
        return 'P001'
    last = row[0]['code']
    return f'P{int(last[1:]) + 1:03d}'


def create_participant(store, age=None, gender=None, language=None, dialect=None, notes=None, consent=False, code=None):
    code = code or next_participant_code(store)
    cur = store.execute(
        "INSERT INTO participants (code, age, gender, language, dialect, notes, consent_given, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (code, age, gender, language, dialect, notes, int(consent), time.time()))
    return get_participant(store, cur.lastrowid)


def get_participant(store, pid):
    row = store.query("SELECT * FROM participants WHERE id=?", (pid,))
    return dict(row[0]) if row else None


def list_participants(store):
    return [dict(r) for r in store.query("SELECT * FROM participants ORDER BY id")]


# --- sessions ---

def create_session(store, participant_id, stage=1, device_mode='handheld', mic_gain=None, notes=None):
    cur = store.execute(
        "INSERT INTO sessions (participant_id, stage, device_mode, mic_gain, sample_rates_json, started_at, notes) "
        "VALUES (?,?,?,?,?,?,?)",
        (participant_id, stage, device_mode, mic_gain,
         json.dumps({'mic': 16000, 'piezo': 8000, 'pressure': 100, 'airflow': 100}),
         time.time(), notes))
    return get_session(store, cur.lastrowid)


def get_session(store, sid):
    row = store.query("SELECT * FROM sessions WHERE id=?", (sid,))
    return dict(row[0]) if row else None


def list_sessions(store):
    return [dict(r) for r in store.query(
        "SELECT s.*, p.code AS participant_code FROM sessions s JOIN participants p ON p.id=s.participant_id "
        "ORDER BY s.id DESC")]


def end_session(store, sid):
    store.execute("UPDATE sessions SET ended_at=? WHERE id=?", (time.time(), sid))


# --- prompts ---

def ensure_prompt(store, text, category='word', language=None):
    row = store.query("SELECT * FROM prompts WHERE text=?", (text,))
    if row:
        return dict(row[0])
    cur = store.execute("INSERT INTO prompts (text, category, language) VALUES (?,?,?)",
                        (text, category, language))
    return dict(store.query("SELECT * FROM prompts WHERE id=?", (cur.lastrowid,))[0])


def list_prompts(store):
    return [dict(r) for r in store.query("SELECT * FROM prompts ORDER BY id")]


# --- takes & windows ---

def create_take(store, session_id, prompt_id):
    row = store.query("SELECT COALESCE(MAX(take_number),0)+1 AS n FROM takes WHERE session_id=? AND prompt_id=?",
                      (session_id, prompt_id))
    n = row[0]['n']
    cur = store.execute("INSERT INTO takes (session_id, prompt_id, take_number, started_at) VALUES (?,?,?,?)",
                        (session_id, prompt_id, n, time.time()))
    return cur.lastrowid


def finish_take(store, take_id, duration_s, verdict, verdict_reason):
    store.execute("UPDATE takes SET duration_s=?, verdict=?, verdict_reason=? WHERE id=?",
                  (duration_s, verdict, verdict_reason, take_id))


def mark_take(store, take_id, marked_bad):
    store.execute("UPDATE takes SET marked_bad=? WHERE id=?", (int(marked_bad), take_id))


def set_take_split(store, take_id, split):
    store.execute("UPDATE takes SET split=? WHERE id=?", (split, take_id))


def insert_window(store, take_id, seq, t_start_ms, samples, features, quality, packet_type, window_count):
    f = features + [0.0] * (13 - len(features))
    store.execute(
        "INSERT INTO windows (take_id, seq, t_start_ms, mic_blob, piezo_blob, pressure_blob, airflow_blob, "
        "f0,f1,f2,f3,f4,f5,f6,f7,f8,f9,f10,f11,f12, quality_score, quality_verdict, packet_type, window_count) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (take_id, seq, t_start_ms,
         _blob(samples.get('mic')), _blob(samples.get('piezo')),
         _blob(samples.get('pressure')), _blob(samples.get('airflow')),
         *f, quality['score'], quality['verdict'], packet_type, window_count))


def _blob(samples):
    if not samples:
        return None
    import struct
    return struct.pack(f'<{len(samples)}h', *samples)


def get_take(store, take_id):
    row = store.query("SELECT t.*, p.text AS prompt_text, s.participant_id, pcode.code AS participant_code "
                      "FROM takes t JOIN prompts p ON p.id=t.prompt_id "
                      "JOIN sessions s ON s.id=t.session_id "
                      "JOIN participants pcode ON pcode.id=s.participant_id WHERE t.id=?", (take_id,))
    return dict(row[0]) if row else None


def list_takes(store, session_id=None, limit=500):
    sql = ("SELECT t.*, p.text AS prompt_text, s.participant_id, pc.code AS participant_code "
           "FROM takes t JOIN prompts p ON p.id=t.prompt_id JOIN sessions s ON s.id=t.session_id "
           "JOIN participants pc ON pc.id=s.participant_id")
    params = []
    if session_id:
        sql += " WHERE t.session_id=?"
        params.append(session_id)
    sql += " ORDER BY t.id DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in store.query(sql, params)]


def take_windows(store, take_id):
    return [dict(r) for r in store.query("SELECT * FROM windows WHERE take_id=? ORDER BY seq", (take_id,))]


def unblob(blob):
    if blob is None:
        return []
    import struct
    return list(struct.unpack(f'<{len(blob)//2}h', blob))


# --- embeddings ---

def insert_embedding(store, take_id, window_id, extractor_name, extractor_version, vector, label,
                     participant_id, stage, quality_score, is_bad):
    import struct
    store.execute(
        "INSERT INTO embeddings (take_id, window_id, extractor_name, extractor_version, dim, vector, label, "
        "participant_id, stage, quality_score, is_bad, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (take_id, window_id, extractor_name, extractor_version, len(vector),
         struct.pack(f'<{len(vector)}f', *vector), label, participant_id, stage,
         quality_score, int(is_bad), time.time()))


def list_embeddings(store, extractor=None, stage=None):
    sql = ("SELECT e.id, e.take_id, e.window_id, e.extractor_name, e.extractor_version, e.dim, e.label, "
           "e.participant_id, e.stage, e.quality_score, e.is_bad, e.created_at FROM embeddings e")
    conds, params = [], []
    if extractor:
        conds.append("e.extractor_name=?")
        params.append(extractor)
    if stage is not None:
        conds.append("e.stage=?")
        params.append(stage)
    if conds:
        sql += " WHERE " + " AND ".join(conds)
    return [dict(r) for r in store.query(sql + " ORDER BY e.id", params)]


def embedding_vectors(store, extractor=None):
    """Return list of dicts with parsed float vectors for export."""
    import struct
    sql = ("SELECT e.id, e.take_id, e.window_id, e.extractor_name, e.extractor_version, e.dim, e.vector, "
           "e.label, e.participant_id, e.stage, e.quality_score, e.is_bad, t.split "
           "FROM embeddings e JOIN takes t ON t.id=e.take_id")
    params = []
    if extractor:
        sql += " WHERE e.extractor_name=?"
        params.append(extractor)
    out = []
    for r in store.query(sql + " ORDER BY e.id", params):
        d = dict(r)
        d['vector'] = list(struct.unpack(f'<{d["dim"]}f', d.pop('vector')))
        out.append(d)
    return out


def label_counts(store, extractor=None):
    sql = "SELECT label, COUNT(*) AS n FROM embeddings"
    params = []
    if extractor:
        sql += " WHERE extractor_name=?"
        params.append(extractor)
    sql += " GROUP BY label ORDER BY n DESC"
    return [dict(r) for r in store.query(sql, params)]


def unprocessed_takes(store, extractor_name, extractor_version):
    return [dict(r) for r in store.query(
        "SELECT t.* FROM takes t WHERE NOT EXISTS (SELECT 1 FROM embeddings e WHERE e.take_id=t.id "
        "AND e.extractor_name=? AND e.extractor_version=?)", (extractor_name, extractor_version))]
