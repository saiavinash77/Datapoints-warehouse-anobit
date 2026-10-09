#!/usr/bin/env python3
"""Storage layer for the dataset (participants, sessions, prompts, takes, windows, embeddings).

Dual backend: PostgreSQL (Supabase) when DATABASE_URL is set, SQLite otherwise.
Same table/column names in both; raw samples stored as BYTEA/BLOB int16 little-endian.
"""

import json
import logging
import os
import sqlite3
import struct
import threading
import time
from pathlib import Path

logger = logging.getLogger(__name__)

SCHEMA_SQLITE = Path(__file__).resolve().parent.parent / 'schema.sql'
SCHEMA_PG = Path(__file__).resolve().parent.parent / 'schema_postgres.sql'
DEFAULT_DB = Path(__file__).resolve().parent.parent / 'avc_collector.db'


class Store:
    def __init__(self, db_path=DEFAULT_DB):
        self.db_path = str(db_path)
        self.pg = bool(os.environ.get('DATABASE_URL'))
        self._local = threading.local()
        self.init_schema()

    def conn(self):
        c = getattr(self._local, 'conn', None)
        if c is None:
            if self.pg:
                import psycopg2
                import psycopg2.extras
                c = psycopg2.connect(os.environ['DATABASE_URL'])
                c.autocommit = False
                self._local.cursor_factory = psycopg2.extras.RealDictCursor
            else:
                c = sqlite3.connect(self.db_path, timeout=10)
                c.row_factory = sqlite3.Row
                c.execute('PRAGMA foreign_keys = ON')
            self._local.conn = c
        return c

    def _sql(self, sql: str) -> str:
        return sql.replace('?', '%s') if self.pg else sql

    def query(self, sql, params=()):
        if self.pg:
            with self.conn().cursor(cursor_factory=self._local.cursor_factory) as cur:
                cur.execute(self._sql(sql), params)
                return cur.fetchall()
        return self.conn().execute(sql, params).fetchall()

    def execute(self, sql, params=()):
        if self.pg:
            cur = self.conn().cursor()
            cur.execute(self._sql(sql), params)
            self.conn().commit()
            return cur
        cur = self.conn().execute(sql, params)
        self.conn().commit()
        return cur

    def insert_id(self, sql, params=()):
        if self.pg:
            cur = self.execute(sql + ' RETURNING id', params)
            return cur.fetchone()['id']
        return self.execute(sql, params).lastrowid

    def init_schema(self):
        schema_path = SCHEMA_PG if self.pg else SCHEMA_SQLITE
        schema = schema_path.read_text(encoding='utf-8')
        if self.pg:
            for stmt in [s.strip() for s in schema.split(';') if s.strip() and not s.strip().startswith('--')]:
                try:
                    self.execute(stmt)
                except Exception:
                    logger.exception("Schema statement failed (may already exist)")
        else:
            c = self.conn()
            c.executescript(schema)
            for col in ('operator', 'site_id'):
                try:
                    c.execute(f"ALTER TABLE sessions ADD COLUMN {col} TEXT")
                except sqlite3.OperationalError:
                    pass
            c.commit()
        self.seed_default_prompts()
        logger.info("Database ready (%s)", 'postgres/supabase' if self.pg else self.db_path)

    def seed_default_prompts(self):
        defaults = [
            ('hello', 'word', 'english'), ('yes', 'word', 'english'), ('no', 'word', 'english'),
            ('thank you', 'word', 'english'), ('help', 'word', 'english'), ('water', 'word', 'english'),
            ('food', 'word', 'english'), ('doctor', 'word', 'english'), ('pain', 'word', 'english'),
            ('namaskaram (నమస్కారం)', 'word', 'telugu'), ('avunu (అవును)', 'word', 'telugu'),
            ('kaadu (కాదు)', 'word', 'telugu'), ('dhanyavadalu (ధన్యవాదాలు)', 'word', 'telugu'),
            ('neellu (నీళ్లు)', 'word', 'telugu'), ('anna (అన్న)', 'word', 'telugu'),
            ('aa', 'vowel', 'neutral'), ('ee', 'vowel', 'neutral'), ('oo', 'vowel', 'neutral'),
            ('aaa (sustained)', 'vowel', 'neutral'), ('eee (sustained)', 'vowel', 'neutral'),
            ('ooo (sustained)', 'vowel', 'neutral'),
            ('ka', 'phoneme', 'neutral'), ('ga', 'phoneme', 'neutral'), ('cha', 'phoneme', 'neutral'),
            ('ja', 'phoneme', 'neutral'), ('ta', 'phoneme', 'neutral'), ('da', 'phoneme', 'neutral'),
            ('pa', 'phoneme', 'neutral'), ('ba', 'phoneme', 'neutral'), ('ma', 'phoneme', 'neutral'),
        ]
        if self.pg:
            self.execute(
                "INSERT INTO prompts (text, category, language) SELECT unnest(%s), unnest(%s), unnest(%s) "
                "ON CONFLICT (text) DO NOTHING",
                ([d[0] for d in defaults], [d[1] for d in defaults], [d[2] for d in defaults]))
        else:
            self.conn().executemany(
                "INSERT OR IGNORE INTO prompts (text, category, language) VALUES (?,?,?)", defaults)
            self.conn().commit()
        logger.info("Default prompts ensured (%d)", len(defaults))

    def backup_bytes(self) -> bytes:
        """Clean backup of the whole database as bytes (SQLite file or JSON dump)."""
        if not self.pg:
            import io
            buf = io.BytesIO()
            dest = sqlite3.connect(buf)
            self.conn().backup(dest)
            dest.close()
            return buf.getvalue()
        tables = ('participants', 'sessions', 'prompts', 'takes', 'windows', 'embeddings')
        dump = {}
        for t in tables:
            rows = self.query(f"SELECT * FROM {t}")
            dump[t] = []
            for r in rows:
                d = dict(r)
                for k, v in list(d.items()):
                    if isinstance(v, (bytes, memoryview)):
                        d[k] = {'blob_b64': __import__('base64').b64encode(bytes(v)).decode()}
                dump[t].append(d)
        return json.dumps(dump, ensure_ascii=False, default=str).encode()

    def backup_to(self, dest_path):
        dest = sqlite3.connect(dest_path)
        self.conn().backup(dest)
        dest.close()
        return dest_path


