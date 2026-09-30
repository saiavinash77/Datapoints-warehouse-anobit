#!/usr/bin/env python3
"""REST API: participants, sessions, prompts, recording, processing jobs, dataset tools, export."""

import csv
import hashlib
import io
import logging
import struct
import threading
import time
import wave
from pathlib import Path

import numpy as np
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from collector import store as st
from collector.features import EXTRACTOR_NAME, EXTRACTOR_VERSION, FeatureExtractor, extract_take_features

logger = logging.getLogger(__name__)

router = APIRouter(prefix='/api')
CTX = None  # set by app.py: {'store': Store, 'ingest': IngestService, 'recorder': Recorder, 'jobs': ProcessManager}


# --- schemas ---

class ParticipantIn(BaseModel):
    age: int | None = None
    gender: str | None = None
    language: str | None = None
    dialect: str | None = None
    notes: str | None = None
    consent: bool = False
    code: str | None = None


class SessionIn(BaseModel):
    participant_id: int
    stage: int = 1
    device_mode: str = 'handheld'
    mic_gain: float | None = None
    notes: str | None = None


class PromptIn(BaseModel):
    text: str
    category: str = 'word'
    language: str | None = None


class RecordStartIn(BaseModel):
    session_id: int
    prompt_id: int
    duration_s: float = 3.0


class MarkIn(BaseModel):
    marked_bad: bool


class ProcessStartIn(BaseModel):
    extractor: str = EXTRACTOR_NAME
    session_id: int | None = None
    include_bad: bool = False


class SplitIn(BaseModel):
    extractor: str = EXTRACTOR_NAME
    train: float = 0.8
    val: float = 0.1
    test: float = 0.1


# --- participants ---

@router.get('/health')
def health():
    return CTX['ingest'].device.snapshot()


@router.post('/participants')
def add_participant(p: ParticipantIn):
    if not p.consent:
        raise HTTPException(400, 'consent_required')
    row = st.create_participant(CTX['store'], age=p.age, gender=p.gender, language=p.language,
                                dialect=p.dialect, notes=p.notes, consent=True, code=p.code)
    return row


@router.get('/participants')
def get_participants():
    return st.list_participants(CTX['store'])


# --- sessions ---

@router.post('/sessions')
def add_session(s: SessionIn):
    return st.create_session(CTX['store'], s.participant_id, stage=s.stage,
                             device_mode=s.device_mode, mic_gain=s.mic_gain, notes=s.notes)


@router.get('/sessions')
def get_sessions():
    return st.list_sessions(CTX['store'])


@router.post('/sessions/{sid}/end')
def end_session(sid: int):
    st.end_session(CTX['store'], sid)
    return {'ok': True}


# --- prompts ---

@router.get('/prompts')
def get_prompts():
    return st.list_prompts(CTX['store'])


@router.post('/prompts')
def add_prompt(p: PromptIn):
    return st.ensure_prompt(CTX['store'], p.text, p.category, p.language)


# --- recording ---

@router.post('/record/start')
def record_start(r: RecordStartIn):
    try:
        take_id = CTX['recorder'].start_take(r.session_id, r.prompt_id, r.duration_s)
    except RuntimeError:
        raise HTTPException(409, 'already_recording')
    return {'take_id': take_id}


@router.post('/record/stop')
def record_stop():
    result = CTX['recorder'].stop_take()
    if result is None:
        return {'ok': False, 'note': 'not_recording_or_already_finished'}
    return {'ok': True, **result}


@router.get('/record/status')
def record_status():
    return {'recording': CTX['recorder'].is_recording}


# --- takes ---

@router.get('/takes')
def get_takes(session_id: int | None = None):
    return st.list_takes(CTX['store'], session_id)


@router.post('/takes/{take_id}/mark')
def mark_take(take_id: int, m: MarkIn):
    st.mark_take(CTX['store'], take_id, m.marked_bad)
    return {'ok': True}