# --- participants ---

def next_participant_code(store) -> str:
    row = store.query("SELECT code FROM participants ORDER BY id DESC LIMIT 1")
    if not row:
        return 'P001'
    last = row[0]['code']
    return f'P{int(last[1:]) + 1:03d}'


def create_participant(store, age=None, gender=None, language=None, dialect=None, notes=None, consent=False, code=None):
    code = code or next_participant_code(store)
    pid = store.insert_id(
        "INSERT INTO participants (code, age, gender, language, dialect, notes, consent_given, created_at) "
        "VALUES (?,?,?,?,?,?,?,?)",
        (code, age, gender, language, dialect, notes, int(consent), time.time()))
    return get_participant(store, pid)


def get_participant(store, pid):
    row = store.query("SELECT * FROM participants WHERE id=?", (pid,))
    return dict(row[0]) if row else None


def list_participants(store):
    return [dict(r) for r in store.query("SELECT * FROM participants ORDER BY id")]


# --- sessions ---

def create_session(store, participant_id, stage=1, device_mode='handheld', mic_gain=None,
                   notes=None, operator=None, site_id=None):
    sid = store.insert_id(
        "INSERT INTO sessions (participant_id, stage, device_mode, mic_gain, sample_rates_json, "
        "started_at, notes, operator, site_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (participant_id, stage, device_mode, mic_gain,
         json.dumps({'mic': 16000, 'piezo': 8000, 'pressure': 100, 'airflow': 100}),
         time.time(), notes, operator, site_id))
    return get_session(store, sid)


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
    pid = store.insert_id("INSERT INTO prompts (text, category, language) VALUES (?,?,?)",
                          (text, category, language))
    return dict(store.query("SELECT * FROM prompts WHERE id=?", (pid,))[0])


def list_prompts(store):
    return [dict(r) for r in store.query("SELECT * FROM prompts ORDER BY id")]


# --- takes & windows ---

def create_take(store, session_id, prompt_id):
    row = store.query("SELECT COALESCE(MAX(take_number),0)+1 AS n FROM takes WHERE session_id=? AND prompt_id=?",
                      (session_id, prompt_id))
    n = row[0]['n']
    return store.insert_id("INSERT INTO takes (session_id, prompt_id, take_number, started_at) VALUES (?,?,?,?)",
                           (session_id, prompt_id, n, time.time()))


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
    return struct.pack(f'<{len(samples)}h', *samples)


def get_take(store, take_id):
    row = store.query("SELECT t.*, p.text AS prompt_text, s.participant_id, s.stage, pcode.code AS participant_code "
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
    data = bytes(blob)
    return list(struct.unpack(f'<{len(data)//2}h', data))


# --- embeddings ---

def insert_embedding(store, take_id, window_id, extractor_name, extractor_version, vector, label,
                     participant_id, stage, quality_score, is_bad):
    blob = struct.pack(f'<{len(vector)}f', *vector)
    if store.pg and len(vector) == 13:
        vec_literal = '[' + ','.join(str(float(v)) for v in vector) + ']'
        store.execute(
            "INSERT INTO embeddings (take_id, window_id, extractor_name, extractor_version, dim, vector, vec, "
            "label, participant_id, stage, quality_score, is_bad, created_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (take_id, window_id, extractor_name, extractor_version, len(vector), blob, vec_literal,
             label, participant_id, stage, quality_score, int(is_bad), time.time()))
        return
    store.execute(
        "INSERT INTO embeddings (take_id, window_id, extractor_name, extractor_version, dim, vector, label, "
        "participant_id, stage, quality_score, is_bad, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        (take_id, window_id, extractor_name, extractor_version, len(vector), blob, label,
         participant_id, stage, quality_score, int(is_bad), time.time()))


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
        d['vector'] = list(struct.unpack(f'<{d["dim"]}f', bytes(d.pop('vector'))))
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