@router.get('/takes/{take_id}/waveform')
def take_waveform(take_id: int):
    wins = st.take_windows(CTX['store'], take_id)
    if not wins:
        raise HTTPException(404, 'no_windows')
    mic = np.concatenate([np.asarray(st.unblob(w['mic_blob']), dtype=np.float32) for w in wins
                          if w['mic_blob'] is not None])
    step = max(1, len(mic) // 4000)
    ds = mic[::step]
    return {'take_id': take_id, 'n': len(mic), 'waveform': [round(float(x), 1) for x in ds]}


@router.get('/takes/{take_id}/audio.wav')
def take_audio(take_id: int):
    wins = st.take_windows(CTX['store'], take_id)
    mic_parts = [st.unblob(w['mic_blob']) for w in wins if w['mic_blob'] is not None]
    if not mic_parts:
        raise HTTPException(404, 'no_mic_data')
    samples = [s for part in mic_parts for s in part]
    buf = io.BytesIO()
    with wave.open(buf, 'wb') as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(16000)
        w.writeframes(struct.pack(f'<{len(samples)}h', *samples))
    return Response(content=buf.getvalue(), media_type='audio/wav',
                    headers={'Content-Disposition': f'attachment; filename=take_{take_id}.wav'})


# --- processing (embeddings) ---

class ProcessManager:
    def __init__(self, store):
        self.store = store
        self.lock = threading.Lock()
        self.status = {'running': False, 'job_id': 0, 'done': 0, 'total': 0,
                       'current': '', 'error': None, 'last_result': None}
        self._thread = None

    def start(self, extractor, session_id=None, include_bad=False):
        with self.lock:
            if self.status['running']:
                raise RuntimeError('job_running')
            self.status.update({'running': True, 'done': 0, 'total': 0, 'current': '',
                                'error': None, 'job_id': self.status['job_id'] + 1})
        self._thread = threading.Thread(target=self._run,
                                        args=(extractor, session_id, include_bad), daemon=True)
        self._thread.start()

    def _run(self, extractor, session_id, include_bad):
        try:
            if session_id:
                takes = st.list_takes(self.store, session_id, limit=100000)
            else:
                takes = st.list_takes(self.store, None, limit=100000)
            with self.lock:
                self.status['total'] = len(takes)
            for i, take in enumerate(reversed(takes)):  # oldest first
                with self.lock:
                    self.status['current'] = f"take {take['id']} ({i + 1}/{len(takes)})"
                self._process_take(take['id'], extractor, include_bad)
                with self.lock:
                    self.status['done'] = i + 1
            with self.lock:
                self.status['running'] = False
                self.status['current'] = 'done'
                self.status['last_result'] = f"{len(takes)} takes processed"
        except Exception as e:
            logger.exception("Process job failed")
            with self.lock:
                self.status['running'] = False
                self.status['error'] = str(e)

    def _process_take(self, take_id, extractor, include_bad):
        store = self.store
        take = st.get_take(store, take_id)
        if not take:
            return
        existing = store.query(
            "SELECT 1 FROM embeddings WHERE take_id=? AND extractor_name=? AND extractor_version=?",
            (take_id, extractor, EXTRACTOR_VERSION))
        if existing:
            return
        wins = st.take_windows(store, take_id)
        if not wins:
            return
        is_bad = bool(take['marked_bad']) or take['verdict'] == 'bad'
        if is_bad and not include_bad:
            return
        ext = FeatureExtractor() if extractor == EXTRACTOR_NAME else None
        if ext is None:
            raise HTTPException(400, f'unknown_extractor:{extractor}')
        for w in wins:
            samples = {'mic': st.unblob(w['mic_blob']), 'piezo': st.unblob(w['piezo_blob']),
                       'pressure': st.unblob(w['pressure_blob']), 'airflow': st.unblob(w['airflow_blob'])}
            vec = ext.extract(samples)
            st.insert_embedding(store, take_id, w['id'], extractor, EXTRACTOR_VERSION, vec,
                                take['prompt_text'], take['participant_id'], 1,
                                w['quality_score'], is_bad)
        pooled = extract_take_features(wins, st.unblob)
        st.insert_embedding(store, take_id, None, extractor, EXTRACTOR_VERSION, list(pooled),
                            take['prompt_text'], take['participant_id'], 1,
                            take['verdict'] and 1.0 if take['verdict'] == 'good' else 0.5, is_bad)


@router.post('/process/start')
def process_start(p: ProcessStartIn):
    try:
        CTX['jobs'].start(p.extractor, p.session_id, p.include_bad)
    except RuntimeError:
        raise HTTPException(409, 'job_already_running')
    return {'ok': True}


@router.get('/process/status')
def process_status():
    return CTX['jobs'].status


# --- dataset tools ---

@router.get('/dataset/counts')
def dataset_counts(extractor: str | None = None):
    counts = st.label_counts(CTX['store'], extractor)
    total = sum(c['n'] for c in counts)
    return {'total': total, 'labels': counts}


@router.post('/dataset/splits/auto')
def auto_splits(s: SplitIn):
    store = CTX['store']
    parts = st.list_participants(store)
    ratios = (s.train, s.val, s.test)
    total = sum(ratios)
    if total <= 0:
        raise HTTPException(400, 'bad_ratios')
    thresholds = (ratios[0] / total, (ratios[0] + ratios[1]) / total)
    counts = {'train': 0, 'val': 0, 'test': 0}
    for p in parts:
        h = int(hashlib.md5(p['code'].encode()).hexdigest(), 16) / (2 ** 128)
        split = 'train' if h < thresholds[0] else ('val' if h < thresholds[1] else 'test')
        counts[split] += 1
        takes = store.query("SELECT t.id FROM takes t JOIN sessions s ON s.id=t.session_id "
                            "WHERE s.participant_id=?", (p['id'],))
        for t in takes:
            st.set_take_split(store, t['id'], split)
    return {'ok': True, 'participants': counts}


@router.get('/dataset/splits/summary')
def splits_summary():
    rows = CTX['store'].query(
        "SELECT COALESCE(t.split,'none') AS split, COUNT(DISTINCT s.participant_id) AS participants, "
        "COUNT(*) AS takes FROM takes t JOIN sessions s ON s.id=t.session_id GROUP BY t.split")
    return [dict(r) for r in rows]


# --- export ---

@router.get('/export/csv')
def export_csv(extractor: str = EXTRACTOR_NAME):
    rows = st.embedding_vectors(CTX['store'], extractor)
    if not rows:
        raise HTTPException(404, 'no_embeddings')
    dim = rows[0]['dim']
    buf = io.StringIO()
    wr = csv.writer(buf)
    wr.writerow([f'f{i}' for i in range(dim)] + ['label', 'participant_id', 'stage', 'take_id',
                                                 'window_id', 'split', 'is_bad', 'quality_score'])
    for r in rows:
        wr.writerow([round(v, 6) for v in r['vector']] + [r['label'], r['participant_id'], r['stage'],
                                                          r['take_id'], r['window_id'], r['split'],
                                                          r['is_bad'], r['quality_score']])
    return Response(buf.getvalue(), media_type='text/csv',
                    headers={'Content-Disposition': f'attachment; filename=dataset_{extractor}.csv'})


@router.get('/export/npz')
def export_npz(extractor: str = EXTRACTOR_NAME):
    rows = st.embedding_vectors(CTX['store'], extractor)
    if not rows:
        raise HTTPException(404, 'no_embeddings')
    X = np.asarray([r['vector'] for r in rows], dtype=np.float32)
    labels = [r['label'] for r in rows]
    splits = [r['split'] or 'none' for r in rows]
    label_names = sorted(set(labels))
    y = np.asarray([label_names.index(l) for l in labels], dtype=np.int64)
    meta = [{'take_id': r['take_id'], 'window_id': r['window_id'], 'participant_id': r['participant_id'],
             'is_bad': r['is_bad']} for r in rows]
    import json
    bio = io.BytesIO()
    np.savez_compressed(bio, X=X, y=y, label_names=np.asarray(label_names), splits=np.asarray(splits),
                        meta=np.asarray(json.dumps(meta)))
    return Response(bio.getvalue(), media_type='application/octet-stream',
                    headers={'Content-Disposition': f'attachment; filename=dataset_{extractor}.npz'})


@router.get('/export/summary')
def export_summary():
    return st.list_embeddings(CTX['store'])
